"""Jendela utama: menu di kiri, isi di kanan."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .. import APP_AUTHOR, APP_NAME, __version__
from ..core import tools
from . import common
from .common import FuncThread, TaskPanel, scroll_page
from .page_about import AboutPage
from .page_convert import ConvertPage
from .page_effect import EffectPage
from .page_live import LivePage
from .page_merge import MergePage
from .page_record import RecordPage
from .page_transcribe import TranscribePage
from .page_tts import TtsPage


def asset(name: str) -> str:
    roots = [tools.app_dir() / "assets"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.insert(0, Path(meipass) / "assets")
    for r in roots:
        if (r / name).exists():
            return str(r / name)
    return ""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — edit video & suara")
        icon = asset("icon.png")
        if icon:
            self.setWindowIcon(QIcon(icon))
        self.resize(1200, 860)
        self.setMinimumSize(QSize(980, 640))

        self.convert = ConvertPage()
        self.record = RecordPage()
        self.effect = EffectPage()
        self.transcribe = TranscribePage()
        self.tts = TtsPage()
        self.merge = MergePage()
        self.live = LivePage()
        self.about = AboutPage()
        pages = [
            ("Konversi", "MP4 → MP3 & format lain", self.convert),
            ("Rekam Suara", "Manual / otomatis + teleprompter", self.record),
            ("Efek Suara", "Penyiar, Macho, Husky…", self.effect),
            ("Suara → Teks", "Ketik otomatis isi rekaman/video", self.transcribe),
            ("Suara AI", "Naskah dibacakan AI", self.tts),
            ("Gabung Video", "Video + narasi + musik", self.merge),
            ("Mode Live", "Ganti suara saat telepon", self.live),
            ("Tentang", "Versi & tips", self.about),
        ]

        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(232)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 20, 14, 14)
        sl.setSpacing(4)
        brand_row = QHBoxLayout()
        if icon:
            logo = QLabel()
            logo.setPixmap(QIcon(icon).pixmap(34, 34))
            brand_row.addWidget(logo)
        brand = QLabel(APP_NAME)
        brand.setObjectName("Brand")
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        sl.addLayout(brand_row)
        sub = QLabel(f"by {APP_AUTHOR}")
        sub.setObjectName("BrandSub")
        sl.addWidget(sub)
        sl.addSpacing(16)

        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.stack = QStackedWidget()
        for title, desc, page in pages:
            it = QListWidgetItem(title)
            it.setToolTip(desc)
            it.setSizeHint(QSize(180, 40))
            self.nav.addItem(it)
            self.stack.addWidget(scroll_page(page))
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        sl.addWidget(self.nav, 1)
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("BrandSub")
        sl.addWidget(ver)

        central = QWidget()
        cl = QHBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(side)
        cl.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.record.setMinimumHeight(1180)

        # Alur antar menu
        self.record.use_in_effect.connect(lambda p: self._goto(self.effect, self.effect.set_file, p))
        self.record.use_in_merge.connect(lambda p: self._goto(self.merge, self.merge.set_narration, p))
        self.effect.use_in_merge.connect(lambda p: self._goto(self.merge, self.merge.set_narration, p))
        self.tts.use_in_merge.connect(lambda p: self._goto(self.merge, self.merge.set_narration, p))

        self.nav.setCurrentRow(0)
        try:
            tools.ffmpeg_path()
            tools.ffprobe_path()
        except tools.FfmpegMissing as exc:
            self.statusBar().showMessage(str(exc))

    def _goto(self, page: QWidget, setter, path: str) -> None:
        if path:
            setter(path)
        for i in range(self.stack.count()):
            sa = self.stack.widget(i)
            if sa.widget() is page:
                self.nav.setCurrentRow(i)
                break

    def closeEvent(self, e):  # noqa: N802
        self.record.shutdown()
        self.live.shutdown()
        for panel in self.findChildren(TaskPanel):
            if panel.thread:
                panel.thread.cancel()
                panel.thread.wait(5000)
        for t in self.findChildren(FuncThread):
            t.wait(3000)
        common.clean_temp()
        super().closeEvent(e)
