#!/usr/bin/env python

"""
A RaspSeed sprout that does routes. A sprouter. 
"""

#from raspseed import *
import raspseed

# TODO: these hardcodes aren't good. Not sure how to do better though honestly. Fuck. 
tor_virt_addr = '10.192.0.0'
tor_virt_cidr = '10'
tor_wap_addr = '10.191.0.0'
tor_wap_cidr = '24'
tor_trans_port = 9040
tor_dns_port = 53

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
    raspseed.sh(chroot_cmds, env=chroot_env, chroot=image.mountpoint, chroot_disable_daemons=True)

def route_thru_tor(image, wan_if, tor_ap):
    image.mount_chroot()

    # TODO: we overwrite sysctl.conf and torrc below, because by default they are only comments
    # might be worth writing a function that grabs any non-comment lines and rewrites the file, though, 
    # in case that changes in the future

    torrc = [
        'VirtualAddrNetworkIPv4 {}/{}'.format(tor_virt_addr, tor_virt_cidr),
        'AutomapHostsOnResolve 1',
        'TransPort {}:{}'.format(tor_wap_addr, tor_trans_port),
        'DNSPort {}:{}'.format(tor_wap_addr, tor_dns_port)]
    raspseed.write_file(torrc, image.mountpoint+'/etc/tor/torrc', append=False)

    # I need to run these iptables commands on every boot
    # I think you can add a post-up script to the interface in /etc/network/interfaces ?
    # I'm not sure if I need to enable ipv4 forwarding in sysctl or not, but I don't think so? 
    # TODO: make sure it fails closed: if the script fails for some reason, there should be no internet access. 
    iptables_cmds = [
        'iptables -F'
        'iptables -t nat -F'
        'iptables -t nat -A PREROUTING -i {} -p udp --dport 53 -j REDIRECT --to-ports {}'.format(tor_ap, tor_dns_port)
        'iptables -t nat -A PREROUTING -i {} -p tcp --syn -j REDIRECT --to-ports {}'.format(tor_ap, tor_trans_port)]



def enable_wifi_ap(image, wan_if, wap_if):
    # TODO: how can I know which interface is which without having to do a bunch of system-specific work before I burn the timage? 
    # hmmmmmm
    # Can Linux correspond physical USB ports to logical devices? If so, I should specify an order and tell people that like slot 1 is wan_if and slot 2 is wap_if. Or whatever

    image.mount_chroot()

    chroot_env = {
        'LANG':'C', 
        'DEBIAN_FRONTEND':'noninteractive'}
    chroot_cmds = [
        'apt-get update',
        'apt-get -y install hostapd',
        'apt-get -y install python python-pip'] # required for some of my boot scripts
    raspseed.sh(chroot_cmds, env=chroot_env, chroot=image.mountpoint, chroot_disable_daemons=True)


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


