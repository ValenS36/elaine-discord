# Memasang Elaine v2 di VPS Ubuntu 24.04

Python 3.12; satu proses bot. Model dijalankan melalui API, bukan di VPS. Paket ini diuji lokal dengan database nyata dan API tiruan; koneksi Discord/provider dan VPS belum diuji. VPS 2 core/8 GB sesuai untuk instalasi kecil ini, tetapi kapasitas nyata bergantung pada jumlah pesan, latensi API, dan ukuran arsip.

## 1. Upload dari komputer Windows

Simpan `elaine-v2.zip` di Downloads. Jalankan di **Command Prompt lokal Windows**, bukan terminal SSH Ubuntu:

```bat
scp "%USERPROFILE%\Downloads\elaine-v2.zip" agentuser@100.95.193.63:/tmp/elaine-v2.zip
ssh agentuser@100.95.193.63
```

Alamat 100.95.193.63 harus bisa dijangkau dari komputer Anda (misalnya melalui jaringan privat yang biasa digunakan).

## 2. Siapkan di terminal VPS

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip unzip
sudo unzip /tmp/elaine-v2.zip -d /opt
cd /opt/elaine-v2
sudo bash deploy/install.sh
sudo nano /opt/elaine-v2/.env
```

Gunakan direktori baru ini untuk instalasi pertama; jangan ekstrak menimpa instalasi v2 yang sedang berjalan. Installer tidak menghentikan bot lama dan tidak otomatis menyalakan bot baru. Jika `.env` v1 ada, setting lama disalin tanpa menampilkan secret. Periksa:

- `DISCORD_TOKEN`: token bot Discord.
- `CHANNEL_ID`: ID text channel server, sama seperti sebelumnya jika ingin melanjutkan riwayat.
- `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL`: sesuai provider yang digunakan. Endpoint harus menyediakan API kompatibel OpenAI `/chat/completions`; paket ini belum mendukung native Anthropic `/v1/messages`. ID Sonnet harus mengikuti dashboard provider, bukan nama perkiraan.
- `AI_MODE=json` dahulu. `tools` hanya bila provider mendukung function calling.
- `DB_PATH=data/elaine_v2.sqlite3`: **jangan arahkan ke database v1**.
- `DEFAULT_TIMEZONE=Asia/Jakarta` (WIB).

Discord Developer Portal → aplikasi bot → Bot → aktifkan **Message Content Intent**. Invite bot dengan scope `bot` dan `applications.commands`. Izin channel: View Channel, Send Messages, Read Message History, Attach Files. Administrator tidak diperlukan.

```bash
sudo -u discordbot .venv/bin/elaine check
sudo -u discordbot .venv/bin/elaine probe-ai
```

`check` memvalidasi konfigurasi/database saja. `probe-ai` memanggil provider dan menggunakan kuota API. Jangan lanjut cutover jika gagal.

## 3. Pindah dari v1

Cari nama service bot lama:

```bash
systemctl list-units --type=service --all | grep -Ei 'discord|elaine'
```

Jika namanya `discord-ai-bot.service`, perintahnya:

```bash
sudo systemctl disable --now discord-ai-bot.service
```

Ganti nama tersebut bila hasil daftar berbeda. Jika v1 berjalan manual di terminal, hentikan dengan Ctrl+C. Jangan menjalankan dua bot dengan token sama sekaligus.

Opsional, impor arsip v1 setelah bot lama berhenti. Backup database lama secara konsisten (sesuaikan nama file dengan `DB_PATH` pada `.env` lama):

```bash
sudo /opt/elaine-v2/.venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path
source = Path('/opt/discord-ai-bot/data/memory.sqlite3')
target = Path('/opt/elaine-v2/data/legacy-memory.sqlite3')
if not source.is_file() or target.exists():
    raise SystemExit('Periksa path sumber; target harus belum ada.')
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)
PY
sudo chown discordbot:discordbot data/legacy-memory.sqlite3
sudo chmod 600 data/legacy-memory.sqlite3
```

Tambahkan ke `.env`:

```dotenv
LEGACY_DB_PATH=/opt/elaine-v2/data/legacy-memory.sqlite3
```

Import otomatis saat startup pertama: hanya channel yang dipilih, idempotent, tidak mengulang instruksi/aksi dari pesan lama. Setelah log `Legacy rows inspected`, hapus baris `LEGACY_DB_PATH` untuk mempercepat restart. Format import mengikuti tabel `messages` bot v1: id, channel_id, author_id, author_name, created_at (ISO), role, content. Jika format berbeda, jangan memaksa impor; arsip lama tetap utuh.

## 4. Jalankan 24/7

```bash
sudo systemctl enable --now elaine.service
sudo systemctl status elaine.service --no-pager
sudo journalctl -u elaine.service -n 80 --no-pager
```

Tunggu log `Elaine siap`. Command disinkronkan ke server channel utama saat startup. Coba `/elaine bantuan`, `/uang tambah`, dan `/ingat buat` dengan jadwal beberapa menit ke depan. Restart service dan pastikan jadwal tetap ada. Uji akun kedua tidak bisa membaca ledger pertama. Tes ini membutuhkan Discord sebenarnya dan tetap perlu dilakukan di VPS.

```bash
sudo systemctl restart elaine.service
sudo journalctl -u elaine.service -f
```

Jika `PrivilegedIntentsRequired`, aktifkan Message Content Intent. Peringatan PyNaCl/voice boleh diabaikan untuk bot teks ini. Jika command tidak tampil, periksa scope aplikasi dan server yang dipilih. Jika HTTP API gagal, periksa kredensial/model/endpoint; `/uang` dan `/ingat` tetap independen dari AI setelah startup.

## 5. Backup dan update

```bash
cd /opt/elaine-v2
sudo -u discordbot .venv/bin/elaine backup --output data/backup-2026-10-28.sqlite3
```

Gunakan nama baru setiap backup. Simpan salinan terpisah dari VPS serta `.env` di tempat privat. Backup menggunakan SQLite backup API, bukan menyalin satu file saat WAL aktif. Untuk update: backup, hentikan service, ganti source dan lock dependency, install dependencies, jalankan `migrate`, `check`, tes, lalu restart. Pertahankan `.env` dan `data/`. Jangan mengedit migration lama; tambah revision baru.

Jika startup v2 gagal, hentikan `elaine.service` dahulu. V1 dan databasenya tidak ditimpa sehingga bisa dinyalakan kembali setelah diperiksa. Data baru yang sudah dibuat di v2 tidak otomatis tersedia pada v1; jangan menghapus database v2.
