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

# TODO: would be better to have printed output & logging configured in the same place
# TODO: if we need to capture the output of *one* chroot command, this doesn't work because of set -v
def sh(commandline, env=None, printoutput=True, chroot=None, chroot_disable_daemons=False, cwd=None):
    if type(commandline) is str:
        commandline = [commandline]

    if chroot:
        if not (os.path.isdir(chroot)):
            raise Exception("chroot dir '{}' does not exist".format(chroot))
        logging.debug('chroot into {}'.format(chroot))
        nowstamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
        cs_name = "chroot_{}.sh".format(nowstamp)
        cs_path = "{}/root/{}".format(chroot, cs_name)
        cs_prefix = [
            '#!/bin/bash',
            'set -e',         # fail immediately on error
            ". /etc/profile", # otherwise we get complaints the $PATH isn't set
            'set -v']         # write each line to output before executing it
        cs_contents = cs_prefix + commandline
        write_file(cs_contents, cs_path, append=False, mode=0o700)
        outercmd = ["chroot {} /root/{}".format(chroot, cs_name)]
    else:
        outercmd = commandline

    outputs = []
    for cli in outercmd:
        print('\n'.join([
                    "==== Running command: {}".format(cli),
                    "     chroot: {}".format(chroot),
                    "     cwd:    {}".format(cwd),
                    "     env:    {}".format(env)]))

        if chroot and chroot_disable_daemons:
            disable_chroot_daemons()
        pristine_output = subprocess.check_output(cli, shell=True, env=env, cwd=cwd)
        if chroot and chroot_disable_daemons:
            enable_chroot_daemons()

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

disable_daemons_script = """
#!/bin/sh
set -e
echo -e '#!/bin/sh\nexit 101' > /usr/sbin/policy-rc.d 
chmod 755 /usr/sbin/policy-rc.d
if ! dpkg-divert --list | grep 'local diversion of /usr/sbin/invoke-rc.d' >/dev/null; then 
    dpkg-divert --add --local --divert /usr/sbin/invoke-rc.d.chroot --rename /usr/sbin/invoke-rc.d
    cp /bin/true /usr/sbin/invoke-rc.d
fi
"""
enable_daemons_script = """
#!/bin/sh
set -e
rm -f /usr/sbin/policy-rc.d
if dpkg-divert --list | grep 'local diversion of /usr/sbin/invoke-rc.d' >/dev/null; then 
    rm -f /usr/sbin/invoke-rc.d
    dpkg-divert --remove --rename /usr/sbin/invoke-rc.d
fi
"""

def disable_chroot_daemons(chroot):
    """
    Disable daemons from starting in a chroot
    Run this before installing daemon packages like ssh or tor
    """
    # Normally, policy-rc.d doesn't exist. If it does, then daemons have already been disabled
    if os.path.exists(chroot+'/usr/sbin/policy-rc.d'):
        logging.debug("Daemons already disabled in chroot {}".format(chroot))
        return
    logging.debug("Disabling daemons in chroot {}".format(chroot))
    write_file(['#!/bin/sh', 'exit 101'], chroot+'/usr/sbin/policy-rc.d', mode=0o755)
    sh(
        'dpkg-divert --add --local --divert /usr/sbin/invoke-rc.d.chroot --rename /usr/sbin/invoke-rc.d', 
        env={ 'LANG':'C' }, chroot=chroot)
    os.copy(chroot+'/bin/true', chroot+'/usr/sbin/invoke-rc.d')

def enable_chroot_daemons(chroot):
    """
    Re-enable daemons to start in the chroot
    Run this before finishing an image
    """
    # In a pristine system, policy-rc.d doesn't exist; if it doesn't now, then daemons are already enabled
    if not os.path.exists(chroot+'/usr/sbin/policy-rc.d'):
        logging.debug("Daemons already enabled in chroot {}".format(chroot))
        return
    logging.debug("Enabling daemons in chroot {}".format(chroot))
    os.remove(chroot+'/usr/sbin/policy-rc.d')
    os.remove(chroot+'/usr/sbin/invoke-rc.d')
    sh(
        'dpkg-divert --remove --rename /usr/sbin/invoke-rc.d',
        env={ 'LANG':'C' }, chroot=chroot)

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
    mountpoint = os.path.abspath(mountpoint)
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

# TODO: can't handle options or mountpoints with spaces! 
#       python's built-in 'shlex' module may be of use here?
def parse_mtab():
    mtab = []
    with open('/etc/mtab') as f:
        for line in f.readlines():
            fields = line.split(' ')
            mtab.append({
                    'fs_spec':    fields[0],
                    'fs_file':    fields[1],
                    'fs_vfstype': fields[2],
                    'fs_mntops':  fields[3],
                    'fs_freq':    fields[4],
                    'fs_passno':  fields[5]})
    return mtab

def is_mounted(mountpoint):
    for fs in parse_mtab():
        if fs['fs_file'] == mountpoint:
            return True
    else:
        return False

# TODO: if lsof is available, show all processes using the mountpoint
def umount(mountpoint, lsof_on_failure=True):
    """Use /bin/umount to unmount a device"""
    if is_mounted(mountpoint):
        try:
            sh('umount "{}"'.format(mountpoint))
        except:
            print('The following process are still using your mountpoint:')
            print(sh('lsof | grep {}'.format(mountpoint)))
            raise

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

# Can't be a member property of RaspSeedImage because it's used in some global functions
depsdir = os.getcwd()+'/dependencies'

# This isn't ideal. You'd like to store the finalstatus as part of the RaspSeedImage instance itself, but I can't figure out how to do that, because I need to reference it from within the statusmethod decorator
'''When an RaspSeedImage reachest this status, it is finished'''
finalstatus = 0
statusmethods = []
def statusmethod(func):
    '''
    Decorator for RaspSeedImage methods that set the image's status.
    Methods must be defined *in status order*, because the first function 
    decorated with @statusmethod will be assigned the property '.status = 1'
    and the second will be assigned '.status = 2' etc. 
    '''
    global finalstatus, statusmethods
    func.status = finalstatus
    def wrapper(class_instance, *args, **kwargs):
        logging.info("Running status function #{}: {}".format(
                func.status, func.__name__))
        func(class_instance, *args, **kwargs)
        class_instance.status = func.status
    wrapper.wrapped = func
    statusmethods += [wrapper]
    finalstatus += 1
    return wrapper

class RaspSeedImage(object):

    # Kali uses 3000MB. My initial experiment, a minimal debootstrap w/ sjoerd's
    # kernel, was just BARELY under 1024MB total
    min_size = 1536
    default_imagename = "raspseed.img"
    default_hostname  = "raspseed"
    default_arch      = "armhf"
    default_debmirr   = "ftp://ftp.debian.org/debian"

    compilable_kernels = {
        'rpi': {
            'url': 'https://github.com/raspberrypi/linux/archive/rpi-3.18.y.zip',
            'foldername': 'linux-rpi-3.18.y',
            'extractcmd': 'unzip'},
        #'mainline40': {
        'mainline': {
            'url': 'https://www.kernel.org/pub/linux/kernel/v4.x/linux-4.0.tar.xz',
            'foldername': 'linux-4.0',
            'extractcmd': 'tar xf'}
        }

    kernel_config_url = 'https://raw.githubusercontent.com/raspberrypi/linux/rpi-3.18.y/arch/arm/configs/bcm2709_defconfig'

    def __init__(self, imagepath=os.getcwd()+default_imagename,
                 imagesize=None, debianversion="sid",
                 kernel="sjoerd", overlaydir=None):
        '''
        __init__() should *not* make any modifications to actual files on disk! We rely on this in various places, so if it changes, stuff might break and **data could be deleted**.
        '''

        self.imagepath = imagepath
        self.imagedir, self.imagename = re.search('(.*)\/(.*)$', imagepath).group(1, 2)

        self.statfile   = self.imagepath+'.status.txt'
        self.mountpoint = self.imagepath+'.mnt'
        self.debianvers = debianversion
        self.hostname   = RaspSeedImage.default_hostname
        self.arch       = RaspSeedImage.default_arch
        self.debmirr    = RaspSeedImage.default_debmirr
        self.kernel     = kernel
        self.rootdev    = None 
        self.bootdev    = None
        self.overlaydir = overlaydir

        if os.path.isfile(self.imagepath):
            self.imagesize = os.stat(self.imagepath).st_size / 1024 / 1024
        else:
            self.imagesize = imagesize if imagesize else RaspSeedImage.min_size
        logging.info("Using a size of {}MB".format(self.imagesize))
        if self.imagesize < RaspSeedImage.min_size:
            raise Exception("imagesize {}MB smaller than minimum size of {}MB".format(self.imagesize, RaspSeedImage.min_size))

        logging.info(self)

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
    def status(self):
        """The current status of the image, as read from the status file"""
        if not os.path.exists(self.imagepath):
            return 0
        with open(self.statfile) as f:
            line1 = f.readline()
        return int(line1)

    @status.setter
    def status(self, status):
        write_file([str(status)], self.statfile, append=False)
        logging.info('Setting status to {}'.format(status))
        return status

    def __str__(self):
        selfstring = '\n'.join([
            "RaspSeedImage(",
            "  "+self.imagepath,
            "  status: {} (from {})".format(self.status, self.statfile),
            "  size: {}MB".format(self.imagesize),
            "  mountpoint: "+self.mountpoint,
            "  debian version: "+self.debianvers,
            "  hostname: "+self.hostname,
            "  architecture: "+self.arch,
            "  debian mirror: "+self.debmirr +")"])
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
            # the loopback command in setup_loopback_partitions returns immediately, even if
            # the fucking /dev/mapper devices haven't been created yet
            if m['device'].startswith('/dev/mapper') and not is_mounted(m):
                time.sleep(3)
            mount(
                device=m['device'], mountpoint=m['mountpoint'], 
                fstype=m['fstype'], fsoptions=m['fsoptions'])

    @statusmethod
    def debootstrap_stage1(self):
        self.mount_chroot()
        sh('qemu-debootstrap --verbose --arch={} "{}" "{}" "{}"'.format(
            self.arch, self.debianvers, self.mountpoint, self.debmirr))

    @statusmethod
    def debootstrap_stage3(self):
        self.mount_chroot()

        write_file(disable_daemons_script, self.mountpoint+'/root/disable_daemons.sh', append=False, mode=0o700)
        write_file(enable_daemons_script, self.mountpoint+'/root/enable_daemons.sh', append=False, mode=0o700)

        sl_new = [
            'deb http://ftp.debian.org/debian {} main contrib non-free'.format(self.debianvers),
            'deb-src http://ftp.debian.org/debian {} main contrib non-free'.format(self.debianvers)]
        write_file(sl_new, self.mountpoint+"/etc/apt/sources.list", mode=0o644)

        write_file([self.hostname], self.mountpoint+"/etc/hostname", append=False, mode=0o644)

        # Kali does this "so X doesn't complain": 
        hosts_contents = [
            "127.0.0.1       {} localhost".format(self.hostname),
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

        # stuff you probably want
        additional_packages = 'apt-transport-https aptitude file openssh-client openssh-server iw usbutils ntp'
        # TODO: micah's stuff you probably want to remove
        additional_packages+= 'python python-pip emacs24-nox screen git'

        stage3cmd = [
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
            #'apt-get -y install uboot-mkimage',
            'apt-get -y install git-core binutils ca-certificates initramfs-tools',
            'apt-get -y install console-common less nano git',
            'echo "root:toor" | chpasswd',
            '''sed -i -e 's/KERNEL\!=\"eth\*|/KERNEL\!=\"/' /lib/udev/rules.d/75-persistent-net-generator.rules''',
            'rm -f /etc/udev/rules.d/70-persistent-net.rules',
            'apt-get --yes --force-yes install $packages',
            'apt-get install -y {}'.format(additional_packages),
            'locale-gen',
            'dpkg-reconfigure locales',
            'update-rc.d ssh enable',
            # Cleanup: 
            'rm -f /etc/ssh_host_*_key.pub',
            'rm -rf /root/.bash_history',
            'apt-get clean',
            # 'rm -f /usr/bin/qemu*', # Keep this around if you want to keep chrooting in there
            'rm -f /0',
            'rm -f /hs_err*']
        sh(stage3cmd, env=thirdstage_env, chroot=self.mountpoint, chroot_disable_daemons=True)

        write_file(["T0:23:respawn:/sbin/agetty -L ttyAMA0 115200 vt100"], self.mountpoint+"/etc/inittab")

        fstab_contents = [
            '/dev/mmcblk0p1  /boot   vfat   ro                0       2',
            '/dev/mmcblk0p2  /       ext4   defaults,noatime  0       1']
        write_file(fstab_contents, "{}/etc/fstab".format(self.mountpoint), mode=0o644)

    def obtain_kernel_source(self, kernel):
        k = self.compilable_kernels[kernel]
        global depsdir
        makedirs(depsdir, exist_ok=True)
        archivename = re.match('.*/(.*)', k['url']).group(1)
        kdir = "{}/{}".format(depsdir, k['foldername'])
        # Download & extract the kernel source:
        if not os.path.exists(kdir):
            if not os.path.exists('{}/{}'.format(depsdir, archivename)):
                sh('wget "{}"'.format(k['url']), cwd=depsdir)
            sh('{} "{}"'.format(k['extractcmd'], archivename), cwd=depsdir)
        configname = re.match('.*/(.*)', self.kernel_config_url).group(1)
        configpath = "{}/arch/arm/configs/{}".format(
            kdir, configname)
        # Download the config
        if not os.path.exists(configpath):
            sh('wget "{}"'.format(self.kernel_config_url), 
               cwd=kdir+"/arch/arm/configs")
        # Download the firmware
        fwdir = "{}/raspberrypi-firmware".format(depsdir)
        if not os.path.exists(fwdir):
            sh('git clone --depth 1 https://github.com/raspberrypi/firmware.git "{}"'.format(fwdir))
        # Download the device tree source file
        dts_path = kdir+'/arch/arm/boot/dts/bcm2709-rpi-2-b.dts'
        dts_url = 'https://raw.githubusercontent.com/raspberrypi/linux/rpi-3.18.y/arch/arm/boot/dts/bcm2709-rpi-2-b.dts'
        if not os.path.exists(dts_path):
            sh('wget "{}"'.format(dts_url), cwd=kdir+'/arch/arm/boot/dts')
    

    def compile_linux_kernel(self, kernel):
        global depsdir
        k = self.compilable_kernels[kernel]
        kdir = "{}/{}".format(depsdir, k['foldername'])

        # make sure the kdir has the device tree source code in it

        zImage = kdir+'/arch/arm/boot/zImage'
        dtb    = kdir+'/arch/arm/boot/dts/bcm2709-rpi-2-b.dtb'
        logging.debug("kdir is {}".format(kdir))
        makeenv = {'ARCH': 'arm', 'CROSS_COMPILE': 'arm-linux-gnueabihf-'}
        if not os.path.exists(zImage) or not os.path.exists(dtb):
            jobs = 0
            with open('/proc/cpuinfo') as f:
                for line in f.readlines():
                    if re.match('processor', line):
                        jobs +=1
            jobs = int(jobs * 1.5)
            # TODO: why will my make commands fail if I don't dot-source /etc/profile first? 
            sh('; '.join(['. /etc/profile', 
                          'echo "ARCH is $ARCH"', 
                          'echo "CROSS_COMPILE is $CROSS_COMPILE"',
                          'make bcm2709_defconfig',
                          'make -j{}'.format(jobs)]),
               env=makeenv, cwd=kdir)

        # Install the kernel, device tree, modules, and firmware
        self.mount_chroot()
        sh('make modules_install INSTALL_MOD_PATH="{}"'.format(self.mountpoint),
           env=makeenv, cwd=kdir)
        shutil.copyfile(zImage, self.mountpoint+'/boot/firmware/kernel7.img')
        shutil.copyfile(dtb,    self.mountpoint+'/boot/firmware/bcm2709-rpi-2-b.dtb')
        fwdir = "{}/raspberrypi-firmware".format(depsdir)
        sh('cp -rf "{}"/boot/* "{}"'.format(
                fwdir, self.mountpoint+"/boot/firmware"))
        # Kali does this: is it necessary? Replaces the installed firmware w/ the mainline Linux firmware
        # rm -rf ${basedir}/root/lib/firmware
        # cd ${basedir}/root/lib
        # git clone --depth 1 https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git firmware
        # rm -rf ${basedir}/root/lib/firmware/.git

        # Create cmdline.txt and config.txt files for booting:
        write_file(
            'dwc_otg.fiq_fix_enable=1 console=ttyAMA0,115200 console=tty1 root=/dev/mmcblk0p2 rootfstype=ext4 rootwait ro rootflags=noload',
            "{}/boot/firmware/cmdline.txt".format(self.mountpoint))
        write_file(
            'gpu_mem=16',
            "{}/boot/firmware/config.txt".format(self.mountpoint))

    @statusmethod
    def install_kernel(self):
        if self.kernel == 'sjoerd':
            self.add_sjoerd_kernel()
        else:
            self.obtain_kernel_source(self.kernel)
            self.compile_linux_kernel(self.kernel)

    @statusmethod
    def copy_overlay(self):
        if self.overlaydir:
            shutil.copytree(self.overlaydir, self.mountpoint, symlinks=True)

    # TODO: this doesn't require u-boot, meaning that the kernel step and the stage3 step are more segregated than my infrastructure currently allows. 
    # WHICH IS GOOD because apparently uboot is broken in jessie rn? Although the Pi 2 is supported in mainline UBoot so I could just build it myself
    # see stage3 comments
    def add_sjoerd_kernel(self):
        sl_new = [
            'deb https://repositories.collabora.co.uk/debian/ jessie rpi2',
            'deb http://ftp.debian.org/debian experimental main']
        write_file(sl_new, self.mountpoint+"/etc/apt/sources.list", mode=0o644)

        sjoerd_env = {
            'LANG':'C', 
            'DEBIAN_FRONTEND':'noninteractive'
        }
        sjcommand = [
            # Do the work you need to do: 
            'apt-get update',
            # NOTE: package can't be authenticated. would be better to import the key out of band first, but until then, --force-yes:
            'apt-get install -y --force-yes collabora-obs-archive-keyring',
            'apt-get update',
            # NOTE: the linux-kbuild-3.18 package isn't included in jessie, but sjoerd's kernel images were build to depend on it:
            'apt-get -t experimental install linux-kbuild-3.18',
            # NOTE: sjoerd's page says the package is raspberrypi-firmware-nokernel. Nope. It's apparently raspberrypi-bootloader-nokernel.
            # NOTE: for some reason even after installing the keyring it can't auth at least some of these packages. I have to use --force-yes again. 
            'apt-get install -y --force-yes raspberrypi-bootloader-nokernel linux-image-3.18.0-trunk-rpi2 linux-headers-3.18.0-trunk-rpi2']
        sh(sjcommand, env=sjoerd_env, chroot=self.mountpoint, chroot_disable_daemons=True)

        # Copy the kernel & supporting files to the place that the Pi expects
        kpath = sh('chroot {} dpkg-query -L linux-image-3.18.0-trunk-rpi2 | grep vmlinuz'.format(self.mountpoint), env=sjoerd_env)
        shutil.copyfile(self.mountpoint+kpath, self.mountpoint+'/boot/firmware/kernel7.img')

        # create a cmdline.txt file. Uses the Raspbian one as a starting point
        cmdline_contents = "dwc_otg.lpm_enable=0 console=ttyAMA0,115200 root=/dev/mmcblk0p2 rootfstype=ext4 elevator=deadline rootwait"
        write_file(cmdline_contents, "{}/boot/firmware/cmdline.txt".format(self.mountpoint))

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

    def buildup(self, statuslevel=None, overwrite=False, whatif=False):
        global finalstatus, statusmethods

        print("Image {} starting at status {}".format(self.imagename, self.status))

        if overwrite:
            logging.info("Removing existing image/stat file...")
            if not whatif:
                self.detach_image()
                if os.path.exists(self.imagepath): os.remove(self.imagepath)
                if os.path.exists(self.statfile): os.remove(self.statfile)

        finish = (statuslevel if statuslevel else finalstatus)
        for stat in range(self.status, finish):
            func = statusmethods[stat]
            logging.info("Running function #{} - '{}' - {}".format(
                    stat, func.wrapped.__name__, func.wrapped.__doc__))
            if not whatif:
                # Basically when you call instance.method(), python calls Class.method(instance). These do the same thing:
                #   imgobj = RaspSeedImage(); imgobj.memberfunc(arg1, argN);
                #   imgobj = RaspSeedImage(); f=RaspSeedImage.memberfunc; f(imgobj, arg1, argN);
                # (This is why all class methods must have at least one argument, usually called 'self')
                func(self)

        print("Image complete: {}".format(self))

def install_prereqs():
    # Note: We assume a completely bare debian install, so that this is enough to get a bare netinstall system up and running
    raise Exception("You need to add the emdebian mirror for what you wanna use ugh. don't forget to get the apt keys. then install sudo apt-get install emdebian-archive-keyring")

    # emdebian info, including repo URLs: http://www.emdebian.org/crosstools.html
    emdebian_host_packages = ""
    host_packages = "dosfstools parted kpartx debootstrap qemu-user-static binfmt-support python ntp gcc-arm-linux-gnueabi bc unzip libncurses5-dev"
    logging.info("Installing host packages: {}".format(host_packages))
    sh("apt-get install -y {}".format(host_packages))

# I only need to do this because I wanted to examine the packages more closely
# Most people shouldn't need this
def download_collabora_repo():
    global depsdir
    url = 'https://repositories.collabora.co.uk/debian/'
    colldir = depsdir+"/collabora-repository"
    makedirs(colldir, exist_ok=True)
    sh(
        'wget --base="{0}" -nH --convert-links --mirror --page-requisites --no-parent "{0}"'.format(url),
        cwd=colldir)
        

def get_argparser():
    """
    Build the argparse object, but do not parse the arguments
    """
    # ARGPARSE
    argparser = argparse.ArgumentParser(
        description='Build an image for the Raspberry Pi 2')
    subparsers = argparser.add_subparsers(dest='subparser')

    # For now, I set it to always display debug messages (below)
    # argparser.add_argument(
    #     '--verbose', '-v', action='count', 
    #     help='Print verbose messages (-vv for debug messages)')

    # PARENT PARSERS
    imagep = argparse.ArgumentParser(add_help=False)
    imagep.add_argument(
        '--image-path', '-i', action='store', dest='imagepath',
        default=os.getcwd()+RaspSeedImage.default_imagename,
        help='The path to the image file')

    # SUBPARSERS
    setups = subparsers.add_parser('setup')
    setups.add_argument(
        '--install-prereqs', '-p', action='store_true', dest='prereqs',
        help='Install required packages on the chroot host')
    setups.add_argument(
        '--download-collabora', action='store_true', dest='collabora',
        help='Download all packages from the sjoerd/collabora repository')

    infos = subparsers.add_parser('info', parents=[imagep])
    infos.add_argument(
        '--levels', '-l', action='store_true',
        help='Show the different status levels possible for an image')

    detachs = subparsers.add_parser('detach', parents=[imagep])

    attachs = subparsers.add_parser('attach', parents=[imagep])
    attachs.add_argument(
        '--chroot', '-c', action='store_true', 
        help='Open a chroot shell into the mounted directory after attaching')

    # TODO: Add a way to tell it to bring it to a specific status level
    images = subparsers.add_parser('image', parents=[imagep])
    images.add_argument(
        '--imagesize', '-s', action='store', default=RaspSeedImage.min_size,
        type=int, help='The size of the image in megabytes')
    images.add_argument(
        '--statuslevel', '-l', action='store', default=None, type=int, 
        help='Do not take the image passt the specified statuslevel')
    images.add_argument(
        '--debianversion', '-d', action='store', default='sid',
        help='The version of debian to use')
    images.add_argument(
        '--force', '-f', action='store_true', 
        help='Force create, even if there is an existing image file')
    images.add_argument(
        '--whatif', '-n', action='store_true',
        help='Take no action, but print what actions would be taken')
    images.add_argument(
        '--kernel', '-k', action='store', 
        choices=['sjoerd','mainline','rpi'], default='sjoerd',
        help=' '.join(
            ['How to get the kernel? Can use precompiled binaries',
             'from sjoerd (working), compile the mainline kernel (currently',
             'broken), or compile the kernel w/ patches from the Raspberry Pi',
             'Foundation (unimplemented).']))
    images.add_argument(
        '--overlay-directory', '-o', action='store', default=None, 
        destination='overlaydir', help='Copy an overlay onto the chroot')

    return argparser


def parse_args(parser, *args):
    parsedargs = parser.parse_args()
    
    ### ALWAYS DISPLAY DEBUG MESSAGES
    # if not parsedargs.verbose: 
    #     logging.basicConfig(format='%(levelname)s: %(message)s')
    # elif parsedargs.verbose == 1:
    #     logging.basicConfig(format='%(levelname)s: %(message)s',level=logging.INFO)
    # elif parsedargs.verbose > 1:
    #    logging.basicConfig(format='%(levelname)s: %(message)s',level=logging.DEBUG)        
    logging.basicConfig(format='%(levelname)s: %(message)s',level=logging.DEBUG)        
    logging.debug("Arguments after parsing: {}".format(parsedargs))

    return parsedargs

def execute(parsedargs, post_setup=None, post_image=None):

    if parsedargs.subparser == 'setup':
        if parsedargs.prereqs: install_prereqs()
        if parsedargs.collabora: download_collabora_repo()

    elif parsedargs.subparser == 'image':
        image = RaspSeedImage(
            imagepath = parsedargs.imagepath, 
            imagesize = parsedargs.imagesize, 
            debianversion = parsedargs.debianversion, 
            kernel = parsedargs.kernel, 
            overlaydir = parsedargs.overlaydir)
        image.buildup(
            overwrite=parsedargs.force, 
            statuslevel=parsedargs.statuslevel,
            whatif=parsedargs.whatif)

    elif parsedargs.subparser == 'info':
        if parsedargs.imagepath:
            image = RaspSeedImage(imagepath=parsedargs.imagepath)
            print(image)
        if parsedargs.levels:
            logging.basicConfig(format='%(levelname)s: %(message)s',level=logging.INFO)
            image = RaspSeedImage(
                imagepath = parsedargs.imagepath)
            image.buildup(whatif=True)

    elif parsedargs.subparser == 'detach':
        image = RaspSeedImage(imagepath=parsedargs.imagepath)
        image.detach_image()

    elif parsedargs.subparser == 'attach':
        image = RaspSeedImage(imagepath=parsedargs.imagepath)
        image.mount_chroot()
        #if parsedargs.chroot:
            # TODO! Open a shell here

def main(*args):
    """A sample main() function that only gets called when raspseed.py is run directly"""
    parser = get_argparser()
    parsed = parse_args(parser, *args)
    execute(parsed)

if __name__ == '__main__':
    sys.exit(main(*sys.argv))
