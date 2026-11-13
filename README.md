# SuaraKu

Aplikasi Windows untuk edit video & suara ringan — bikin video buat pamer di LinkedIn atau status WA.
by **Pahlevi Lubis**.

## Fitur

| Menu | Gunanya |
|---|---|
| **Potong** | Potong MP3/rekaman/video lewat **gelombang suara**: drag untuk memilih, buang/ambil bagian, pecah jadi beberapa file, fade in/out, **buang hening otomatis**, **edit pakai teks** (hapus kata di teks → suaranya ikut terpotong), dan **Rapikan dengan Claude** (buang “eh/emm” & kalimat yang diulang). |
| **Konversi** | MP4 → MP3, M4A, WAV, OGG, FLAC; video ke MP4 / MP4 kecil 720p / MP4 tanpa suara. Bisa banyak file sekaligus dan potong (trim). |
| **Rekam Suara** | Rekam narasi dari mic — **manual** atau **otomatis** (mulai saat kamu bicara, berhenti sendiri saat diam). Teleprompter bergulir, video referensi, rapatkan jeda hening, lalu otomatis **beri efek** dan **ketik jadi teks**. |
| **Efek Suara** | Preset **Penyiar Radio, Macho, Husky/Sexy, Podcast, Bersih**, Gema Aula, Telepon, Robot, Raksasa, Tupai — plus atur manual (nada, bass, kejelasan, treble, gema, pembersih noise). Bisa dengar contoh sebelum/sesudah, dan bisa langsung ke suara di dalam video. |
| **Suara → Teks** | Ubah rekaman/voice note/video jadi tulisan secara offline (Whisper), plus subtitle `.srt`. Mesin Cepat sudah terpasang, mesin Akurat bisa diunduh. |
| **Suara AI** | Ketik naskah → dibacakan suara AI (Indonesia & Inggris). Butuh internet. |
| **Gabung Video** | Hapus suara asli video, pasang narasi (bisa diberi efek & digeser waktunya), tambah musik latar yang otomatis mengecil saat ada narasi, **subtitle otomatis** (3 gaya, + file .srt), opsi 720p. |
| **✨ Asisten Claude** | Butuh API key dari [platform.claude.com](https://platform.claude.com/) (bayar per pakai, terpisah dari langganan Claude.ai). Bikin naskah → teleprompter / Suara AI, bikin caption LinkedIn & status WA, plus tombol ✨ di Efek Suara & Mode Live (“ceritakan suara yang kamu mau”) dan di Potong (rapikan rekaman). Yang dikirim hanya teks, bukan suara. API key disimpan terenkripsi (Windows DPAPI). |
| **Mode Live** | Ganti suara secara langsung saat telepon/video call di WhatsApp Desktop, Teams, Zoom, Meet. Butuh mic virtual gratis [VB-CABLE](https://vb-audio.com/Cable/) (pasang sekali). Jeda sekitar 0,1–0,25 detik; pakai earphone. |

## Cara download & pasang

1. Buka tab **Releases** di repo ini → versi paling atas.
2. Download `SuaraKu-Setup-x.y.z.exe`, lalu jalankan.
3. Kalau muncul "Windows protected your PC": klik **More info → Run anyway**
   (aplikasinya belum ditandatangani sertifikat berbayar, jadi Windows belum kenal).

Tidak perlu hak admin. Hasil rekaman dan suara AI tersimpan di `Documents\SuaraKu`.

## Cara build

Setiap kali ada perubahan di branch `main`, GitHub Actions otomatis:

1. Menjalankan tes mesin audio/video memakai FFmpeg Windows.
2. Membuat `SuaraKu.exe` dengan PyInstaller dan memasukkan FFmpeg.
3. Menjalankan cek mandiri (`SuaraKu.exe --selftest`).
4. Membuat installer dengan Inno Setup dan menerbitkannya di **Releases**.

Menjalankan dari kode (butuh Python 3.12 dan FFmpeg di PATH):

```
pip install -r requirements.txt
python main.py
python tests/test_core.py
```

## Struktur

```
main.py                 titik masuk + selftest
suaraku/core/           mesin (tanpa Qt): FFmpeg, efek, gabung, suara AI
suaraku/ui/             tampilan PySide6, satu file per menu
packaging/              resep PyInstaller + skrip installer Inno Setup
.github/workflows/      build otomatis di GitHub
```

## Lisensi pihak ketiga

FFmpeg (GPL, ffmpeg.org), Qt/PySide6 (LGPL), edge-tts (GPL-3.0), faster-whisper & model Whisper (MIT), font DejaVu (lisensi Bitstream Vera, lihat `assets/fonts`). VB-CABLE (vb-audio.com) dipasang terpisah oleh pengguna.
