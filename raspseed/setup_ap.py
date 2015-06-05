#!/usr/bin/env python

import subprocess
import re
import os

class NetworkInterface(object):
    def __init__(self, ifdef):
        self.ifdev = ifdev
        #self.medium = medium
        #self.freqs = freqs

    @property
    def iwphy(self):
        '''Get a hostapd physical device name (like 'phy3') from a Linux device name (like 'wlan0')'''
        # If this was determined previously, it was cached; use the cache
        try:
            return self.__iwphy
        except AttributeError:
            iwdev = subprocess.check_output('iw dev {} info'.format(self.ifdev))
            for line in iwdev.split('\n'):
                m = re.match('\s+wiphy (\d+)', line)
                if m:
                    phynum = m.group(1)
                    phy = "phy{}".format(phynum)
                    self.__iwphy = phy
                    return phy
            raise Exception("Could not find a physical device name - is this really a wifi adapter?")

    @property
    def ap(self):
        '''
        Determine whether a hostapd physical device is capable of hosting an access point
        We're not supposed to screen scrape iw, but the nl80211 API is inscrutable from the outside so fuck it
        '''
        # If this was determined previously, it was cached; use the cache
        try:
            return self.__ap
        except AttributeError:
            iwinfo = subprocess.check_output('iw phy {} info'.format(self.iwphy))
            for line in iwinfo.split('\n'):
                if re.match('^\s+\* AP$', iwinfo):
                    self.__ap = True
                    return True
            self.__ap = False
            return False

    @property
    def sysfsinfo(self):
        """
        """
        # If this was determined previously, it was cached; use the cache
        try:
            return self.__sysfsinfo
        except AttributeError:
            sysfs_symlink = '/sys/class/net/{}'.format(self.ifdev)
            if not os.path.exists(sysfs_symlink):
                raise Exception(
                    "Could not find device at {}".format(sysfs_symlink))
            # this will result in a path lik: 
            # /sys/devices/platform/bcm2708_usb/usb1/1-1/1-1.5/1-1.5:1.0/net/wlan0/
            sysfs_resolved = os.path.realpath(sysfs_symlink)
            sysfs_components = split_path(sysfs_resolved)
            if (    # This is just to sanity check:
                    sysfs_components[0]  != "sys" or 
                    sysfs_components[1]  != "devices" or
                    sysfs_components[-1] != os.path.split(sysfs_symlink)[1]):
                raise Exception(
                    "sysfs symlink resolved to {} - looks wrong".format(
                        sysfs_resolved))
            businfo = sysfs_components[7] # something like "1-1.5:1.0"

def parse_usb_sysfs_directory(dirname):
    """
    Get information out of sysfs
    (See also: http://tiebing.blogspot.com/2014/07/linux-usb-sysfs-device-naming-scheme.html )
    Expect 'dirname' to be something like "1-1.5:1.0"
    """
    prefix, suffix = dirname.split(':')

    config, interface = suffix.split('.')

    
    

def split_path(path):
    allcomponents = path.split('/')
    components = []
    for c in allcomponents: # trim empty components at beginning/end
        if c != "": 
            components += [c]
    return components

def enumerate_interfaces():
    ifconfig = subprocess.check_output(
        'ifconfig -a', shell=True, env={ 'LANG': 'C' })
    interfaces = ()
    for line in ifconfig.split('\n'):
        match = re.search('^(\w+)', line)
        if match:
            interfaces.append(NetworkInterface(match.group(1)))
    return interfaces

def determine_ap():

    def find_interfaces():
        env = { 'LANG': 'C' }
        ifconfig = subprocess.check_output('ifconfig -a', shell=True, env=env)
        interfaces = {
            'eths': [],
            'wlans': []}
        for line in ifconfig.split('\n'):
            # find all network adapters except 'lo'
            e = re.search('^(eth\d+)', line)
            w = re.search('^(wlan\d+)', line)
            if e: 
                interfaces['eths'].append(e.group(1))
            elif w:
                interfaces['wlans'].append(w.group(1))
        return interfaces

    ap_interface = None
    for iw in find_interfaces()['wlans']:
        phy = phy_from_dev(iw)
        if phy_is_ap_capable(phy):
            ap_interface = iw
            break

    return ap_interface

def enable_ap(interface, ssid='sprouter-tor', hw_mode='g'):
    hostapdconf = [
        'interface={}'.format(interface),
        'ssid={}'.format(ssid),
        'hw_mode={}'.format(hw_mode),
        'channel=0',
        'macaddr_acl=0',
        'auth_algs=1',
        'ignore_broadcast_ssid=0',
        'wpa=2',
        'wpa_passphrase=Raspberry',
        'wpa_key_mgmt=WPA-PSK',
        'wpa_pairwise=TKIP',
        'rsn_pairwise=CCMP']
