# Development Notes

## References & inspiration

 -  The Kali Linux script for generating a Raspberry Pi image was very helpful
    <https://github.com/offensive-security/kali-arm-build-scripts/blob/master/rpi2.sh>
 -  The Debian Wiki page for creating an armhf chroot was also useful
    <https://wiki.debian.org/ArmHardFloatChroot>
 -  Ubuntu build script might be useful to keep around too: 
    <http://www.finnie.org/software/raspberrypi/rpi2-build-image.sh>

## Important TODO items

- Align the partitions for better performance
    - https://www.raspberrypi.org/forums/viewtopic.php?t=11258&p=123670
    - Maybe this is not important for my use, where almost all the time the image will be mounted ro
    - Even where it is important, note that this has to be done *per SD card*, and not per image
    - ... which means it would have to be in a script that runs at boot time, not at image creation time
    - This means its fine to use `parted` (which does not align blocks) during image creation time
    - but we'd have to use `sfdisk` (which can align blocks, if you do some maths yourself) in the boot script
- Handle downloading external dependencies better
    - I need Linux sources, Pi firmware, and (possibly) raspberrypi/tools (a compiled linaro toolchain)
    - I wouldn't need the Git history on some/most/all of these. If I clone with --depth=1, is it updateable? Oh shit, I think you can just `git pull` to update it
- Support for different kernels
    - Can't tell whether there is mainline kernel support for RPi or not. 
    - Looks like it works for at least some stuff, and fails on some other stuff. I don't care about the GPU though! So maybe. 
        - http://wiki.beyondlogic.org/index.php?title=Raspberry_Pi_Building_Mainline_Kernel
    - If there's a god damned motha fuckin precompiled kernel somewhere I could use that'd be hella rad
    - (I'm having trouble with the sjoerd one)
- Parallellize
    - I could download the GH repos, any other source I need, and dd the image, all at the same time
- Banana Pi support
- Set GPU memory to the minimum allowed. I think it's 16MB, while default is 64MB. 
- Don't wait to bring up network before offering login over serial

## Less important TODO items

- I fucking cannot get fucking locales to fucking work fucking noninteractively. I keep having to fucking `dpkg-reconfigure locales` and fucking manually fucking select the fucking locale.
    - Maybe this will help. i don't fuckin know though. http://www.debian-administration.org/article/394/Automating_new_Debian_installations_with_preseeding
    - This fucking didn't fucking help: http://stackoverflow.com/questions/8671308/non-interactive-method-for-dpkg-reconfigure
- The deboostrap steps I use result in some fucked up characters getting written to the serial console during boot. Still works to log in but it looks ugly.
    
## Experiments / troubleshooting

- Notes on using the serial console w/ systemd - is this really necessary? http://linux-sunxi.org/Mainline_Debian_HowTo#systemd
    - Also found this, but currently my chroots don't have that /usr/lib/systemd directory http://stackoverflow.com/questions/21596384/cannot-disable-systemd-serial-getty-service

## Specific issues & decisions

A logbook of cargo-cult-ish-ness

Notes about specific problems I overcame and why certain decisions were made

### Using debconf

Run `debconf-get-selections` to see all the possible customizations in any installed package. 

If you want to change the default, you can create a file to place your customizations in, call `debconf-set-selections` with input redirected to that file, and then run `DEBIAN_FRONTEND=noninteractive apt-get ...`.

### Not using flash-kernel with sjoerd's build

In sjoerd's repository, there is a flash-kernel package, forked from the official debian one. I think (but I haven't verified) that the only difference between them is in the `/usr/share/flash-kernel/db/all.db` file, which has this section in sjoerd's package:

    Machine: Raspberry pi 2 Model B
    Machine: BCM2709
    Kernel-Flavors: rpi2
    Boot-Kernel-Path: /boot/firmware/kernel7.img
    
NOTE: At least if you're doing this in a chroot, you need to specify the machine name for flash-kernel: `echo "Raspberry pi 2 Model B" > /etc/flash-kernel/machine`

1) I think this could have been placed in `/etc/flash-kernel/db`, rather than having a whole new package. Maybe? Not sure. 
2) I can't get the fucking thing to work. 
    - I have tried it straight from sjoerd's repo
    - I've also tried the official version in the debian repos, with the stanza above added to /etc/flash-kernel/db
    - In both cases, it doesn't report any errors, but I get no kernel in /boot or /boot/firmware
3) It's honestly not necessary if you're willing to copy the kernels to the right place every time

So that's what I'm doing. Fuck reading shell scripts (SHELL SCRIPTS) in 2015 trying to figure out why they aren't doing what you want. I referred once again to Kali's script.

Other notes about flash-kernel: 

- If you wanna try messing with flash-kernel packages available from different repositories, you can use `apt-cache policy flash-kernel` to see what's available
- You can install a specific version w/ an equals sign, like this: `apt-get install flash-kernel=3.33.co1+b1`
- If you wanna make sure that never changes, you could probably do something with apt-pinning or smth, but you can also use apt-mark to hold it at a specific version: `apt-mark hold flash-kernel`

Some old code I probably don't need:

        makedirs(self.mountpoint+'/etc/flash-kernel', exist_ok=True)
        write_file("Raspberry pi 2 Model B", self.mountpoint+'/etc/flash-kernel/machine', append=False, mode=0o644)

        fkdb_contents = [
            'Machine: Raspberry pi 2 Model B',
            'Machine: BCM2709',
            'Kernel-Flavors: rpi2',
            'Boot-Kernel-Path: /boot/firmware/kernel7.img']
        makedirs(self.mountpoint+'/etc/flash-kernel/db', exist_ok=True, mode=0o755)
        write_file(fkdb_contents, fkdb, append=False, mod=0o644)


Some notes about booting on the Pi in general (don't have a better place for these atm):

- Some people use U-Boot, some people boot kernels directly. U-boot can do tftp I guess. And other stuff. It's the only way to boot from a ramdisk. Not sure I care about that.
- On the Pi 1 models, the GPU will boot from `/kernel.img` at the root of the first partition (which it assumes is FAT32) 
- On the Pi 2 models, the GPU will boot from `/kernel7.img` at the root of the first partition (which it assumes is FAT32)

### Building your own kernel

I don't have this working yet

- Looks like recent kernels use a "device tree", which means that if you compile it yourself, you have to copy a (some?) .dtb file(s) to your /boot/firmware partition
- However, the sjoerd kernel package doesn't seem to include any of these. I believe that when it says that the patchset he got from the Raspberry Pi repository "isn't multiplatform capable", this is what it's referring to.

### cmdline.txt

- Console during boot: 
    - `console=ttyAMA0,115200` will spit out the boot messages over serial
    - `console=tty1` will spit out the boot messages over HDMI/VGA
    - You can have both of these at the same time, just one or the other, or neither. 

### Linux kernel versions

I'd like to be able to provide options

- Use sjoerd's kernel. This isn't working for me currently, idk why
- Use the mainline kernel. I *think* this is possible (see above in the TODO section). 
    - Based on this: <https://www.kernel.org/releases.html>, I'm going to be attempting the 3.18.x series mainline kernels
- Compile the RPI Foundation's kernel myself
- ... if there's a better option than that fuckin lmk

### Trying to do sjoerd

most of this is documented in the script itself

i expect this section to go away once i get all the problems ironed out, but for now, it's gonna hold my working state

currently having this problem: 

    root@susan:~# apt-get install -y --force-yes flash-kernel=3.33.co1+b1 raspberrypi-bootloader-nokernel linux-image-3.18.0-trunk-rpi2 linux-headers-3.18.0-trunk-rpi2
    Reading package lists... Done
    Building dependency tree
    Reading state information... Done
    linux-headers-3.18.0-trunk-rpi2 is already the newest version.
    raspberrypi-bootloader-nokernel is already the newest version.
    The following extra packages will be installed:
      initramfs-tools
    Suggested packages:
      bash-completion linux-doc-3.18 debian-kernel-handbook fdutils
      The following NEW packages will be installed:
        flash-kernel initramfs-tools linux-image-3.18.0-trunk-rpi2
    0 upgraded, 3 newly installed, 0 to remove and 0 not upgraded.
    Need to get 0 B/17.3 MB of archives.
    After this operation, 66.2 MB of additional disk space will be used.
    Preconfiguring packages ...
    Selecting previously unselected package initramfs-tools.
    (Reading database ... 26377 files and directories currently installed.)
    Preparing to unpack .../initramfs-tools_0.119_all.deb ...
    Unpacking initramfs-tools (0.119) ...
    Selecting previously unselected package linux-image-3.18.0-trunk-rpi2.
    Preparing to unpack .../linux-image-3.18.0-trunk-rpi2_3.18.5-1~exp1.co1+b1_armhf.deb ...
    Unpacking linux-image-3.18.0-trunk-rpi2 (3.18.5-1~exp1.co1+b1) ...
    Selecting previously unselected package flash-kernel.
    Preparing to unpack .../flash-kernel_3.33.co1+b1_armhf.deb ...
    Unpacking flash-kernel (3.33.co1+b1) ...
    Processing triggers for man-db (2.7.0.2-5) ...
    Setting up initramfs-tools (0.119) ...
    update-initramfs: deferring update (trigger activated)
    Setting up linux-image-3.18.0-trunk-rpi2 (3.18.5-1~exp1.co1+b1) ...
    /etc/kernel/postinst.d/initramfs-tools:
    update-initramfs: Generating /boot/initrd.img-3.18.0-trunk-rpi2
    Warning: root device  does not exist
    Setting up flash-kernel (3.33.co1+b1) ...
          
    Creating config file /etc/default/flash-kernel with new version
    Processing triggers for initramfs-tools (0.119) ...
    update-initramfs: Generating /boot/initrd.img-3.18.0-trunk-rpi2
    Warning: root device  does not exist
    Unsupported platform.
    run-parts: /etc/initramfs/post-update.d//flash-kernel exited with return code 1
    dpkg: error processing package initramfs-tools (--configure):
     subprocess installed post-installation script returned error exit status 1
    Errors were encountered while processing:
     initramfs-tools
    E: Sub-process /usr/bin/dpkg returned an error code (1)
        

Actually I think maybe you can ignore this? Hmm.

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

    > chroot /mnt/raspseed /bin/sh
    chroot: permission denied: /bin/sh

I couldn't figure out why this was happening. The filesystems were mounted `exec`, `dev` and `proc` were mounted, and I was in a root shell. Switching to the `qemu-bootstrap` method seems to have fixed it. 

I had also seen `udev` mount itself to `/mnt/raspseed/dev`, on top of the `dev` bind mount which I explicitly do in my build script. I had thought that perhaps doing things the Kali way was causing this, but I had contaminated my build environment and couldn't be sure. Now it looks like only `udev` is being mounted there. Perhaps that's the actual cause of this problem? 

### Using sjoerd's binaries

The whole point of this project is to try to create my own set of scripts for an armhf debian distribution. 

However, sjoerd has written his own, and compiled binaries for use. They are available here: <http://sjoerd.luon.net/posts/2015/02/debian-jessie-on-rpi2/>



