# Verifikasi Elaine 2.1

**58 pengujian otomatis lulus** pada Python 3.12. Pengujian memakai SQLite file nyata, migration Alembic, clock terkontrol, HTTPX MockTransport, serta simulasi API Discord. Lint Ruff, kompilasi Python, pemeriksaan dependency pip dan syntax shell installer/upgrader lulus.

Cakupan lama: parsing rupiah, idempotensi konfirmasi paralel, isolasi owner, edit/void/audit, TTL/version draf, CSV, persistensi alarm setelah database dibuka ulang, retry/catch-up, sumber ingatan, JSON repair, registrasi command dan backup.

Cakupan baru:

- Callback tombol Akui → Selesai, Tunda, idempotensi double-click, pembaruan kartu dan penghentian voice. Simulasi webhook secara eksplisit menolak `view=None` seperti discord.py, sehingga kegagalan v2.0 dapat terdeteksi.
- Status diakui terpisah dari selesai; tugas berulang tidak ikut mematikan seluruh seri; tombol lama tidak bisa menyelesaikan seri yang dibatalkan.
- Hapus transaksi perlu preview; owner/versi diperiksa; hasil hilang dari riwayat, ekspor dan total; audit/idempotensi tetap terjaga. Termasuk transaksi yang sebelumnya voided.
- Batas panjang embed/field/modal/custom ID dengan input panjang, pagination dan round-trip persistent button.
- Konfigurasi terenkripsi, izin 600, reload/restart, endpoint baru perlu key baru, URL privat/DNS privat ditolak, admin server tidak otomatis menjadi admin AI, key tidak diprefill atau dipantulkan dalam UI.
- Reaksi terpisah dari teks; emoji Unicode/custom/shortcode dihilangkan tanpa merusak jam HH:MM:SS. Anggaran context menghitung serialisasi arsip.
- Audio PCM berukuran frame yang benar; voice play/disconnect; voice sibuk; owner tidak hadir; cancel saat connect; tidak mengganggu koneksi yang tidak dimiliki ketika validasi gagal.
- Upgrade database 0001 nyata ke 0002 memperbaiki status acknowledged dan mempertahankan data lama serta kolom voice opsional.

Peringatan deprecation audioop/TextInput.label berasal dari API library dan tidak menggagalkan pengujian pada Python 3.12. API lama masih didukung oleh discord.py 2.7.1 yang dikunci.

**Belum diuji live:** autentikasi dan command sync Discord, voice handshake/DAVE/UDP pada VPS, pemutaran audio yang terdengar di perangkat pengguna, model/API key asli, dan script upgrade pada VPS pengguna. Karena tidak ada akses tersebut di lingkungan pengembangan, pengujian otomatis tidak membuktikan bot sudah diperbarui atau voice sudah terdengar. Ikuti uji penerimaan UPGRADE.md.

`docs/visual-preview.html` menampilkan ilustrasi kartu yang dibangun dari output presentation yang sama. Ini pratinjau HTML, bukan screenshot Discord, dan tombolnya tidak interaktif.

Jalankan ulang:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
```
