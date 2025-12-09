#!/usr/bin/env bash
set -euo pipefail

for f in /docker-entrypoint.d/*; do
    case "$f" in
        *.sh)     echo "running $f"; . "$f" ;;
        *.py)     echo "running $f"; python3 "$f" ;;
    esac
    echo OK
done

python3 /srv/render_nginx_conf.py

if [ "${ENABLE_TENANT_ACCESS_LOGS:-}" == "true" ]; then
  mkdir -p /etc/vector /var/log/nginx
  python3 /srv/render_vector_config.py > /etc/vector/vector.yaml
  chmod 600 /srv/logrotate.conf
  exec /usr/bin/supervisord -n -c /srv/supervisord.conf
else
  exec openresty -g "daemon off;"
fi
