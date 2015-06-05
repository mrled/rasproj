#!/bin/sh
set -e

rm -f /usr/sbin/policy-rc.d

if dpkg-divert --list | grep 'local diversion of /usr/sbin/invoke-rc.d' >/dev/null; then 
    rm -f /usr/sbin/invoke-rc.d
    dpkg-divert --remove --rename /usr/sbin/invoke-rc.d
fi
