#!/bin/sh
set -e

echo "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}..."
python - <<'PY'
import os
import time
import psycopg

host = os.getenv("DB_HOST", "db")
port = os.getenv("DB_PORT", "5432")
name = os.getenv("DB_NAME", "sakukoperasi_db")
user = os.getenv("DB_USER", "postgres")
password = os.getenv("DB_PASSWORD", "postgres")

dsn = f"dbname={name} user={user} password={password} host={host} port={port}"

for attempt in range(30):
    try:
        with psycopg.connect(dsn):
            print("PostgreSQL is ready")
            break
    except Exception as exc:
        print(f"Attempt {attempt + 1}/30 failed: {exc}")
        time.sleep(1)
else:
    raise SystemExit("PostgreSQL is not reachable after 30 attempts")
PY

python manage.py migrate --noinput

if [ "${AUTO_SEED_ADMIN:-true}" = "true" ]; then
    python manage.py seed_default_admin --if-not-exists
fi

if [ "${DJANGO_DEBUG:-false}" = "true" ]; then
    # Development: auto-reload kode dari volume mount.
    exec python manage.py runserver 0.0.0.0:8000
fi

python manage.py collectstatic --noinput
exec gunicorn sakukoperasi.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --access-logfile -
