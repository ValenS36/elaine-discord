# Upgrade Elaine 2.0 → 2.1

Paket ini menerapkan 11 penyesuaian antarmuka, pengingat, keuangan, AI, dan voice. Kode serta pengujian disiapkan lokal; aktivasi ke VPS dan koneksi voice Discord nyata tetap perlu diverifikasi di server Anda. Repository publik memberi akses baca, bukan kredensial push ke akun GitHub.

## Pasang di VPS yang sekarang

Unduh `elaine-v2.1.zip`. Di **CMD Windows lokal**:

```bat
scp "%USERPROFILE%\Downloads\elaine-v2.1.zip" agentuser@100.95.193.63:/tmp/elaine-v2.1.zip
ssh agentuser@100.95.193.63
```

Di **terminal VPS**:

```bash
sudo apt update
sudo apt install -y unzip libopus0 python3-venv
unzip /tmp/elaine-v2.1.zip -d /tmp
sudo bash /tmp/elaine-v2.1/deploy/upgrade.sh
sudo systemctl status elaine.service --no-pager
sudo journalctl -u elaine.service -n 60 --no-pager
```

Gunakan direktori ekstraksi baru; jika `/tmp/elaine-v2.1` sudah berisi pekerjaan lain, simpan pekerjaan itu dahulu. Jangan ekstrak langsung menimpa bot aktif.

Script menyimpan source lama, `.env`, daftar dependency, dan database sebelum upgrade; menghentikan bot; mengganti source; memasang dependency; menjalankan migration 0002 dan check; kemudian menyalakan bot. `.env`, `data/`, serta `.git` instalasi dipertahankan. Backup privat berada di `/opt/elaine-backups/<waktu>/`. Perubahan source lokal juga tercadangkan; prompt baru berisi perubahan reaksi, sehingga jika pernah menyesuaikan prompt sendiri, gabungkan dengan salinan lama setelah update. Upgrade menyebabkan downtime selama pemasangan.

Jika script gagal setelah service dihentikan, service tetap berhenti agar schema dan kode tidak bercampur. Jangan menghapus database atau langsung menyalakan kode 2.0 di database 0002. Untuk rollback, gunakan source, dependency dan database dari checkpoint yang sama saat bot berhenti; data yang dibuat setelah checkpoint tidak ada dalam backup tersebut. Periksa error terlebih dahulu atau kirim pesan error tanpa token/API key.

## Izin Discord

Di text channel: View Channel, Send Messages, **Embed Links**, Read Message History, Attach Files, **Add Reactions**.

Di voice channel: View Channel, **Connect**, **Speak**. Message Content Intent tetap aktif. Bot tidak membutuhkan Administrator. Command disinkronkan pada startup ke server utama. Jika command baru belum tampil, tutup/buka Discord atau tunggu sinkronisasi selesai; pastikan log `Elaine siap` berasal dari startup terbaru.

## Uji penerimaan

1. `/admin status`: versi 2.1.0, model AI, mode API, context arsip, worker dan kesiapan suara.
2. `/elaine profil`: kartu Elaine, personality, kemampuan, avatar. `/elaine preferensi zona_waktu:Asia/Jakarta` mengubah preferensi Anda.
3. `/ingat buat`: buat pengingat beberapa menit ke depan. Tekan **Akui** → status *Diakui · belum selesai*. Tekan **Selesai** → *Selesai*, tanpa error palsu. Periksa `/ingat daftar`.
4. Buat alarm lain → **Tunda 10 menit**. Kartu menunjukkan waktu baru; tidak ada kiriman sebelum jatuh tempo baru. Restart service untuk memeriksa persistensi.
5. `/uang tambah` → isi formulir → kartu receipt. Gunakan **Hapus** atau `/uang hapus id:...` → periksa preview → **Hapus transaksi**. Transaksi hilang dari riwayat, ekspor dan rekap. Audit internal tetap tersimpan, bukan penghapusan fisik seluruh jejak database.
6. Masuk voice channel, jalankan `/suara tes` (5 detik). `/suara gabung` membuat Elaine bergabung; `/suara keluar` memutus sesi. Penghentian sesi dibatasi pemilik sesi/admin.
7. `/ingat buat jenis:alarm channel_suara:<channel>` dengan waktu dekat. Pemilik harus berada di channel tersebut saat pembuatan dan pemutaran. Alarm berbunyi maksimal 30 detik, lalu keluar; berulang sesuai jumlah/interval alarm yang sudah disetel. Akui/Selesai/Tunda/Batal menghentikan audio. Jika pengguna tidak ada, izin gagal, atau voice sibuk, notifikasi teks tetap tersedia dan kartu menjelaskan status suara. Ini tidak menjamin membangunkan perangkat yang offline/mute/deafen. Satu alarm suara per server pada satu waktu.
8. `/admin ai`: isi model, base URL, API key, context arsip dan mode API. Simpan, lalu `/admin ai_tes`. Key tidak diprefill atau dicetak; kosong berarti gunakan key sebelumnya jika endpoint sama. Mengganti endpoint membutuhkan key baru. Setting berlaku pada request baru dan bertahan setelah restart.
9. Uji akun kedua: tidak boleh mengubah konfigurasi AI global, menghapus transaksi, atau menekan tombol pengingat milik akun pertama.
10. Ajak Elaine ngobrol. Reaksi dipilih AI jika sesuai konteks, maksimal satu per pesan. Teks obrolan difilter dari emoji Unicode/custom/shortcode; gestur Markdown tetap boleh. Reaksi bukan jaminan muncul pada setiap pesan.

## Siapa yang boleh mengubah AI?

Pemilik aplikasi bot dari Discord Developer Portal (atau pemilik team) dapat memakai `/admin ai`. Izin Manage Server saja tidak memberikan akses key global. Jika perlu, pemilik VPS dapat menambahkan user tepercaya lewat `.env`:

```dotenv
AI_CONFIG_ADMIN_IDS=123456789012345678
```

Pisahkan beberapa ID dengan koma, lalu restart service. Group `/admin` default terlihat untuk pengguna dengan Manage Server; sesuaikan izin command di Integrations bila pemilik aplikasi tidak memiliki izin tersebut di server. Pemeriksaan owner di backend tetap berlaku.

Form modal hanya dikirim kepada penggunanya; input API key tetap diproses melalui Discord, bukan field password yang disamarkan. Jangan mengetik key dalam chat. Status hanya menyebut key tersedia/tidak. Base URL harus HTTPS publik dan kompatibel `/chat/completions`; native Anthropic belum didukung.

Override disimpan terenkripsi di `data/ai-settings.enc`, dengan key lokal `data/ai-settings.key` (izin 600). Keduanya dan `.env` perlu backup privat terpisah dari backup SQLite; mengenkripsi tidak melindungi dari pihak yang sudah mempunyai akses penuh ke VPS. Untuk kembali ke setting `.env`, hentikan bot, pindahkan kedua file override ke tempat backup privat, lalu restart. Jangan mengunggah file-file ini ke GitHub.

Context yang ditampilkan adalah **anggaran arsip dalam karakter**, default 18.000. Itu bukan jendela konteks maksimum Sonnet atau batas token akun provider. Payload juga memuat system prompt/schema dan pesan sekarang. Karena endpoint kompatibel tidak selalu mengumumkan kapasitas model, bot menampilkan kapasitas token model sebagai tidak dilaporkan, bukan menebaknya.

## Sinkronkan perubahan ke GitHub

Paket berisi source lengkap. Jika repository kerja berada di `~/elaine-github`, salin source dari paket ke sana (jangan menyalin `.env`/`data/`). Jika memakai Git langsung di `/opt/elaine-v2`, jalankan status setelah upgrade. Kemudian sebagai akun pemilik repository:

```bash
git status --short
git add src prompts migrations tests deploy docs README.md DEPLOY.md TESTING.md UPGRADE.md CHANGELOG.md pyproject.toml requirements.lock alembic.ini .env.example .gitignore
git diff --cached --stat
git commit -m "Elaine 2.1: Discord cards, reminder fixes, voice and AI settings"
git push origin main
```

Periksa perubahan terlebih dahulu. Jangan memakai `sudo git`. Ada juga patch Git `elaine-v2.1.patch` untuk diterapkan dari root repository yang masih berdasarkan commit `94f2fa7`:

```bash
git apply --check /path/ke/elaine-v2.1.patch
git apply /path/ke/elaine-v2.1.patch
```

Jika pengecekan konflik, jangan gunakan force; gabungkan perubahan lokal terlebih dahulu. Push GitHub tetap tidak otomatis mengubah kode di VPS tanpa alur deploy.
