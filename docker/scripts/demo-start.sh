#!/usr/bin/env bash
# Entrypoint for the demo container.
# Applies any pending migrations then starts the dev server.
set -euo pipefail

echo "==> Applying migrations"
python3 manage.py migrate --run-syncdb

echo "==> Starting demo at http://0.0.0.0:8000/"
exec python3 manage.py runserver 0.0.0.0:8000
