"""Menu Tentang: versi, status FFmpeg, dan tips."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import APP_AUTHOR, APP_NAME, __version__
from ..core import tools
from .common import card, hint, page_header


def engine_status() -> tuple[bool, str]:
    try:
        ff = tools.ffmpeg_path()
        ver = tools.run([ff, "-hide_banner", "-version"]).stdout.splitlines()[0]
        rb = "aktif" if tools.has_filter("rubberband") else "cadangan (tanpa rubberband)"
        return True, f"{ver}\nMesin ubah nada: {rb}"
    except Exception as exc:
        return False, str(exc)


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(page_header(APP_NAME, f"Versi {__version__} · by {APP_AUTHOR}"))

        c1, l1 = card("Mesin")
        ok, text = engine_status()
        lbl = QLabel(text)
        lbl.setObjectName("Ok" if ok else "Err")
        lbl.setWordWrap(True)
        l1.addWidget(lbl)
        lay.addWidget(c1)

        c2, l2 = card("Tips biar videomu kece buat dipamerin")
        for t in [
            "1. Bikin naskah dulu. Lempar poin-poinnya ke Claude, minta dirapikan jadi narasi "
            "30–60 detik.",
            "2. Rekam di menu Rekam Suara pakai teleprompter. Pakai earphone yang ada mic-nya, "
            "di ruangan yang tidak bergema.",
            "3. Di Efek Suara, coba preset Penyiar Radio dulu — biasanya paling aman. Macho dan "
            "Husky untuk gaya.",
            "4. Di Gabung Video, hapus suara asli, pasang narasi, tambah musik pelan. Centang "
            "720p supaya ringan diunggah.",
            "5. Kalau malas rekam, pakai Suara AI.",
            "6. Butuh caption? Menu Suara → Teks membuat teks dan subtitle (.srt) dari videomu.",
        ]:
            l2.addWidget(hint(t))
        lay.addWidget(c2)

        c3, l3 = card("Lisensi")
        l3.addWidget(hint("SuaraKu memakai FFmpeg (ffmpeg.org, lisensi GPL), Qt/PySide6 (LGPL), "
                          "edge-tts, faster-whisper + model Whisper (MIT). Mode Live memakai "
                          "VB-CABLE dari vb-audio.com (dipasang terpisah)."))
        lay.addWidget(c3)
        lay.addStretch(1)
