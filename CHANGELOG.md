# 2.1.0

- Semua tampilan domain utama menggunakan Discord embed, field konsisten, label Indonesia, pagination lima item, dan tombol tindakan. Ini kartu native Discord; tidak memakai tabel Markdown yang tidak dirender konsisten di Discord.
- `/admin status` menampilkan model aktif, batas context arsip dalam karakter, mode API, database, worker dan dukungan voice. Tidak mengarang kapasitas token model.
- `/elaine profil` menjadi profil Elaine dengan avatar, personality dan kemampuan. Timezone pindah ke `/elaine preferensi`.
- Memperbaiki `Webhook.send(view=None)` yang menyebabkan pesan gagal sesudah commit saat selesai/snooze. Pengujian callback memeriksa parameter view agar regresi ini terdeteksi.
- Akui menghentikan notifikasi, tidak menyelesaikan tugas. Selesai mencatat penyelesaian. Tunda memperbarui revisi dan waktu, membatalkan delivery lama, dan berhenti bila tugas sudah selesai. Tombol memperbarui kartu asal dengan status terbaru.
- Migration 0002 memperbaiki pengingat satu kali yang ditandai completed padahal occurrence hanya acknowledged, dan menambah tujuan voice yang opsional.
- `/uang hapus` dan tombol Hapus memakai preview, versi, kepemilikan, idempotensi dan audit. Transaksi deleted tidak masuk riwayat, ekspor dan rekap; audit dipertahankan.
- Alarm voice terjadwal, `/suara gabung`, tes 5 detik dan keluar. Nada PCM dihasilkan lokal, tanpa file suara dari internet/FFmpeg. Pemutaran dibatasi 30 detik per delivery dan berhenti saat diakui/selesai/ditunda/dibatalkan/pengguna keluar channel.
- `/admin ai` modal privat + `/admin ai_tes`: override model, URL, key, context dan mode disimpan atomik terenkripsi. Owner aplikasi saja secara default, key tidak dicetak, endpoint baru membutuhkan key baru, URL privat ditolak.
- AI boleh memberi reaksi dari allowlist. Emoji tidak dicampur dalam teks obrolan; filter output mempertahankan gestur Markdown.
- Modul baru: presentation, voice, runtime_config, reactions. Domain tidak bergantung pada Discord UI.
