## Run Project

Activate virtual environment:

```bash
source venv/bin/activate
```

Start server:

```bash
cd sakukoperasi
python manage.py runserver
```

## Docker Setup (Django + PostgreSQL)

1. Copy environment template, lalu isi `DJANGO_SECRET_KEY` dan `DEFAULT_ADMIN_PASSWORD`:

```bash
cp .env.example .env
```

- `DJANGO_DEBUG=true` → mode development (`runserver`, auto-reload).
- `DJANGO_DEBUG=false` → mode production (`gunicorn` + static via whitenoise); `DJANGO_SECRET_KEY` wajib diisi.

2. Build and start containers:

```bash
docker-compose up --build
```

3. Open API/app:

```text
http://localhost:8000
```

Useful commands:

```bash
# stop containers
docker-compose down

# stop and remove database volume
docker-compose down -v

# run Django command inside web container
docker-compose exec web python manage.py createsuperuser
```

## Docker Shortcut: tahu

This project containt script `tahu` for important shortcut for docker.

1. Grant permission to execute (once time):

```bash
chmod +x tahu
```

2. Run Command:

```bash
./tahu install
./tahu up
./tahu migrate
./tahu logs
```

Important actions:

```text
up         Build and start containers
down       Stop and remove containers
reset      Stop containers and remove volumes
logs       Show service logs
ps         Show service status
install    Install Docker + Compose (Ubuntu/Debian/Arch/CachyOS)
migrate    Run Django migrate
check      Run Django check
shell      Open Django shell
superuser  Create Django superuser
dump       Export data to sakukoperasi/data.json
loaddata   Import sakukoperasi/data.json
```

Note: `./tahu install` need root access/sudo

## PostgreSQL Configuration

This project now uses PostgreSQL instead of SQLite.

Set these environment variables before running the app:

```bash
export DB_NAME=sakukoperasi_db
export DB_USER=postgres
export DB_PASSWORD=postgres
export DB_HOST=localhost
export DB_PORT=5432
```

Install PostgreSQL driver (already installed in this workspace):

```bash
pip install "psycopg[binary]"
```

## Migrate Existing SQLite Data to PostgreSQL

From folder `sakukoperasi/`:

1. Export data from SQLite:

```bash
python manage.py dumpdata --exclude auth.permission --exclude contenttypes > data.json
```

2. Make sure PostgreSQL server is running and database exists:

```bash
createdb -U "$DB_USER" "$DB_NAME"
```

3. Run migrations on PostgreSQL:

```bash
python manage.py migrate
```

4. Import old data into PostgreSQL:

```bash
python manage.py loaddata data.json
```

## Seeder Default Admin

From folder `sakukoperasi/`:

```bash
python manage.py seed_default_admin
```

Tidak ada password bawaan. Password diambil dari `--password` atau `DEFAULT_ADMIN_PASSWORD`,
dan harus lolos validasi password Django (minimal 8 karakter, tidak umum, tidak hanya angka).

Custom credential example:

```bash
python manage.py seed_default_admin --username superadmin --password SuperSecure123 --email superadmin@example.com
```

Automatic seeder on container startup:

1. Docker entrypoint runs migrate, then auto-seeds admin.
2. Auto-seed uses safe mode (`--if-not-exists`), so existing admin is not overwritten.
3. Jika `DEFAULT_ADMIN_PASSWORD` kosong, auto-seed dilewati (buat manual dengan `./tahu superuser`).

Environment variables for auto-seed (`.env`):

```bash
AUTO_SEED_ADMIN=true
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=<password-kuat>
DEFAULT_ADMIN_EMAIL=admin@sakukoperasi.local
```

## Autentikasi API

Semua endpoint `/api/` wajib login. Hak tulis (tambah/ubah/hapus) mengikuti permission model
Django yang diatur per user/group di admin.

```bash
# Ambil token
curl -X POST http://localhost:8000/api/auth/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "<password>"}'

# Pakai token
curl http://localhost:8000/api/members/ -H 'Authorization: Token <token>'
```
