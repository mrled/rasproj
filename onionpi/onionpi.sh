#!/bin/sh

# Problems I'm finding: 
# - Return values vs echo and output. Error checking feels like C, ugh.
# - No associative arrays (or lots of other nice things)
# - Arguments are hard to parse for anything but the very simplest case
# - Probably going to end up with lots of duplicated code

setup_host() {
    host_packages="dosfstools parted kpartx qemu-bootstrap"
    apt-get install $host_packages
}

setup_new_image() {
    imagepath=$1
    mountpath=$2
    imagesize=$3
    [ $imagepath ] || echo "Setting an imagepath is required" >&2; return
    [ $mountpath ] || mountpath="/mnt/onionpi"
    [ $imagesize ] || imagesize=4096

    mkdir -p $mountpath

    # Create the image file
    dd if=/dev/zero of="$imagepath" bs=1M count="$imagesize"
    
    # Partition it to have a FAT partition and an ext4 one
    # This could be automated with sfdisk at some point
    # NOTE: Results in an error "Warning: The resulting partition is not properly aligned for best performance"
    # No idea if this matters on an SD card? I think it's fixable with sfdisk, but that's harder to understand.
    # It's also fixable if you just use fdisk manually. 
    parted "$imagepath" --script -- mklabel msdos
    parted "$imagepath" --script -- mkpart primary fat32 0 64
    parted "$imagepath" --script -- mkpart primary ext4 64 -1

    attach_image "$imagepath" "$mountpath"

    # Make the filesystems
    mkfs.vfat $bootp
    mkfs.ext4 $rootp
}

get_loop_dev() {
    imagepath=$1
    [ $imagepath ] || echo "Setting an imagepath is required" >&2; return 1
    losetup -j "$imagepath" | sed 's/\:.*//' | head -1
}

get_bootp_dev() {
    imagepath=$1
    [ $imagepath ] || echo "Setting an imagepath is required" >&2; return 1
    loopdev_path=`get_loop_dev "$imagepath"`
    loopdev_name=`kpartx -va $loopdev_path | sed -E 's/.*(loop[0-9])p.*/\1/g' | head -1`
    mapperdev="/dev/mapper/${loopdev_name}"
    bootp=${mapperdev}p1
}

attach_image() {
    imagepath=$1
    mountpath=$2
    [ $imagepath ] || echo "Setting an imagepath is required" >&2; return 1
    [ $mountpath ] || echo "Setting a mountpath is required" >&2; return 1

    loopdev_path=`losetup -f "$imagepath"  --show` 
    loopdev_name=`kpartx -va $loopdev_path | sed -E 's/.*(loop[0-9])p.*/\1/g' | head -1`
    mapperdev="/dev/mapper/${loopdev_name}"
    bootp=${mapperdev}p1
    rootp=${mapperdev}p2
}

mount_image() {
}

# Use sjoern's packages to set up the chroot
# (See http://sjoerd.luon.net/posts/2015/02/debian-jessie-on-rpi2/ )
setup_chroot_sjoern() {
    chrootdir=$1
    version=$2
    [ $chrootdir ] || echo "Setting a chrootdir is required" >&2; return 
    [ $version ] || version="sid"

    # If the $chrootdir already exists, assume deboostrap has been run
    [ ! -d $chrootdir ] && qemu-debootstrap --no-check-gpg --arch=armhf $version $chrootdir ftp://ftp.debian.org/debian

    echo > $chrootdir/etc/apt/sources.list <<EOF
deb http://ftp.debian.org/debian $verision main
deb-src http://ftp.debian.org/debian $version main
deb https://repositories.collabora.co.uk/debian jessie rpi2
deb http://ftp.debian.org/debian experimental main
EOF

    echo > $chrootdir/usr/sbin/policy-rc.d <<EOF
echo "************************************" >&2
echo "All rc.d operations denied by policy" >&2
echo "************************************" >&2
exit 101
EOF
    chmod 755 $chrootdir/usr/sbin/policy-rc.d 
}

