#!/usr/bin/env sh
echo "${CONTENT}" > /usr/share/nginx/html/index.html
exec nginx -g 'daemon off;'
