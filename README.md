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

## Aturan Pinjaman

| Aturan | Mingguan | Bulanan |
|---|---|---|
| Tarif | Tabel tarif (diisi via admin) | Tabel tarif (`seed_loan_rules`) |
| Jaminan | Opsional | Wajib, milik anggota yang sama |
| Biaya admin | Dipotong saat pencairan | Dipotong saat pencairan |
| Jatuh tempo | Tiap 7 hari dari tanggal pinjam | Tanggal yang sama tiap bulan |
| Pinjaman aktif | Maks 1 per anggota | Maks 1 per anggota |
| Nomor pinjaman | ID Mingguan anggota (3 digit, mis. `007`) | ID Bulanan anggota (4 digit, mis. `0012`) |

- Pinjam lagi setelah lunas memakai nomor yang sama, dibedakan dengan urutan **pinjaman ke-n** (`pinjaman_ke` di API).
- Pembayaran bebas nominal, tidak bisa melebihi sisa pinjaman, dan tidak bisa diubah/dihapus.
- **Telat**: total dibayar < angsuran × jumlah periode yang sudah jatuh tempo.
- **Denda** diinput manual oleh petugas (nominal + alasan) dan dibayar terpisah.
- **Lunas**: sisa pinjaman 0 dan tidak ada denda yang belum dibayar.
- Jaminan tidak boleh dipakai di dua pinjaman yang belum lunas.
- Anggota yang punya data keuangan tidak bisa dihapus; gunakan **nonaktifkan** (anggota nonaktif tidak bisa mengajukan pinjaman baru).

Status telat bergantung pada tanggal, jadi jalankan pembaruan status setiap hari (cron di host):

```bash
# crontab -e
5 0 * * * cd /path/to/SakuKoperasi && ./tahu refresh_loan_status
```

### Endpoint API

| Endpoint | Keterangan |
|---|---|
| `/api/members/` | Anggota; `POST /api/members/{id}/nonaktifkan/` |
| `/api/jaminan/` | Jaminan anggota |
| `/api/pinjaman-mingguan/` | Pinjaman mingguan |
| `/api/pinjaman-bulanan/` | Pinjaman bulanan |
| `/api/pembayaran-pinjaman/` | Pembayaran (create/list, filter `?loan=<id>`) |
| `/api/denda-pinjaman/` | Denda (create/list, `PATCH` hanya `is_paid`) |

## Simpanan Sukarela

- Rekening dibuka manual oleh petugas untuk **nasabah** (anggota maupun non-anggota); tidak otomatis saat anggota didaftarkan.
- **Nomor rekening** diisi manual (mis. sesuai buku tabungan) dan harus unik. Satu nasabah satu rekening per jenis simpanan.
- Rekening/transaksi dicari dengan **nama nasabah**, nomor rekening, telepon, atau ID anggota (admin: kotak cari & autocomplete; API: `?search=`).
- Setor/tarik bebas kapan saja. Penarikan dicek terhadap saldo dan **saldo minimum mengendap** (diatur di *Jenis Simpanan*, default 0).
- Tanggal transaksi tidak boleh di masa depan, sebelum rekening dibuka, atau sebelum transaksi terakhir.
- Transaksi tidak dapat diubah/dihapus; rekening **ditutup** (saldo harus 0), bukan dihapus.
- Akun tabungan lama hasil migrasi bernomor `LAMA-<id>` — ganti dengan nomor buku tabungan di admin.

### Bunga simpanan

Aturan bunga diatur di admin **Aturan Bunga Simpanan** dan berversi berdasarkan *Berlaku Mulai*:

| Field | Keterangan |
|---|---|
| Bunga (% per tahun) | Persentase tahunan |
| Dasar Perhitungan | Saldo terendah / saldo akhir / rata-rata saldo harian dalam periode |
| Saldo Minimum Dapat Bunga | Di bawah nilai ini tidak mendapat bunga |

Bunga per bulan = saldo dasar × %/tahun × jumlah hari ÷ 365, dibulatkan ke bawah ke rupiah. Untuk mengubah aturan, **tambahkan aturan baru** dengan tanggal berlaku baru; aturan yang sudah dipakai posting dikunci agar riwayat tetap dapat ditelusuri. Tanpa aturan, tidak ada bunga yang diposting.

Posting bunga bulan lalu (aman dijalankan ulang, tidak dobel):

```bash
./tahu apply_savings_interest                      # bulan lalu
./tahu apply_savings_interest --period 2026-09 --dry-run   # simulasi
# crontab: tiap tanggal 1 jam 01:00
0 1 1 * * cd /path/to/SakuKoperasi && ./tahu apply_savings_interest
```

| Endpoint | Keterangan |
|---|---|
| `/api/nasabah/` | Data nasabah (`?search=`) |
| `/api/rekening-simpanan/` | Buka/lihat rekening (`?search=`), `POST …/{id}/tutup/` |
| `/api/transaksi-simpanan/` | Setor/tarik (create/list, `?rekening=<id>`, `?search=`) |
| `/api/jenis-simpanan/` | Jenis simpanan & saldo mengendap |
| `/api/aturan-bunga/` | Aturan bunga |

## Simpanan Pokok & Wajib

- Wajib untuk setiap anggota, dicatat dalam **Akun Simpanan Anggota** dengan **ID diisi manual** petugas (mis. `SA-0012`), satu akun per anggota.
- Akun dibuat terpisah dari pendaftaran anggota. Anggota yang belum punya akun: admin **Anggota → filter "Akun simpanan pokok & wajib: Belum ada"**.
- Saat akun dibuat, sistem membuat 2 sub-rekening: `<ID>-POKOK` dan `<ID>-WAJIB`. Saat setor, pilih akun (cari nama) lalu pilih jenis **pokok** atau **wajib**.
- **Nominal** diatur di admin **Nominal Simpanan** (berversi per tanggal berlaku). Pokok dibayar sekali (boleh dicicil); wajib per bulan mulai bulan akun dibuka.
- **Tunggakan** = kewajiban yang sudah jatuh tempo − total setoran (wajib dihitung s.d. bulan lalu; bulan berjalan = tagihan bulan ini). Kelebihan setor mengurangi tagihan bulan berikutnya.
- Pokok & wajib **hanya bisa ditarik saat anggota keluar** lewat **Proses keluar anggota** (admin aksi / API), yang ditolak bila masih ada pinjaman atau denda belum lunas. Proses ini menonaktifkan anggota, mengembalikan seluruh saldo pokok & wajib, dan menutup akun. Simpanan sukarela diurus terpisah.
- Pokok & wajib tidak mendapat bunga (bisa diaktifkan nanti dengan menambah Aturan Bunga untuk jenis tersebut).

| Endpoint | Keterangan |
|---|---|
| `/api/akun-simpanan-anggota/` | Buat/lihat akun (`?search=` nama/ID anggota/ID akun), berisi ringkasan saldo & tunggakan |
| `POST /api/akun-simpanan-anggota/{id}/setor/` | `{"jenis": "pokok"\|"wajib", "amount": opsional}`; tanpa `amount` = bayar semua yang jatuh tempo |
| `POST /api/akun-simpanan-anggota/{id}/proses-keluar/` | Pengembalian simpanan & tutup akun |
| `/api/nominal-simpanan/` | Nominal pokok/wajib berversi |

## Pagination, Filter & Pencarian API

Semua endpoint daftar mengembalikan format berhalaman:

```json
{"count": 120, "next": "…?page=2", "previous": null, "results": [ … ]}
```

| Parameter | Contoh |
|---|---|
| Halaman | `?page=2&page_size=100` (default 50, maks 200) |
| Pencarian | `?search=budi` |
| Urutan | `?ordering=-loan_date` |
| Rentang tanggal | `?loan_date_after=2026-01-01&loan_date_before=2026-03-31` |

Filter per endpoint: anggota (`is_active`, `has_akun_simpanan`), jaminan (`member`, `jenis_penjamin`), pinjaman (`member`, `status`, `jaminan`, `loan_date_*`), pembayaran (`loan`, `payment_date_*`), denda (`loan`, `is_paid`, `penalty_date_*`), rekening (`nasabah`, `product`, `jenis`, `is_active`, `member_account`), transaksi simpanan (`rekening`, `transaction_type`, `transaction_date_*`), akun simpanan anggota (`member`, `is_active`).

## CI & Development

GitHub Actions (`.github/workflows/ci.yml`) berjalan di setiap PR dan push ke `main`: lint (ruff), `manage.py check`, cek migrasi tertinggal, seluruh test terhadap PostgreSQL 16, `check --deploy` mode production, dan build image Docker.

Menjalankan hal yang sama secara lokal (dari folder `sakukoperasi/`, database PostgreSQL harus berjalan):

```bash
pip install -r requirements-dev.txt
ruff check .
DJANGO_DEBUG=true python manage.py makemigrations --check --dry-run
DJANGO_DEBUG=true python manage.py test core
```
