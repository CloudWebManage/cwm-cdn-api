#!/bin/bash
set -euo pipefail

STATEFILE=/var/lib/logrotate/nginx.status
CONF=/srv/logrotate.conf

mkdir -p "$(dirname "$STATEFILE")"

while true; do
    /usr/sbin/logrotate -s "$STATEFILE" "$CONF"
    sleep 300
done
