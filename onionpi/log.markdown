# Onion Pi project

## Set up the chroot

In this, my host machine is called "susan", which has a large disk mounted to /chroots. On the host: 

    # Install packages required on the host: 
    apt-get install dosfstools parted kpartx

    # Create the image file
    dd if=/dev/zero of=/chroots/rpi.img bs=1M count=8192
    
    # Partition it to have a FAT partition and an ext4 one
    # This could be automated with sfdisk at some point
    # NOTE: Results in an error "Warning: The resulting partition is not properly aligned for best performance"
    # No idea if this matters on an SD card? I think it's fixable with sfdisk, but that's harder to understand.
    # It's also fixable if you just use fdisk manually. 
    parted /chroots/rpi.img --script -- mklabel msdos
    parted /chroots/rpi.img --script -- mkpart primary fat32 0 64
    parted /chroots/rpi.img --script -- mkpart primary ext4 64 -1

    # Mount the disk image as a loopback disk so you can create filesystems on its partitions: 
    # "-f" finds the first available loop. For the rest, I'll assume this was loop0
    loopdev_path=`losetup -f --show /chroots/rpi.img` 
    loopdev_name=`kpartx -va $loopdev_path| sed -E 's/.*(loop[0-9])p.*/\1/g' | head -1`
    mapperdev="/dev/mapper/${loopdev_name}"
    bootp=${mapperdev}p1
    rootp=${mapperdev}p2

    # Make the filesystems
    mkfs.vfat $bootp
    mkfs.ext4 $rootp
    
At the end, you'll need to unmount everything and remove the loopback device and mapped partitions. But don't do this yet obvi: 

    umount $bootp
    umount $rootp
    kpartx -dv $loopdev
    losetup -d $loopdev

- Create an image file
- Partition it to have a FAT partition and an ext4 one
- setup armhf chroot on the ext4 partition: https://wiki.debian.org/ArmHardFloatChroot
- particularly note the instructions at the bottom: run `apt-get update`, install `debian-archive-keyring`, then run `apt-get update` again

In the chroot: 

    # Per the instructions at the very bottom of the ArmHardFloatChroot page:
    apt-get update
    apt-get install debian-archive-keyring
    apt-get update
    
    # Required to fix locale errors: 
    apt-get install locales
    dpkg-reconfigure locales
    
At this point, `dpkg-reconfigure` will ask you to select a locale. I use `en_US.UTF-8`. A good test to make sure that this solved any locale issues is to run `perl -v`; if it doesn't complain about locale issues, you're OK. 

    # Install build packages
    apt-get install build-essential
    


## Get Pi-specific stuff

See here: http://sjoerd.luon.net/posts/2015/02/debian-jessie-on-rpi2/

- There are just a few packages up there: 
    - A keyring package 
    - A package containing a compiled Linux kernel
    - A flash-kernel package from debian experimental + rpi2 changes
    - raspberrypi-firmware-nokernel, which contains the binary blob  needed to boot
    - libraspberrypi{0,-bin,-dev,-doc} - not actually sure about these? 
    
For my first pass, I'm going to use those binaries, but eventually I want to have scripts that can compile them for me. 

### To use the binaries provided by sjoerd

His instructions say to add his package repo, which is true, but first you have to do some stuff. In the chroot: 

    # Install the HTTPS transport for APT
    apt-get install apt-transport-https  
    
    # Add the repository referenced on that page & update
    vi /etc/sources.list

Note that at this point, his instructions are slightly out of date compared to how he configured his debian packages webserver, and we also have to add an experimental repository to get the `linux-kbuild-3.18` package, which is a dependency of the Linux kernel package he prepared. 

My sources.list ended up looking like this: 

    # These were preexisting from the ArmHardFloatChroot page on the Debian wiki: 
    deb http://ftp.debian.org/debian sid main
    deb-src http://ftp.debian.org/debian sid main
    
    # This is the correct line to get his packages:
    deb https://repositories.collabora.co.uk/debian jessie rpi2
    
    # This is necessary for linux-kbuild-3.18 and maybe other stuff too idk:
    deb http://ftp.debian.org/debian experimental main
    
Now continue in the chroot: 
    
    # Update to get his packages
    apt-get update
    
    # Install his keyring so you can verify his packages
    # (This package is not verifiable, however, which is why we use --force-yes)
    # ... looks like not all the packages were signed with this?  I have no idea. Whatever.
    # You may need to add the -y --force-yes to every apt-get that deals with his repository
    apt-get install collabora-obs-archive-keyring -y --force-yes
    
    # Now install the other packages
    apt-get install libraspberrypi0 libraspberrypi-doc libraspberrypi-dev libraspberrypi-bin
    apt-get install raspberrypi-bootloader-nokernel flash-kernel
    apt-get install linux-image-3.18.0-trunk-rpi2 linux-headers-3.18.0-trunk-rpi2
    

### To create my own packages from pristine upstream

- See official raspberry pi stuff: https://github.com/raspberrypi
- Make packages that cover all the shit sjoern did
- Make packages / custom scripts to handle all the things I need

