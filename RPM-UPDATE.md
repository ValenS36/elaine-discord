# Batas AI 5 RPM

Seluruh panggilan AI pada satu proses Elaine berbagi maksimum 5 request dalam
jendela berjalan 60 detik. Ini mencakup chat, retry respons tidak valid,
ringkasan ingatan, serta /admin ai_tes. HTTP yang gagal tetap menghabiskan slot.
Request berikutnya menunggu secara asynchronous; timeout HTTP baru dihitung
setelah mendapat slot. Lima request pertama boleh berurutan dalam waktu singkat.
Ini bukan batas token (TPM), biaya, atau lima pesan Discord.

/admin status menampilkan batasnya. Batas tetap 5 dan tidak berubah saat model,
API key, atau endpoint diganti lewat Discord. Scheduler pengingat dan pemutaran
alarm tidak membutuhkan request AI; antrean AI tidak menghentikan scheduler.
Chat yang menunggu bisa lebih lambat dan antrean chat per channel tetap berlaku.

Pembatas disimpan di memori satu proses: restart mengosongkan riwayat slot.
Jalankan hanya satu service Elaine. Proses lain atau aplikasi lain yang memakai
API key sama tidak ikut dihitung. Provider tetap dapat menolak request berdasarkan
kuota akun, token, atau request dari aplikasi lain.

## Terapkan patch di repository VPS

Patch dibuat dari origin/main b2f2765. Pastikan working tree bersih sebelum mulai.

```bash
cd ~/elaine-github
git switch main
git pull --ff-only origin main
git apply --check /tmp/elaine-5rpm.patch && git apply /tmp/elaine-5rpm.patch
git add src/elaine/rate_limit.py src/elaine/provider.py src/elaine/presentation.py tests/test_rate_limit.py RPM-UPDATE.md
git commit -m "Limit all AI requests to 5 per rolling minute"
git push origin main
```

Jika apply gagal, jangan lanjut atau memakai force. Gabungkan perubahan dahulu.
Push repository tidak otomatis memperbarui service. Untuk instalasi 2.1 yang
masih menggunakan source dasar b2f2765, salin tiga file kode berikut dari
repository yang sudah diperbarui; tidak ada dependency atau migrasi DB baru.

```bash
sudo systemctl stop elaine.service
sudo install -o discordbot -g discordbot -m 644 src/elaine/rate_limit.py src/elaine/provider.py src/elaine/presentation.py /opt/elaine-v2/src/elaine/
sudo systemctl start elaine.service
sudo journalctl -u elaine.service -n 60 --no-pager
```

Sebelum menimpa source instalasi, simpan salinan tiga file lama jika ada perubahan
lokal. Jika repository Git Anda langsung berada di /opt/elaine-v2, terapkan patch
di sana dan restart service; tidak perlu menyalin file ke folder yang sama.
