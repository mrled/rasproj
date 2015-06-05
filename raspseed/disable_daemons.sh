#!/bin/sh
set -e

echo -e '#!/bin/sh\nexit 101' > /usr/sbin/policy-rc.d 
chmod 755 /usr/sbin/policy-rc.d

if ! dpkg-divert --list | grep 'local diversion of /usr/sbin/invoke-rc.d' >/dev/null; then 
    dpkg-divert --add --local --divert /usr/sbin/invoke-rc.d.chroot --rename /usr/sbin/invoke-rc.d
    cp /bin/true /usr/sbin/invoke-rc.d
fi

