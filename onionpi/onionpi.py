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
from pdb import set_trace as strace

# TODO: PEP8-ify this, I do a lot of things in a non-standard way apparently


# TODO: would be better to have printed output & logging configured in the same place
def sh(commandline, env=None, printoutput=True, chroot=False, cwd=None):
    print("==== Running command: {}".format(commandline))
    pristine_output = subprocess.check_output(commandline, shell=True, env=env, cwd=cwd)
    output = pristine_output.strip('\r\n')
    if printoutput and len(output) > 0:
        print(output)
        print("==== Finished")
    return output

# NOTE: The chroot stuff is completely untested! I should probably take it out tbh.
# def sh(commandline, env=None, printoutput=True, chroot=False):
#     
#     if chroot:
#         if not (os.path.isdir(chroot)):
#             raise Exception("chroot dir '{}' does not exist".format(chroot))
#         print("==== In chroot {} running command {}".format(chroot, commandline))
#         cs_name = "chroot_{}.sh".format(uuid.uuid4())
#         cs_path = "{}/{}".format(chroot, cs_name)
#         cs_contents = [
#             "mount -t proc proc /proc",
#             commandline]
#         write_file(cs_contents, cs_path, append=False, mode=0o700)
#         commandline = "chroot {} /{}".format(chroot, cs_name)
#     else:
#         print("==== Running command: {}".format(commandline))

#     pristine_output = subprocess.check_output(commandline, shell=True, env=env)
#     output = pristine_output.strip('\r\n')
#     if printoutput and len(output) > 0:
#         print(output)
#         print("==== Finished")
#     return output

def write_file(contents, filename, append=True, mode=False):
    if type(contents) is str:
        contents = [contents]
    openmode = 'w' if append else 'w+'
    with open(filename, openmode) as f:
        print('\n'.join(contents), file=f)
    if mode:
        os.chmod(filename, mode)

def makedirs(path, mode=0o777, exist_ok=False):
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

            # Agh. Can't do the 'if exdevice != device' check because mounting /dev to /chroot/dev
            # will also get udev mounted there right on top of it. Fuck. 
            # if exdevice != device:
            #     raise Exception("Attempted to mount '{}' on '{}' but '{}' is already mounted there".format(
            #         device, mountpoint, exdevice))
            # else:
            #     logging.info("Attempted to mount '{}' on '{}' but it is already mounted there".format(
            #         device, mountpoint))
            #     ismounted = True
            logging.info("Attempted to mount '{}' on '{}' but it is already mounted there".format(
                device, mountpoint))
            ismounted = True

    if not ismounted:
        sh('mount {} {} "{}" "{}"'.format(fs_type, fs_options, device, mountpoint))

def umount(mountpoint):
    """
    Use /bin/umount to unmount a device 
    If the device is not mounted, do nothing
    """
    for mount in sh('mount', printoutput=False).split('\n'):
        exdevice, exmountpoint = re.search('(.+) on (.+) type ', mount).group(1, 2)
        if exmountpoint == mountpoint:
            sh('umount "{}"'.format(mountpoint))

def check_loopdev(image):
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
    

# TODO: have every method require a version level and then DTRT if the image is not at that version level
class OnionPiImage(object):

    # Kali requires 3000MB so that's what I'm going with here. 
    # Could stand to do some experiments to determine a real min size for debian though
    min_size = 1024

    def __init__(self, imagepath,
        mountpoint="/mnt/onionpi", imagesize=False, debianversion="sid", 
        hostname="onionpi", workdir='/tmp/onionpi-workdir'):


        self.imagepath = imagepath
        self.imagedir, self.imagename = re.search('(.*)\/(.*)$', imagepath).group(1, 2)

        if os.path.isfile(self.imagepath):
            self.imagesize = os.stat(self.imagepath).st_size / 1024 / 1024
        else:
            self.imagesize = imagesize if imagesize else OnionPiImage.min_size
        logging.info("Using a size of {}MB".format(self.imagesize))
        if self.imagesize < OnionPiImage.min_size:
            raise Exception("imagesize {}MB smaller than minimum size of {}MB".format(self.imagesize, OnionPiImage.min_size))

        self.mountpoint = mountpoint
        self.debianversion = debianversion
        self.hostname = hostname

        self.arch = "armhf"
        self.debmirr = "ftp://ftp.debian.org/debian"

        logging.info(self)

        self.statusmethod = {
            0: lambda *a, **k: None,          # noop
            1: self.create_image,
            2: self.partition_image,
            3: self.create_image_filesystems,
            4: self.debootstrap_stage1,
            5: self.debootstrap_stage3,
            6: self.compile_kernel,
            7: self.add_rpi_firmware,
            8: self.generate_checksum }
        self.finalstatus = len(self.statusmethod) -1

        self.rootdev = None
        self.bootdev = None

        self.workdir = workdir

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

    # TODO: This isn't right. There should be a way to do getters and setters in Python, and that's what I wanna do
    def set_status(self, status):
        write_file([str(status)], self.statfile, append=False)
        return status

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

        if 0 < statint <= 7:
            return statint
        else:
            raise Exception('Invalid statfile')

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

    def create_image(self):
        makedirs(self.imagedir, mode=0o755, exist_ok=True)
        sh('dd if=/dev/zero of="{}" bs=1M count="{}"'.format(
            self.imagepath, self.imagesize))

        return self.set_status(1)

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
        return self.set_status(2)

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
        if len(loopdevs) > 1:
            logging.warn("Multiple loop devices found: {}".format(self.imagepath, loopdevs))
        if len(loopdevs) > 0:
            self.loopdev_path = loopdevs[0]
            logging.info("Using existing loopback device at '{}'".format(self.loopdev_path))
        else:
            # If that didn't work, find a new loopback device to use, and attach the image
            self.loopdev_path = sh('losetup --find "{}" --show'.format(self.imagepath))
            logging.info("Connected image to new loopback device at '{}'".format(self.loopdev_path))

    def create_image_filesystems(self):
        self.setup_loopback_partitions()

        sh('mkfs.vfat {}'.format(self.bootdev))
        sh('mkfs.ext4 {}'.format(self.rootdev))
        return self.set_status(3)

    def mount_chroot(self):
        self.setup_loopback_partitions()
        for m in self.mounts:
            mount(device=m['device'], mountpoint=m['mountpoint'], 
                fstype=m['fstype'], fsoptions=m['fsoptions'])

    def debootstrap_stage1(self):
        # Why do debootstrap in stages? The ArmHardFloatChroot debian wiki page just uses qemu-debootstrap to do it all at once
        # This is the way it's done in Kali's build script - I haven't tested or compared them

        self.mount_chroot()

        # The Kali Linux way
        #sh('debootstrap --foreign --arch={} "{}" "{}" "{}"'.format(
        #    self.arch, self.debianversion, self.mountpoint, self.debmirr))

        # TODO: --no-check-gpg ??

        # The way on the ArmHardFloatChroot debian wiki page
        sh('qemu-debootstrap --no-check-gpg --verbose --arch={} "{}" "{}" "{}"'.format(
            self.arch, self.debianversion, self.mountpoint, self.debmirr))
        

        return self.set_status(4)

    # def debootstrap_stage2(self):
    #     self.mount_chroot()
    #     # The "qemu-arm-static" binary is from the "qemu-user-static" Debian package
    #     shutil.copyfile("/usr/bin/qemu-arm-static", self.mountpoint+"/usr/bin/qemu-arm-static")
    #     sh('chroot "{}" /bin/sh /debootstrap/debootstrap --second-stage'.format(self.mountpoint), 
    #         env={"LANG":"C"})
    #     return self.set_status(5)

    def debootstrap_stage3(self):
        self.mount_chroot()
        sourceslist_contents = [
            "deb http://ftp.debian.org/debian {} main".format(self.debianversion),
            "deb-src http://ftp.debian.org/debian {} main".format(self.debianversion)]
        write_file(sourceslist_contents, self.mountpoint+"/etc/apt/sources.list", 
            append=False, mode=0o755)

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
            "console-common console-data/keymap/policy select Select keymap from full list",
            "console-common console-data/keymap/full select en-latin1-nodeadkeys"]
        write_file(debconfset_contents, self.mountpoint+"/debconf.set")

        thirdstage_env = {
            'LANG':'C', 
            'DEBIAN_FRONTEND':'noninteractive'
        }

        thirdstage_contents = [
            '#!/bin/bash',
            #'set -e'  # This fails the script in here. TODO: write better error handling for that case. Bleh.
            'set -v',
            'dpkg-divert --add --local --divert /usr/sbin/invoke-rc.d.chroot --rename /usr/sbin/invoke-rc.d',
            'cp /bin/true /usr/sbin/invoke-rc.d',
            'echo -e "#!/bin/sh\nexit 101" > /usr/sbin/policy-rc.d',
            'chmod +x /usr/sbin/policy-rc.d',
            '',
            'apt-get update',
            'apt-get install locales-all',
            '',
            'debconf-set-selections /debconf.set',
            'rm -f /debconf.set',
            'apt-get update',
            'apt-get -y install git-core binutils ca-certificates initramfs-tools uboot-mkimage',
            'apt-get -y install locales console-common less nano git',
            'echo "root:toor" | chpasswd',
            '''sed -i -e 's/KERNEL\!=\"eth\*|/KERNEL\!=\"/' /lib/udev/rules.d/75-persistent-net-generator.rules''',
            'rm -f /etc/udev/rules.d/70-persistent-net.rules',
            'apt-get --yes --force-yes install $packages',
            '',
            'update-rc.d ssh enable',
            '',
            'rm -f /usr/sbin/policy-rc.d',
            'rm -f /usr/sbin/invoke-rc.d',
            'dpkg-divert --remove --rename /usr/sbin/invoke-rc.d',
            '',
            'rm -f /third-stage']
        write_file(thirdstage_contents, self.mountpoint+"/thirdstage", append=False, mode=0o700)
        sh("chroot {} /thirdstage".format(self.mountpoint), thirdstage_env)

        cleanup_contents = [
            '#!/bin/bash',
            'set -e',
            'set -v',
            'rm -rf /root/.bash_history',
            'apt-get update',
            'apt-get clean',
            'rm -f /0',
            'rm -f /hs_err*',
            # 'rm -f /usr/bin/qemu*', # Keep this around if you want to keep chrooting in there
            'rm -f cleanup']
        write_file(cleanup_contents, self.mountpoint+"/cleanup", append=False, mode=0o700)
        sh("chroot {} /cleanup".format(self.mountpoint), thirdstage_env)

        write_file(["T0:23:respawn:/sbin/agetty -L ttyAMA0 115200 vt100"], self.mountpoint+"/etc/inittab", append=True)

        return self.set_status(5)

    def test_chroot(self):
        # Just useful for testing that my chroot still works
        #sh("chroot {} /bin/ls /dev".format(self.mountpoint))
        return

    def compile_kernel(self):
        makedirs(self.workdir, exist_ok=True)
        kdir = "{}/linux".format(self.workdir)

        # TODO: is this the best version of the kernel?? bleh
        if os.path.exists(kdir):
            shutil.rmtree(kdir)
        sh('git clone --depth 1 https://github.com/raspberrypi/linux -b rpi-3.18.y "{}"'.format(kdir))
        
        # Compile the kernel
        procs = 0
        with open('/proc/cpuinfo') as f:
            for line in f.readlines():
                if re.match('processor', line):
                    procs +=1
        makeenv = {'ARCH': 'arm', 'CROSS_COMPILE': 'arm-linux-gnueabihf-'}
        sh('make -j {}'.format(procs), env=makeenv, cwd=kdir)
        sh('make modules_install INSTALL_MOD_PATH="{}"'.format(self.mountpoint), env=makeenv, cwd=kdir)
        shutil.copyfile(kdir+'/arch/arm/boot/zImage', self.mountpoint+'/boot/firmware/kernel7.img')
        sh('cp "{}"/arch/arm/boot/dts/bcm*.dtb {}/boot/firmware/'.format(kdir, self.mountpoint))

        # Kali does this: is it necessary? Replaces the installed firmware w/ the mainline Linux firmware
        # rm -rf ${basedir}/root/lib/firmware
        # cd ${basedir}/root/lib
        # git clone --depth 1 https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git firmware
        # rm -rf ${basedir}/root/lib/firmware/.git

        return self.set_status(6)

    def add_rpi_firmware(self):
        makedirs(self.workdir, exist_ok=True)
        fwdir = "{}/firmware".format(self.workdir)

        if os.path.exists(fwdir):
            shutil.rmtree(fwdir)
        sh('git clone --depth 1 https://github.com/raspberrypi/firmware.git "{}"'.format(fwdir))
        sh('cp -rf "{}"/boot/* "{}"'.format(fwdir, self.mountpoint+"/boot/firmware"))

        # Create cmdline.txt file
        cmdline_contents = 'dwc_otg.fiq_fix_enable=1 console=tty1 console=tty1 root=/dev/mmcblk0p2 rootfstype=ext4 rootwait ro rootflags=noload'
        write_file(cmdline_contents, "{}/boot/firmware/cmdline.txt".format(self.mountpoint))

        return self.set_status(7)

    def detach_image(self):
        for m in reversed(self.mounts):
            umount(m['mountpoint'])

        # Note: will detach ALL loopback devices for the image
        loopdev = check_loopdev(self.imagepath)
        for ld in loopdev:
            logging.info("Removing loopback device '{}'".format(ld))
            sh('kpartx -dv {}'.format(ld))
            sh("losetup --detach '{}'".format(ld))

    def generate_checksum(self):
        self.detach_image()
        with open(self.imagepath, 'rb') as img:
            self.sha1sum = hashlib.sha1(img.read()).hexdigest() 
        logging.info("SHA1 for image at '{}' is '{}'".format(self.imagepath, self.sha1sum))
        write_file(self.sha1sum, "{}.sha1".format(self.imagepath), append=False)

    def go(self, statuslevel=None, overwrite=False, whatif=False):
        print("Image {} starting at status {}".format(self.imagename, self.status))

        if overwrite:
            logging.info("Removing existing image/stat file...")
            if not whatif:
                self.detach_image()
                if os.path.exists(self.imagepath): os.remove(self.imagepath)
                if os.path.exists(self.statfile): os.remove(self.statfile)

        finish = self.finalstatus if self.finalstatus > statuslevel else statuslevel
        for stat in range(self.status +1, finish +1):
            logging.info("Running function {}: {}".format(stat, self.statusmethod[stat].__name__))
            if not whatif:
                self.statusmethod[stat]()

        print("Image complete: {}".format(self))

    def test_go(self, statuslevel=None, overwrite=False):
        self.debootstrap_stage3(fake=True)
        self.test_chroot()


def setup_host():
    # Note: We assume a completely bare debian install, so that this is enough to get a bare netinstall system up and running

    raise Exception("You need to add the emdebian mirror for what you wanna use ugh. don't forget to get the apt keys. then install sudo apt-get install emdebian-archive-keyring")

    # emdebian info, including repo URLs: http://www.emdebian.org/crosstools.html
    emdebian_host_packages = ""
    host_packages = "dosfstools parted kpartx debootstrap qemu-user-static binfmt-support python ntp gcc-arm-linux-gnueabi"
    logging.info("Installing host packages: {}".format(host_packages))
    sh("apt-get install -y {}".format(host_packages))

def main(*args):
    argparser = argparse.ArgumentParser(
        description='Build an image for the Raspberry Pi 2')
    subparsers = argparser.add_subparsers(dest='subparser')

    argparser.add_argument('--verbose', '-v', action='count', 
        help='Print verbose messages (-vv for debug messages)')

    setupp = subparsers.add_parser('setup')
    #setupp.add_argument('-s', '--setup-host', action='store_true',
    #    help='Install required packages on the chroot host')

    infop = subparsers.add_parser('info')
    infop.add_argument('imagepath', action='store', 
        help='The path to the image file')

    detachp = subparsers.add_parser('detach')
    detachp.add_argument('imagepath', action='store', 
        help='The path to the image file')

    # TODO: Add a way to tell it to bring it to a specific status level
    imagep = subparsers.add_parser('image')
    imagep.add_argument('imagepath', action='store', 
        help='The path to the image file')
    imagep.add_argument('--mountpoint', '-m', action='store', default='/mnt/onionpi',
        help='The path to use as a mountpoint')
    imagep.add_argument('--imagesize', '-s', action='store', type=int, default=OnionPiImage.min_size,
        help='The size of the image in megabytes')
    imagep.add_argument('--debianversion', '-d', action='store', default='sid',
        help='The version of debian to use')
    imagep.add_argument('--overwrite', '-o', action='store_true',
        help='Delete any existing image file')
    imagep.add_argument('--whatif', '-n', action='store_true',
        help='Take no action, but print what actions would be taken')
    imagep.add_argument('--workdir', '-w', action='store',
        help='Temporary directory for compiling etc')

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
        setup_host()
    elif parsed.subparser == 'image':
        image = OnionPiImage(imagepath=parsed.imagepath, mountpoint=parsed.mountpoint, 
            imagesize=parsed.imagesize, debianversion=parsed.debianversion, workdir=parsed.workdir)
        image.go(overwrite=parsed.overwrite, whatif=parsed.whatif)
    elif parsed.subparser == 'info':
        image = OnionPiImage(imagepath=parsed.imagepath)
        print(image)
    elif parsed.subparser == 'detach':
        image = OnionPiImage(imagepath=parsed.imagepath)
        image.detach_image()

if __name__ == '__main__':
    sys.exit(main(*sys.argv))
