#!/usr/bin/env python

from __future__ import print_function
import subprocess
import os
import shutil
import stat
import argparse
import sys
import re
import logging 
import pdb
import uuid
import hashlib
import datetime
import time
from pdb import set_trace as strace

# TODO: PEP8-ify this, I do a lot of things in a non-standard way apparently


## Changeable stuff
#mainline_kernel_url = 'https://www.kernel.org/pub/linux/kernel/v3.x/linux-3.18.11.tar.xz'
mainline_kernel_url = 'https://www.kernel.org/pub/linux/kernel/v4.x/linux-4.0.tar.xz'
mainline_kernel_filename = re.match('.*/(.*)', mainline_kernel_url).group(1)

## End changeable stuff

# TODO: would be better to have printed output & logging configured in the same place
# TODO: if we need to capture the output of *one* chroot command, this doesn't work because of set -v
def sh(commandline, env=None, printoutput=True, chroot=None, cwd=None):
    if type(commandline) is str:
        commandline = [commandline]

    if chroot:
        if not (os.path.isdir(chroot)):
            raise Exception("chroot dir '{}' does not exist".format(chroot))
        nowstamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
        cs_name = "chroot_{}.sh".format(nowstamp)
        cs_path = "{}/root/{}".format(chroot, cs_name)
        cs_prefix = [
            '#!/bin/bash',
            'set -e',         # fail immediately on error
            ". /etc/profile", # otherwise we get complaints the $PATH isn't set
            'set -v',         # write each line to output before executing it
            #"mount -t proc proc /proc", # this should have already been done tbh
            ]
        cs_contents = cs_prefix + commandline
        write_file(cs_contents, cs_path, append=False, mode=0o700)
        outercmd = ["chroot {} /root/{}".format(chroot, cs_name)]
    else:
        outercmd = commandline

    outputs = []
    for cli in outercmd:
        print("==== Running command: {}".format(cli))
        pristine_output = subprocess.check_output(cli, shell=True, env=env)
        output = pristine_output.strip('\r\n') # Trims newlines @ beginning/end only
        if printoutput and len(output) > 0:
            print(output)
            print("==== Finished")
    return output

def write_file(contents, filename, append=True, mode=False, uniqueonly=False):
    if type(contents) is str:
        contents = [contents]

    # Useful b/c it lets us add lines to e.g. sources.list, but only if they aren't already in there
    if uniqueonly:
        new_contents = []
        with open(filename) as f:
            contents_curr = f.readlines()
        for newline in contents:
            found = False
            for oldline in contents_curr:
                if newline == oldline:
                    found = True
                    break
            if not found:
                new_contents += [newline]
        contents = new_contents

    openmode = 'a' if append else 'w'
    with open(filename, openmode) as f:
        print('\n'.join(contents), file=f)
    if mode:
        os.chmod(filename, mode)

def makedirs(path, mode=0o755, exist_ok=False):
    """Replacement for os.makedirs() w/ the Py3 feature of exists_ok"""
    if os.path.exists(path) and exist_ok:
        return
    os.makedirs(path, mode)

def mount(device, mountpoint, fstype=None, fsoptions=None):
    """
    Use /bin/mount to mount a device on a mountpoint
    Create the mountpoint if necessary
    If the device is already mounted there, do nothing
    If something else is already mounted there, raise an exception
    """
    makedirs(mountpoint, mode=0o755, exist_ok=True)

    fs_type = "-t {}".format(fstype) if fstype else ""
    fs_options = "-o {}".format(fsoptions) if fsoptions else ""

    ismounted = False
    for mount in sh('mount', printoutput=False).split('\n'):
        exdevice, exmountpoint = re.search('(.+) on (.+) type ', mount).group(1, 2)
        if exmountpoint == mountpoint:
            logging.info("Attempted to mount '{}' on '{}' but it is already mounted there".format(
                device, mountpoint))
            ismounted = True
    if not ismounted:
        sh('mount {} {} "{}" "{}"'.format(fs_type, fs_options, device, mountpoint))

def umount(mountpoint):
    """Use /bin/umount to unmount a device"""
    for mount in sh('mount', printoutput=False).split('\n'):
        exdevice, exmountpoint = re.search('(.+) on (.+) type ', mount).group(1, 2)
        if exmountpoint == mountpoint:
            sh('umount "{}"'.format(mountpoint))

def check_loopdev(image):
    """Use /sbin/losetup to check if an image has been attached as a loopback device"""
    retval = []
    if os.path.exists(image):
        pristine_output = sh('losetup --associated "{}"'.format(image))
        if pristine_output:
            for line in pristine_output.split('\n'):
                loopdev = re.search('(.+?):.*', line).group(1)
                retval += [loopdev]
                logging.info("Found existing loopback device for image '{}' at '{}'".format(image, loopdev))
        else:
            logging.info("No existing loopback device found for image '{}'".format(image))
    else:
        logging.info("No image at path '{}'".format(image))
    return retval    

# This isn't ideal. You'd like to store the finalstatus as part of the OnionPiImage instance itself, but I can't figure out how to do that, because I need to reference it from within statusmethod()
'''When an OnionPiImage reachest this status, it is finished'''
finalstatus = 0
statusmethods = []
def statusmethod(func):
    global finalstatus
    global statusmethods
    finalstatus += 1
    func.status = finalstatus
    def wrapper(class_instance, *args, **kwargs):
        func(class_instance, *args, **kwargs)
        class_instance.status = func.status
    wrapper.status = func.status
    wrapper.__orig_name__ = func.__name__
    statusmethods += [wrapper]
    return wrapper

# TODO: have every method require a version level and then DTRT if the image is not at that version level
class OnionPiImage(object):

    # Kali uses 3000MB
    # My debian minimal w/ sjoerd was just BARELY under 1024MB total
    min_size = 1536

    def __init__(self, imagepath,
                 mountpoint=None, imagesize=None, workdir=None, 
                 debianversion="sid", hostname="onionpi", 
                 kernel_method="sjoerd"):
        '''
        __init__ should *not* make any modifications to actual files on disk! We rely on this in various places, so if it changes, stuff might break and **data could be deleted**.
        '''

        self.imagepath = imagepath
        self.imagedir, self.imagename = re.search('(.*)\/(.*)$', imagepath).group(1, 2)

        if os.path.isfile(self.imagepath):
            self.imagesize = os.stat(self.imagepath).st_size / 1024 / 1024
        else:
            self.imagesize = imagesize if imagesize else OnionPiImage.min_size
        logging.info("Using a size of {}MB".format(self.imagesize))
        if self.imagesize < OnionPiImage.min_size:
            raise Exception("imagesize {}MB smaller than minimum size of {}MB".format(self.imagesize, OnionPiImage.min_size))

        self.kernel_method = kernel_method

        if not mountpoint:
            mountpoint = '/mnt/'+self.imagename
        if not workdir:
            workdir = '/tmp/'+self.imagename
        self.mountpoint = mountpoint
        self.workdir = workdir
        self.debianversion = debianversion
        self.hostname = hostname

        self.arch = "armhf"
        self.debmirr = "ftp://ftp.debian.org/debian"

        logging.info(self)

        self.rootdev = None
        self.bootdev = None


    @property
    def mounts(self):
        # NOTE: These are mounted in order, then unmounted in reverse order. 
        return [
            { 'device': self.rootdev, 'mountpoint': self.mountpoint,                  'fstype': None,   'fsoptions': None },
            { 'device': self.bootdev, 'mountpoint': self.mountpoint+"/boot/firmware", 'fstype': None,   'fsoptions': None },
            { 'device': 'proc',       'mountpoint': self.mountpoint+'/proc',          'fstype': 'proc', 'fsoptions': None },
            { 'device': '/dev/',      'mountpoint': self.mountpoint+'/dev',           'fstype': None,   'fsoptions': 'bind' },
            { 'device': '/dev/pts',   'mountpoint': self.mountpoint+'/dev/pts',       'fstype': None,   'fsoptions': 'bind' }]

    @property
    def statfile(self):
        """The location of the status file"""
        return "{}.status.txt".format(self.imagepath)

    @property
    def status(self):
        """The current status of the image, as read from the status file"""
        if not os.path.exists(self.imagepath):
            return 0
        
        if not os.path.exists(self.statfile):
            raise Exception("Image exists but statfile does not")

        with open(self.statfile) as f:
            line1 = f.readline()

        try:
            statint = int(line1)
        except:
            raise Exception("statfile is invalid format")

        global finalstatus
        if 0 < statint <= finalstatus:
            return statint
        else:
            raise Exception('Invalid statfile')

    @status.setter
    def status(self, status):
        write_file([str(status)], self.statfile, append=False)
        logging.info('Setting status to {}'.format(status))
        return status

    def __str__(self):
        selfstring = '\n'.join([
            "OnionPiImage(",
            "  "+self.imagepath,
            "  status: {} (from {})".format(self.status, self.statfile),
            "  size: {}MB".format(self.imagesize),
            "  mountpoint: "+self.mountpoint,
            "  debian version: "+self.debianversion,
            "  hostname: "+self.hostname,
            "  architecture: "+self.arch,
            "  debian mirror: "+self.debmirr,
            "  hostname: {})".format(self.hostname)])
        return selfstring

    def check_partitions(self):
        dump = sh('sfdisk -d {}'.format(self.imagepath)).split('\n')
        # Check that the first partition is type FAT32 and second is type Linux
        p1good = False
        p2good = False
        for line in dump:
            if re.match('^{}1.*Id= c$'.format(imagepath), line):
                p1good = True
            if re.match('^{}2.*Id=83$'.format(imagepath), line):
                p2good = True
        return p1good and p2good

    @statusmethod
    def create_image(self):
        makedirs(self.imagedir, mode=0o755, exist_ok=True)
        sh('dd if=/dev/zero of="{}" bs=1M count="{}"'.format(
            self.imagepath, self.imagesize))

    @statusmethod
    def partition_image(self):
        # Partition it to have a FAT partition and an ext4 one
        # This could be automated with sfdisk at some point
        # NOTE: Results in an error: "Warning: The resulting partition is not 
        # properly aligned for best performance"
        # No idea if this matters on an SD card? I think it's fixable with 
        # sfdisk, but that's harder to understand. It's also fixable if you just
        # use fdisk manually. 
        sh('parted "{}" --script -- mklabel msdos'.format(self.imagepath))
        sh('parted "{}" --script -- mkpart primary fat32 0 64'.format(
            self.imagepath))
        sh('parted "{}" --script -- mkpart primary ext4 64 -1'.format(
            self.imagepath))

    # TODO: Make sure the status of this gets checked and run automatically
    # This is HOST status, not IMAGE status, so I can't track it with self.status
    def setup_loopback_partitions(self):
        # Retrieve mapper name, e.g. loop0
        # This is usually found as /dev/loop0
        # After the kpartx command has run, the partitions will be attached as
        # e.g. /dev/mapper/loop0p1 and /dev/mapper/loop0p2

        self.setup_loopback()

        # If the partitions are already attached, kpartx will return them; 
        # otherwise, it will attach and return them
        # Its output looks like: 
        # add map loop2p1 (254:2): 0 125000 linear /dev/loop2 1
        # add map loop2p2 (254:3): 0 8263607 linear /dev/loop2 125001
        kpartx_output = sh("kpartx -lva \"{}\"".format(self.loopdev_path))

        re_result = re.search('^add map (\w+)p. ', kpartx_output)
        loopmap = re_result.group(1)

        self.bootdev = "/dev/mapper/{}p1".format(loopmap)
        self.rootdev = "/dev/mapper/{}p2".format(loopmap)
        logging.info("Boot device: '{}'; Root device: '{}'".format(self.bootdev, self.rootdev))

    def setup_loopback(self):
        # First try to find an already-attached loopback device for the image
        loopdevs = check_loopdev(self.imagepath)
        if len(loopdevs) > 0:
            if len(loopdevs) > 1:
                logging.warn("Multiple loop devices found: {}".format(self.imagepath, loopdevs))
            self.loopdev_path = loopdevs[0]
            logging.info("Using existing loopback device at '{}'".format(self.loopdev_path))
        else:
            # If that didn't work, find a new loopback device to use, and attach the image
            self.loopdev_path = sh('losetup --find "{}" --show'.format(self.imagepath))
            #time.sleep(6) # Sometimes kpartx won't see these partitions right away
            logging.info("Connected image to new loopback device at '{}'".format(self.loopdev_path))

    @statusmethod
    def create_image_filesystems(self):
        self.setup_loopback_partitions()
        time.sleep(6) # Sometimes kpartx won't see these partitions right away
        sh('mkfs.vfat {}'.format(self.bootdev))
        sh('mkfs.ext4 {}'.format(self.rootdev))

    def mount_chroot(self):
        self.setup_loopback_partitions()
        for m in self.mounts:
            mount(device=m['device'], mountpoint=m['mountpoint'], 
                fstype=m['fstype'], fsoptions=m['fsoptions'])

    @statusmethod
    def debootstrap_stage1(self):
        self.mount_chroot()
        sh('qemu-debootstrap --verbose --arch={} "{}" "{}" "{}"'.format(
            self.arch, self.debianversion, self.mountpoint, self.debmirr))

    @statusmethod
    def debootstrap_stage3(self):
        self.mount_chroot()

        sl_new = [
            'deb http://ftp.debian.org/debian {} main contrib non-free'.format(self.debianversion),
            'deb-src http://ftp.debian.org/debian {} main contrib non-free'.format(self.debianversion)]
        write_file(sl_new, self.mountpoint+"/etc/apt/sources.list", append=True, mode=0o644)

        write_file([self.hostname], self.mountpoint+"/etc/hostname", append=False, mode=0o644)

        # Kali does this "so X doesn't complain": 
        hosts_contents = [
            "127.0.0.1       {}    localhost".format(self.hostname),
            "::1             localhost ip6-localhost ip6-loopback",
            "fe00::0         ip6-localnet",
            "ff00::0         ip6-mcastprefix",
            "ff02::1         ip6-allnodes",
            "ff02::2         ip6-allrouters"]
        write_file(hosts_contents, self.mountpoint+"/etc/hosts", append=False, mode=0o644)
        
        interfaces_contents = [
            "auto lo",
            "iface lo inet loopback",
            "auto eth0",
            "iface eth0 inet dhcp"]
        write_file(interfaces_contents, self.mountpoint+"/etc/network/interfaces", append=False, mode=0o644)

        # TODO: do something better here, or at least allow a customization that doesn't rely on Google
        write_file(["nameserver 8.8.8.8"], self.mountpoint+"/etc/resolv.conf", append=False, mode=0o644)

        # TODO: what do these lines really do though. (from Kali)
        debconfset_contents = [
            'console-common console-data/keymap/policy   select      Select keymap from full list',
            'console-common console-data/keymap/full     select      en-latin1-nodeadkeys',
            'locales locales/default_environment_locale  multiselect en_US.UTF-8 UTF-8',
            'locales locales/default_environment_locale  select      en_US.UTF-8']
        
        write_file(debconfset_contents, self.mountpoint+"/debconf.set")

        thirdstage_env = {
            'LANG':'C', 
            'DEBIAN_FRONTEND':'noninteractive'
        }

        additional_packages = 'apt-transport-https aptitude file openssh-client openssh-server'

        stage3cmd = [
            'dpkg-divert --add --local --divert /usr/sbin/invoke-rc.d.chroot --rename /usr/sbin/invoke-rc.d',
            'cp /bin/true /usr/sbin/invoke-rc.d',
            #'echo -e "#!/bin/sh\nexit 101" > /usr/sbin/policy-rc.d',
            'echo -e "exit 101" > /usr/sbin/policy-rc.d',
            'chmod +x /usr/sbin/policy-rc.d',
            'apt-get update',
            'apt-get install locales locales-all debconf-utils',
            'debconf-set-selections /debconf.set',
            'rm -f /debconf.set',
            'apt-get update',
            # uboot is broken in jessie rn?? sjoerd doesn't require it. the webpage says:
            # "ideally, having the firmware load a bootloader (such as u-boot) rather than a kernel directly to allow for a much more flexible boot sequence and support for using an initramfs"
            # ... for non-sjoerd images, might have to build our own uboot. ugh.
            # ... oh, looks like this migrated to u-boot-tools: https://packages.debian.org/wheezy/uboot-mkimage
            # However, still not sure that it supports the Pi2 yet - it does support Pi1, but 2 support is unclear
            #'apt-get -y install git-core binutils ca-certificates initramfs-tools uboot-mkimage',
            'apt-get -y install git-core binutils ca-certificates initramfs-tools',
            'apt-get -y install console-common less nano git',
            'echo "root:toor" | chpasswd',
            '''sed -i -e 's/KERNEL\!=\"eth\*|/KERNEL\!=\"/' /lib/udev/rules.d/75-persistent-net-generator.rules''',
            'rm -f /etc/udev/rules.d/70-persistent-net.rules',
            'apt-get --yes --force-yes install $packages',
            # (most of) my customizations are here:
            'apt-get install -y {}'.format(additional_packages),
            'locale-gen',
            'dpkg-reconfigure locales',
            'update-rc.d ssh enable',
            # End my shit
            # Cleanup: 
            'rm -f /etc/ssh_host_*_key.pub',
            'rm -f /usr/sbin/policy-rc.d',
            'rm -f /usr/sbin/invoke-rc.d',
            'dpkg-divert --remove --rename /usr/sbin/invoke-rc.d',
            'rm -rf /root/.bash_history',
            #'apt-get update',
            'apt-get clean',
            'rm -f /0',
            'rm -f /hs_err*',
            # 'rm -f /usr/bin/qemu*', # Keep this around if you want to keep chrooting in there
            ]
        sh(stage3cmd, env=thirdstage_env, chroot=self.mountpoint)

        write_file(["T0:23:respawn:/sbin/agetty -L ttyAMA0 115200 vt100"], self.mountpoint+"/etc/inittab", append=True)

    def test_chroot(self):
        # Just useful for testing that my chroot still works
        #sh("chroot {} /bin/ls /dev".format(self.mountpoint))
        return

    @statusmethod
    def install_kernel(self):
        if self.kernel_method == 'sjoerd':
            self.add_sjoerd_kernel()
        elif self.kernel_method == 'mainline':
            self.compile_kernel(kernel='mainline')
        elif self.kernel_method == 'rpi':
            self.compile_kernel(kernel='rpi')
        else:
            raise Exception('No such kernel method "{}"'.format(self.kernel_method))

    def compile_linux_kernel(self, clean=False, kernel='mainline'):
        """Compile a kernel from rpi sources. (Currently broken.)"""
        self.mount_chroot()
        makedirs(self.workdir, exist_ok=True)

        # TODO: <clean> doesn't work yet lol
        # if clean and os.path.exists(kdir):
        #     shutil.rmtree(kdir)
        # else:
        if kernel == 'mainline':
            global mainline_kernel_filename
            kdirname = re.match('(.*)\.tar\.xz', mainline_kernel_filename).group(1)
            kdir = "{}/{}".format(self.workdir, kdirname)
            if not os.path.isdir(kdir):
                sh("tar xf '{}'".format(mainline_kernel_filename), cwd=self.workdir)
            else:
                sh('make clean', cwd=kdir)
        elif kernel == 'rpi':
            kdir = '{}/raspberrypi-linux'
            if not os.path.isdir(kdir+'/.git'):
                sh('git clone --depth 1 https://github.com/raspberrypi/linux -b rpi-3.18.y "{}"'.format(kdir))
            else:
                sh('git pull', cwd=kdir)
                sh('make clean', cwd=kdir)
        else:
            raise Exception("I don't know how to make a kernel of type '{}'".format(kernel))
        
        # Compile the kernel
        procs = 0
        with open('/proc/cpuinfo') as f:
            for line in f.readlines():
                if re.match('processor', line):
                    procs +=1
        procs = procs * 1.5
        makeenv = {'ARCH': 'arm', 'CROSS_COMPILE': 'arm-linux-gnueabihf-'}
        sh('make bcm2709_defconfig', env=makeenv, cwd=kdir)
        sh('make -j {}'.format(procs), env=makeenv, cwd=kdir)
        sh('make modules_install INSTALL_MOD_PATH="{}"'.format(self.mountpoint), env=makeenv, cwd=kdir)
        shutil.copyfile(kdir+'/arch/arm/boot/zImage', self.mountpoint+'/boot/firmware/kernel7.img')
        shutil.copyfile(kdir+'arch/arm/boot/dts/bcm2709-rpi-2-b.dtb', self.mountpoint+'/boot/firmware/')

        # Kali does this: is it necessary? Replaces the installed firmware w/ the mainline Linux firmware
        # rm -rf ${basedir}/root/lib/firmware
        # cd ${basedir}/root/lib
        # git clone --depth 1 https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git firmware
        # rm -rf ${basedir}/root/lib/firmware/.git

        # Now get the raspberry pi firmware
        makedirs(self.workdir, exist_ok=True)
        fwdir = "{}/firmware".format(self.workdir)

        if clean and os.path.exists(fwdir):
            shutil.rmtree(fwdir)
            sh('git clone --depth 1 https://github.com/raspberrypi/firmware.git "{}"'.format(fwdir))

        sh('cp -rf "{}"/boot/* "{}"'.format(fwdir, self.mountpoint+"/boot/firmware"))

        # Create cmdline.txt file. Uses the Kali one as a starting point
        cmdline_contents = 'dwc_otg.fiq_fix_enable=1 console=ttyAMA0,115200 console=tty1 root=/dev/mmcblk0p2 rootfstype=ext4 rootwait ro rootflags=noload'
        write_file(cmdline_contents, "{}/boot/firmware/cmdline.txt".format(self.mountpoint))

        # Create a config.txt just to avoid wasting memory on the graphics chip we won't be using
        configtxt_contents = 'gpu_mem=16'
        write_file(configtxt_contents, "{}/boot/firmware/config.txt".format(self.mountpoint))
        

    # TODO: this doesn't require u-boot, meaning that the kernel step and the stage3 step are more segregated than my infrastructure currently allows. 
    # WHICH IS GOOD because apparently uboot is broken in jessie rn? Although the Pi 2 is supported in mainline UBoot so I could just build it myself
    # see stage3 comments
    def add_sjoerd_kernel(self):
        sl_new = [
            'deb https://repositories.collabora.co.uk/debian/ jessie rpi2',
            'deb http://ftp.debian.org/debian experimental main']
        write_file(sl_new, self.mountpoint+"/etc/apt/sources.list", append=True, mode=0o644)

        sjoerd_env = {
            'LANG':'C', 
            'DEBIAN_FRONTEND':'noninteractive'
        }
        sjcommand = [
            # Prevent daemons from starting in the chroot:
            'dpkg-divert --add --local --divert /usr/sbin/invoke-rc.d.chroot --rename /usr/sbin/invoke-rc.d',
            'cp /bin/true /usr/sbin/invoke-rc.d',
            'echo -e "exit 101" > /usr/sbin/policy-rc.d',
            'chmod +x /usr/sbin/policy-rc.d',
            # Do the work you need to do: 
            'apt-get update',
            # NOTE: package can't be authenticated. would be better to import the key out of band first, but until then, --force-yes:
            'apt-get install -y --force-yes collabora-obs-archive-keyring',
            'apt-get update',
            # NOTE: the linux-kbuild-3.18 package isn't included in jessie, but sjoerd's kernel images were build to depend on it:
            'apt-get -t experimental install linux-kbuild-3.18',
            # NOTE: sjoerd's page says the package is raspberrypi-firmware-nokernel. Nope. It's apparently raspberrypi-bootloader-nokernel.
            # NOTE: for some reason even after installing the keyring it can't auth at least some of these packages. I have to use --force-yes again. 
            'apt-get install -y --force-yes raspberrypi-bootloader-nokernel linux-image-3.18.0-trunk-rpi2 linux-headers-3.18.0-trunk-rpi2',
            # Re-enable daemons in the chroot:
            'rm -f /usr/sbin/policy-rc.d',
            'rm -f /usr/sbin/invoke-rc.d',
            'dpkg-divert --remove --rename /usr/sbin/invoke-rc.d']
        sh(sjcommand, env=sjoerd_env, chroot=self.mountpoint)

        # Copy the kernel & supporting files to the place that the Pi expects
        kpath = sh('chroot {} dpkg-query -L linux-image-3.18.0-trunk-rpi2 | grep vmlinuz'.format(self.mountpoint), env=sjoerd_env)
        shutil.copyfile(self.mountpoint+kpath, self.mountpoint+'/boot/firmware/kernel7.img')

        # create a cmdline.txt file. Uses the Raspbian one as a starting point
        cmdline_contents = "dwc_otg.lpm_enable=0 console=ttyAMA0,115200 root=/dev/mmcblk0p2 rootfstype=ext4 elevator=deadline rootwait"
        write_file(cmdline_contents, "{}/boot/firmware/cmdline.txt".format(self.mountpoint))

        fstab_contents = [
            'proc            /proc           proc    defaults          0       0',
            '/dev/mmcblk0p1  /boot           vfat    defaults          0       2',
            '/dev/mmcblk0p2  /               ext4    defaults,noatime  0       1']
        write_file(fstab_contents, "{}/etc/fstab".format(self.mountpoint), mode=0o644)

    def detach_image(self):
        for m in reversed(self.mounts):
            umount(m['mountpoint'])

        # Note: will detach ALL loopback devices for the image
        loopdev = check_loopdev(self.imagepath)
        for ld in loopdev:
            logging.info("Removing loopback device '{}'".format(ld))
            sh('kpartx -dv {}'.format(ld))
            sh("losetup --detach '{}'".format(ld))

    def purge_files(self):
        os.remove(self.imagepath)
        os.remove(self.statfile)
        # TODO: should also remove working dirs and shit

    @statusmethod
    def generate_checksum(self):
        self.detach_image()
        with open(self.imagepath, 'rb') as img:
            self.sha1sum = hashlib.sha1(img.read()).hexdigest() 
        logging.info("SHA1 for image at '{}' is '{}'".format(self.imagepath, self.sha1sum))
        write_file(self.sha1sum, "{}.sha1".format(self.imagepath), append=False)

    def go(self, statuslevel=None, overwrite=False, whatif=False):
        global finalstatus, statusmethods

        print("Image {} starting at status {}".format(self.imagename, self.status))

        if overwrite:
            logging.info("Removing existing image/stat file...")
            if not whatif:
                self.detach_image()
                if os.path.exists(self.imagepath): os.remove(self.imagepath)
                if os.path.exists(self.statfile): os.remove(self.statfile)

        finish = finalstatus if finalstatus > statuslevel else statuslevel
        for stat in range(self.status, finish):
            func = statusmethods[stat]
            logging.info("Running function #{} - '{}' - {}".format(stat, func.__orig_name__, func.__doc__))
            if not whatif:
                # Basically when you call instance.method(), python calls Class.method(instance). These do the same thing:
                #   imgobj = OnionPiImage(); imgobj.memberfunc(arg1, argN);
                #   imgobj = OnionPiImage(); f=imgobj.memberfunc; f(imgobj, arg1, argN);
                # (This is why all class methods must have at least one argument, usually called 'self')
                func(self)

        print("Image complete: {}".format(self))

def install_prereqs():
    # Note: We assume a completely bare debian install, so that this is enough to get a bare netinstall system up and running
    raise Exception("You need to add the emdebian mirror for what you wanna use ugh. don't forget to get the apt keys. then install sudo apt-get install emdebian-archive-keyring")

    # emdebian info, including repo URLs: http://www.emdebian.org/crosstools.html
    emdebian_host_packages = ""
    host_packages = "dosfstools parted kpartx debootstrap qemu-user-static binfmt-support python ntp gcc-arm-linux-gnueabi bc"
    logging.info("Installing host packages: {}".format(host_packages))
    sh("apt-get install -y {}".format(host_packages))

# I only need to do this because I wanted to examine the packages more closely
# Most people shouldn't need this
def download_collabora_repo(depsdir):
    url = 'https://repositories.collabora.co.uk/debian/'
    colldir = depsdir+"/collabora-repository"
    makedirs(colldir, exist_ok=True)
    sh(
        'wget --base="{0}" -nH --convert-links --mirror --page-requisites --no-parent "{0}"'.format(url),
        cwd=colldir)
        

def main(*args):
    argparser = argparse.ArgumentParser(
        description='Build an image for the Raspberry Pi 2')
    subparsers = argparser.add_subparsers(dest='subparser')

    argparser.add_argument('--verbose', '-v', action='count', 
        help='Print verbose messages (-vv for debug messages)')

    setupp = subparsers.add_parser('setup')
    setupp.add_argument('--install-prereqs', '-p', action='store_true', dest='prereqs',
        help='Install required packages on the chroot host')
    # TODO: The default here is specific to my system; fix that
    setupp.add_argument('--dependency-directory', action='store', 
        dest='depsdir', default='/chroots/dependencies',
        help='The directory to store downloaded dependencies')
    setupp.add_argument('--download-collabora', action='store_true', dest='collabora',
        help='Download all packages from the sjoerd/collabora repository so they can be examined')

    infop = subparsers.add_parser('info')
    infop.add_argument('--image-path', '-i', action='store', dest='imagepath',
        help='The path to the image file')
    infop.add_argument('--levels', '-l', action='store_true',
        help='Show the different status levels possible for an image')

    detachp = subparsers.add_parser('detach')
    detachp.add_argument('imagepath', action='store', 
        help='The path to the image file')
    detachp.add_argument('--purge', '-p', action='store_true', 
        help='Delete the image file, status file, and any working directories')

    # TODO: Add a way to tell it to bring it to a specific status level
    imagep = subparsers.add_parser('image')
    imagep.add_argument('imagepath', action='store', 
        help='The path to the image file')
    imagep.add_argument('--mountpoint', '-m', action='store', 
        help='The path to use as a mountpoint. Default is to use a directory in /mnt based on the image filename')
    imagep.add_argument('--imagesize', '-s', action='store', type=int, default=OnionPiImage.min_size,
        help='The size of the image in megabytes')
    imagep.add_argument('--debianversion', '-d', action='store', default='sid',
        help='The version of debian to use')
    imagep.add_argument('--overwrite', '-o', action='store_true',
        help='Delete any existing image file')
    imagep.add_argument('--whatif', '-n', action='store_true',
        help='Take no action, but print what actions would be taken')
    imagep.add_argument('--workdir', '-w', action='store',
        help='Temporary directory for compiling etc. Default is to use a directory in /tmp based on the image filename')
    imagep.add_argument('--kernel', '-k', action='store', 
        choices=['sjoerd','mainline','rpi'], default='sjoerd',
        help='How to get the kernel? Can use precompiled binaries from sjoerd (working), compile the mainline kernel (currently broken), or compile the kernel w/ patches from the Raspberry Pi Foundation (unimplemented).')

    parsed = argparser.parse_args()

    if parsed.verbose == 1:
        logging.basicConfig(format='%(levelname)s: %(message)s',level=logging.INFO)
    elif parsed.verbose > 1:
        logging.basicConfig(format='%(levelname)s: %(message)s',level=logging.DEBUG)
    logging.info("Informational messages on")
    logging.debug("Debug messages on")
    logging.debug("Arguments passed on cli: {}".format(args))
    logging.debug("Arguments after parsing: {}".format(parsed))

    if parsed.subparser == 'setup':
        depsdir = parsed.depsdir
        if parsed.prereqss: install_prereqs()
        if parsed.collabora: download_collabora_repo(depsdir)

    elif parsed.subparser == 'image':
        image = OnionPiImage(
            imagepath = parsed.imagepath, 
            mountpoint = parsed.mountpoint, 
            imagesize = parsed.imagesize, 
            debianversion = parsed.debianversion, 
            kernel_method = parsed.kernel,
            workdir=parsed.workdir)
        image.go(
            overwrite=parsed.overwrite, 
            whatif=parsed.whatif)

    elif parsed.subparser == 'info':
        if parsed.imagepath:
            image = OnionPiImage(imagepath=parsed.imagepath)
            print(image)
        if parsed.levels:
            print('Available images:')
            #fakeimage = OnionPiImage('/nonexistent')
            for k in OnionPiImage.statusmethods.keys():
                print('{}: {}'.format(k, OnionPiImage.statusmethods[k].__name__))

    elif parsed.subparser == 'detach':
        image = OnionPiImage(imagepath=parsed.imagepath)
        image.detach_image()
        if parsed.purge:
            image.purge_files()

if __name__ == '__main__':
    sys.exit(main(*sys.argv))
