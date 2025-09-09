#!/usr/bin/env bash

set -euo pipefail

if [ "${1:-}" == "" ]; then
  gunicorn --print-config cwm_cdn_api.app:app
  exec gunicorn cwm_cdn_api.app:app
else
  exec "$@"
fi
