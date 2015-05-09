#!/usr/bin/env python

"""
A RaspSeed sprout that does routes. A sprouter. 
"""

#from raspseed import *
import raspseed

def install_tor(image):
    image.mount_chroot()

    sources_list = [
        'deb http://deb.torproject.org/torproject.org jessie main',
        'deb-src http://deb.torproject.org/torproject.org jessie main']
    raspseed.write_file(
        sources_list, image.mountpoint+'/etc/apt/sources.list',
        append=True, uniqueonly=True)

    chroot_env = {
        'LANG':'C', 
        'DEBIAN_FRONTEND':'noninteractive'}
    chroot_cmds = [
        'gpg --keyserver keys.gnupg.net --recv 886DDD89',
        'gpg --export A3C4F0F979CAA22CDBA8F512EE8CBC9E886DDD89 | apt-key add -',
        'apt-get update',
        'apt-get -y install tor deb.torproject.org-keyring tor-geoipdb']
    raspseed.sh(chroot_cmds, chroot=image.mountpoint, env=chroot_env)

def route_thru_tor(image, wan_if, tor_ap):
    image.mount_chroot()
    

def add_tor_router(image, wan_if, tor_ap):
    install_tor(image)

def find_subparser(name, argparser):
    '''Find a subparser by name, given an argparser object'''
    for sp in argparser._subparsers._actions:
        if sp.choices[name]:
            return sp
    raise Exception('Could not find a subparser called {}'.format(name))

def sprout_main(*args):

    # get an argparse object
    parser = raspseed.get_argparser(*args)

    # add the new args i want
    images = find_subparser('image', parser)
    images.add_argument(
        '--egress-interface', '-e', action='store',
        help='The outbound interface')
    images.add_argument(
        '--ingress-interface', '-e', action='store',
        help='The wifi interface to use for a Tor-only access point')

    parsed = parser.parse_args()
    raspseed.execute(parsed)

    if parsed.subparser == 'image':
        

sprout_execution = {
    'image': customize_sprout_image
    }
#raspseed.execution['image'].append(customize_sprout_image)

if __name__ == '__main__':
    sys.exit(sprout_main(*sys.argv))






# TODO 1: 
# handle wifi interfaces
# start Tor
# block all outbound traffic except Tor traffic, ntpdate/tlsdate

# TODO 2: 
# figure out multiple wifi interfaces 


