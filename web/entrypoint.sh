#!/bin/sh
# Turn on TLS only if a certificate has been mounted.
#
# The camera is the reason this exists: browsers expose getUserMedia only in a secure context,
# so a phone reaching the server over the LAN needs HTTPS to capture at all. Without a
# certificate the stack still runs perfectly well over HTTP — the laptop at localhost counts as
# a secure context, so nothing is lost there.
set -e

if [ -s /etc/nginx/certs/server.crt ] && [ -s /etc/nginx/certs/server.key ]; then
    cp /etc/nginx/tls.conf.template /etc/nginx/conf.d/tls.conf
    echo "TLS enabled: certificate found."
else
    rm -f /etc/nginx/conf.d/tls.conf
    echo "TLS disabled: no certificate at /etc/nginx/certs. Run 'make cert' to enable the"
    echo "camera on phones; HTTP is fine on this machine."
fi

exec nginx -g 'daemon off;'
