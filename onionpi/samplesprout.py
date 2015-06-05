#!/usr/bin/env python

"""
A sample RaspSeed sprout

This allows you to call the sprout and pass all it all of the arguments for RaspSeed. 
"""

import raspseed

def setup_sprout_host():
    """
    Execute this function after setting up the host with 'raspseed.py setup'
    """
    raspseed.sh('apt-get install SOMETHING -y')

def customize_sprout_image(image):
    """
    Execute this function after creating a bootable image with 'raspseed.py image'

    The RaspSeedImage object is passed in as 'image', so you might examine the mounts, for instance, with image.mounts within this function.
    """

    # RaspSeedImage's mount_chroot() method is idempotent
    image.mount_chroot()

    chroot_env = {
        'LANG':'C', 
        'DEBIAN_FRONTEND':'noninteractive'}
    chroot_cmds = [
        'echo "this command is executed inside the chroot"',
        'uname -a',
        'mount']
    raspseed.sh(chroot_cmds, chroot=image.mountpoint, env=chroot_env)


imgargs = [
    { args: ['--xample', '-x'], kwargs: {action:store_true} },
    { args: ['--yample', '-y'], kwargs: {action:store_false} }]
    

def sprout_main(*args):

    # host_arguments / image_arguments should each be lists of dictionaries which contain 'args' and 'kwargs' keys
    # (as imgargs is defined above)
    args = raspseed.parse(
        host_arguments = None,
        image_arguments = imgargs,
        *args)
    parsed = args.parse_args()

    raspseed.execute(
        parsed,
        post_setup = setup_sprout_host,
        post_image = customize_sprout_image)

if __name__ == '__main__':
    sys.exit(sprout_main(*sys.argv))

