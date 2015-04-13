# Development Notes

## References & inspiration

 -  The Kali Linux script for generating a Raspberry Pi image was very helpful
    <https://github.com/offensive-security/kali-arm-build-scripts/blob/master/rpi2.sh>
 -  The Debian Wiki page for creating an armhf chroot was also useful
    <https://wiki.debian.org/ArmHardFloatChroot>

## Important TODO items

- Align the partitions for better performance
    - https://www.raspberrypi.org/forums/viewtopic.php?t=11258&p=123670
    - Maybe this is not important for my use, where almost all the time the image will be mounted ro
    - Even where it is important, note that this has to be done *per SD card*, and not per image
    - ... which means it would have to be in a script that runs at boot time, not at image creation time
    - This means its fine to use `parted` (which does not align blocks) during image creation time
    - but we'd have to use `sfdisk` (which can align blocks, if you do some maths yourself) in the boot script

## Less important TODO items

- The OnionPi name is already taken and is boring and would taste gross

## Specific issues & decisions

A logbook of cargo-cult-ish-ness

Notes about specific problems I overcame and why certain decisions were made

### Python 2 requirement

All code should work with Python 2.7. 

Ideally, it should also work with Python 3.x, but I chose 2.7 because people are more likely to have it lying around

### debootstrap in stages

Kali does debootstrap in 2 stages ("the Kali way"): 

    # ... snip ...
    debootstrap --foreign --arch $architecture kali kali-$architecture http://$mirror/kali
    # ... snip ...
    LANG=C chroot kali-$architecture /debootstrap/debootstrap --second-stage
    # ... snip ... 
    # there's also a final stage, where apt-get is called directly from the chroot

The Debian wiki page for armhf does the first two in one step ("the `qemu-deboostrap` way"): 

    qemu-debootstrap --no-check-gpg --arch=armhf sid /chroots/sid-armhf ftp://ftp.debian.org/debian/
    
If you watch the output of the `qemu-deboostrap` command, it claims to invoke both of the commands that make up steps 1 and 2 in the Kali way.

However, when I did it the Kali way, I was getting errors where you would try to chroot, but it would give you a permission denied error: 

    > chroot /mnt/onionpi /bin/sh
    chroot: permission denied: /bin/sh

I couldn't figure out why this was happening. The filesystems were mounted `exec`, `dev` and `proc` were mounted, and I was in a root shell. Switching to the `qemu-bootstrap` method seems to have fixed it. 

I had also seen `udev` mount itself to `/mnt/onionpi/dev`, on top of the `dev` bind mount which I explicitly do in my build script. I had thought that perhaps doing things the Kali way was causing this, but I had contaminated my build environment and couldn't be sure. Now it looks like only `udev` is being mounted there. Perhaps that's the actual cause of this problem? 

### Using sjoerd's binaries

The whole point of this project is to try to create my own set of scripts for an armhf debian distribution. 

However, sjoerd has written his own, and compiled binaries for use. They are available here: <http://sjoerd.luon.net/posts/2015/02/debian-jessie-on-rpi2/>



