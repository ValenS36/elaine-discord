# Verifikasi

Pengujian otomatis dilakukan pada Python 3.12, menggunakan SQLite file nyata, migrasi Alembic, jam tiruan, serta HTTPX MockTransport. Tidak memakai token Discord/API nyata.

**Hasil: 26 pengujian lulus.**

Cakupan: parsing rupiah dan input ambigu; idempotensi konfirmasi paralel; isolasi owner; edit/void/audit; versi draf dan TTL; ekspor CSV; alarm setelah database dibuka ulang; ack/cancel/snooze; retry; catch-up jadwal harian; scope arsip dan invalidasi fakta karena edit/delete; pemulihan JSON provider; registrasi slash command; konsistensi backup.

Pemeriksaan tambahan: lint Ruff, kompilasi Python, syntax shell installer. Peringatan audioop dari library Discord pada Python 3.12 tidak menghalangi fitur teks.

Belum diuji live: login Discord, sinkronisasi command ke server, permission overwrite server milik pengguna, interaksi tombol dari aplikasi Discord, format respons Sonnet di provider pengguna, systemd di VPS. Langkah uji penerimaan ada di DEPLOY.md. Jangan menyamakan pengujian offline dengan bot sudah online.
