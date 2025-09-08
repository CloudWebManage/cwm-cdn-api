#!/usr/bin/env bash

set -euo pipefail

gunicorn --print-config cwm_cdn_api.app:app
exec gunicorn cwm_cdn_api.app:app
