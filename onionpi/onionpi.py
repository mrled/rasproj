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
from pdb import set_trace as strace

def sh(commandline, env=None):
    return subprocess.check_output(commandline, shell=True, env=env)

def write_file(contents, filename, append=True, mode=False):
    mode = 'w' if append else 'w+'
    with open(filename, mode) as f:
        print(contents, file=f)
    if mode:
        os.chmod(filename, mode)

# TODO: have every method require a version level and then DTRT if the image is not at that version level
class OnionPiImage(object):
    def __init__(self, imagepath,
        mountpoint="/mnt/onionpi", imagesize=4096, debianversion="sid",
        hostname="onionpi"):

        # TODO: an image that already exists shouldn't be set to 4096, but should be set to whatever size it actually is
        if imagesize < 4096:
            raise Exception("imagesize ({}) too small".format(imagesize))

        self.imagepath = imagepath
        self.imagesize = imagesize
        self.imagedir, self.imagename = re.search('(.*)\/(.*)$', imagepath).group(1, 2)

        self.mountpoint = mountpoint
        self.debianversion = debianversion
        self.hostname = hostname

        self.arch = "armhf"
        self.debmirr = "ftp://ftp.debian.org/debian"

        logging.info(self)

    @property
    def statfile(self):
        """The location of the status file"""
        return "{}_onionpi_status.txt".format(self.imagepath)

    @property
    def status(self):
        """
        The current status of the image, as read from the status file
        Statuses: 
        0:  Nonexistent (the image file has not been created)
        1:  The image has been created
        2:  The image has been partitioned (can tell with fdisk -l)
        3:  Filesystems have been created on the image (how can I tell?)
        4:  Filesystems are mounted to self.mountpoint
        5:  debootstrap_stage1 has completed (determine w/ statfile)
        6:  debootstrap_stage2 has completed
        7:  debootstrap_stage3 has completed
        """
        if not os.path.exists(self.imagepath):
            return 0
        
        if not os.path.exists(self.statfile):
            raise Exception("Image exists but statfile does not")

        with open(self.statfile) as f:
            contents = f.readlines()
        if len(contents) is not 1:
            raise Exception("statfile is invalid format")

        try:
            statint = int(contents)
        except:
            raise Exception("statfile is invalid format")

        if 0 < statint <= 7:
            return statint
        else:
            raise Exception('Invalid statfile')

    def __str__(self):
        str = (
            "OnionPiImage(",
            "  "+self.imagepath,
            "  status: {} (from {})".format(self.status, self.statfile),
            "  size: {}MB".format(self.imagesize),
            "  mountpoint: "+self.mountpoint,
            "  debian version: "+self.debianversion,
            "  hostname: "+self.hostname,
            "  architecture: "+self.arch,
            "  debian mirror: "+self.debmirr,
            "  hostname: {})".format(self.hostname))
        return str

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
        os.mkdirs(self.imagedir, mode=0o755, exist_ok=True)
        sh('dd if=/dev/zero of="{}" bs=1M count="{}"'.format(
            self.imagepath, self.imagesize))
        
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

    def set_partition_devices(self):
        # Retrieve mapper name, e.g. /dev/mapper/loop0
        loopmap = sh("kpartx -lva \"{}\" | sed -E 's/.*(loop[0-9])p.*/\1/g' | head -1".format(loopdev_path))
        self.bootdev = loopmap+"p1"   # e.g. /dev/mapper/loop0p1
        self.rootdev = loopmap+"p2"   # e.g. /dev/mapper/loop0p2

    def create_image_filesystems(self):
        # Add device for the image file, e.g. /dev/loop0 
        self.loopdev_path = sh('losetup -f "{}"  --show'.format(imagepath))

        # Add devices for each partition
        sh("kpartx -va \"{}\"".format(loopdev_path))
        set_partition_devices()

        sh('mkfs.vfat {}'.format(self.bootdev))
        sh('mkfs.ext4 {}'.format(self.rootdev))

    def mount_chroot(self):
        os.mkdirs(self.mountpoint, mode=0o755, exist_ok=True)
        sh('mount "{}" "{}"'.format(self.rootdev, self.mountpoint))

        bootpath = self.mountpoint+"/boot/firmware"
        os.mkdirs(bootpath, mode=0o755, exist_ok=True)
        sh('mount "{}" "{}"'.format(self.bootdev, bootpath))

        procpath = self.mountpoint+"/proc"
        os.mkdirs(procpath, mode=0o755, exist_ok=True)
        sh('mount -t proc proc "{}"'.format(procpath))

        devpath = self.mountpoint+"/dev"
        os.mkdirs(devpath, mode=0o755, exist_ok=True)
        sh('mount -o bind /dev/ "{}"'.format(devpath))

        devptspath = self.mountpoint+"/dev"
        os.mkdirs(devptspath, mode=0o755, exist_ok=True)
        sh('mount -o bind /dev/pts "{}"'.format(devptspath))

    def debootstrap_stage1(self):
        """
        Why do debootstrap in stages? The ArmHardFloatChroot debian wiki page just uses qemu-debootstrap to do it all at once
        This is the way it's done in Kali's build script - I haven't tested or compared them
        """
        sh("debootstrap --foreign --arch={} {} {} {}".format(
            self.arch, self.debianversion, self.mountpoint, self.debmirr))

    def debootstrap_stage2(self):
        # TODO: What package does that file come from? 
        shutil.copyfile("/usr/bin/qemu-arm-static", self.mountpoint+"/usr/bin")
        sh("chroot {} /debootstrap/debootstrap --second-stage".format(self.mountpoint), 
            env={"LANG":"C"})

    def debootstrap_stage3(self):
        sourceslist_contents = (
            "deb http://ftp.debian.org/debian {} main".format(self.version),
            "deb-src http://ftp.debian.org/debian {} main".format(self.version))
        write_file(self.mountpoint+"/etc/apt/sources.list", 
            sourceslist_file, append=False, mode=0o755)

        write_file(self.hostname, self.mountpoint+"/etc/hostname", append=False, mode=0o644)

        # Kali does this "so X doesn't complain": 
        hosts_contents = (
            "127.0.0.1       {}    localhost".format(self.hostname),
            "::1             localhost ip6-localhost ip6-loopback",
            "fe00::0         ip6-localnet",
            "ff00::0         ip6-mcastprefix",
            "ff02::1         ip6-allnodes",
            "ff02::2         ip6-allrouters")
        write_file(hosts_contents, self.mountpoint+"/etc/hosts", append=False, mode=0o644)
        
        interfaces_contents = (
            "auto lo",
            "iface lo inet loopback",
            "auto eth0",
            "iface eth0 inet dhcp")
        write_file(interfaces_contents, self.mountpoint+"/etc/network/interfaces", append=False, mode=0o644)

        # TODO: do something better here, or at least allow a customization that doesn't rely on Google
        write_file("nameserver 8.8.8.8", self.mountpoint+"/etc/resolv.conf", append=False, mode=0o644)

        # TODO: what do these lines really do though
        debconfset_contents = (
            "console-common console-data/keymap/policy select Select keymap from full list",
            "console-common console-data/keymap/full select en-latin1-nodeadkeys")
        write_file(debconfset_contents, self.mountpoint+"/debconf.set")

        thirdstage_env = {
            'LANG':'C', 
            'DEBIAN_FRONTEND':'noninteractive'
        }

        thirdstage_contents = (
            '#!/bin/bash',
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
            'rm -f /third-stage')
        write_file(thirdstage_contents, self.mountpoint+"/thirdstage", append=False, mode=0o700)
        sh("chroot {} /thirdstage".format(self.mountpoint), thirdstage_env)

        cleanup_contents = (
            '#!/bin/bash',
            'rm -rf /root/.bash_history',
            'apt-get update',
            'apt-get clean',
            'rm -f /0',
            'rm -f /hs_err*',
            'rm -f cleanup',
            'rm -f /usr/bin/qemu*')
        write_file(cleanup_contents, self.mountpoint+"/thirdstage", append=False, mode=0o700)
        sh("chroot {} /cleanup".format(self.mountpoint), thirdstage_env)

    def detatch_image(self):
        # TODO: Should check if this needs doing before doing it
        sh("umount {}/proc/sys/fs/binfmt_misc".format(self.mountpoint))
        sh("umount {}/dev/pts".format(self.mountpoint))
        sh("umount {}/dev/".format(self.mountpoint))
        sh("umount {}/proc".format(self.mountpoint))


#     def deboostrap_chroot_sjoern(self):
#         # TODO: --no-check-gpg ??
#         sh("qemu-debootstrap --no-check-gpg --arch=armhf {} {} {}".format(
#             self.debianversion, self.mountpoint, ))
#
#         sourceslist_file = self.mountpoint+"/etc/apt/sources.list"
#         sourceslist_contents = (
#             "deb http://ftp.debian.org/debian {} main".format(self.version)
#             "deb-src http://ftp.debian.org/debian {} main".format(self.version)
#             "deb https://repositories.collabora.co.uk/debian jessie rpi2"
#             "deb http://ftp.debian.org/debian experimental main")
#         print(sourceslist_contents, file=sourceslist_file)
#         os.chmod(sourceslist_file, 0o755)
#    
#         policyrcd = self.mountpoint+"/usr/sbin/policy-rc.d"
#         policyrcd_contents = (
#             'echo "************************************" >&2'
#             'echo "All rc.d operations denied by policy" >&2'
#             'echo "************************************" >&2'
#             'exit 101')
#         print(policyrcd_contents, file=policyrcd_file)
#         os.chmod(policyrcd_file, 0o755)


def setup_host():
    host_packages = "dosfstools parted kpartx qemu-bootstrap python"
    logging.info("Installing host packages: {}".format(host_packages))
    sh("apt-get install -y {}".format(host_packages))


def main(*args):
    argparser = argparse.ArgumentParser(
        description='Build an image for the Raspberry Pi 2')
    subparsers = argparser.add_subparsers(dest='subparser')

    argparser.add_argument('--verbose', '-v', action='store_true', 
        help='Print verbose messages')

    setupp = subparsers.add_parser('setup')
    #setupp.add_argument('-s', '--setup-host', action='store_true',
    #    help='Install required packages on the chroot host')

    imagep = subparsers.add_parser('image')
    imagep.add_argument('imagepath', action='store', 
        help='The path to the image file')
    imagep.add_argument('--mountpoint', '-m', action='store', default='/mnt/onionpi',
        help='The path to use as a mountpoint')
    imagep.add_argument('--imagesize', '-s', action='store', type=int, default=4096,
        help='The size of the image in megabytes')
    imagep.add_argument('--debianversion', '-d', action='store', default='sid',
        help='The version of debian to user')

    parsed = argparser.parse_args()

    if parsed.verbose:
        logging.basicConfig(level=logging.INFO)

    if parsed.subparser == 'setup':
        setup_host()
    elif parsed.subparser == 'image':
        image = OnionPiImage(imagepath=parsed.imagepath, mountpoint=parsed.mountpoint, 
            imagesize=parsed.imagesize, debianversion=parsed.debianversion)


if __name__ == '__main__':
    sys.exit(main(*sys.argv))
