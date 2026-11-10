"""Menu Gabung: video + narasi + musik latar, hapus/campur suara asli."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QLabel, QPushButton, QRadioButton,
    QVBoxLayout, QWidget,
)

from ..core import effects, jobs
from . import common
from .common import (
    FILTER_AUDIO, FILTER_MEDIA, FILTER_VIDEO, FilePicker, LabeledSlider, Player, TaskPanel, card,
    hint, page_header, row,
)


class MergePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(page_header(
            "Gabung Video + Suara",
            "Hapus suara asli video, pasang narasimu, tambah musik latar. "
            "Musik otomatis mengecil saat kamu bicara, lalu naik lagi."))

        c1, l1 = card("1. Video")
        self.video = FilePicker("Pilih video…", FILTER_VIDEO, clearable=False)
        self.video.changed.connect(self._video_changed)
        l1.addWidget(self.video)
        self.keep_orig_no = QRadioButton("Hapus suara asli video")
        self.keep_orig_yes = QRadioButton("Campur dengan suara asli")
        self.keep_orig_no.setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.keep_orig_no)
        g.addButton(self.keep_orig_yes)
        self.orig_vol = LabeledSlider("Volume suara asli", 0, 150, 60, step=5, unit="%")
        self.orig_vol.setEnabled(False)
        self.keep_orig_yes.toggled.connect(self.orig_vol.setEnabled)
        l1.addLayout(row(self.keep_orig_no, self.keep_orig_yes, None))
        l1.addWidget(self.orig_vol)
        lay.addWidget(c1)

        c2, l2 = card("2. Narasi (suaramu)",
                      "Hasil rekaman, hasil Efek Suara, atau Suara AI. Boleh dikosongkan.")
        self.narr = FilePicker("Pilih file narasi…", FILTER_MEDIA)
        l2.addWidget(self.narr)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(0, 3600)
        self.offset.setDecimals(1)
        self.offset.setSingleStep(0.5)
        self.offset.setSuffix(" detik")
        self.narr_fx = QComboBox()
        self.narr_fx.addItem("Tanpa efek tambahan", None)
        for p in effects.PRESETS:
            if p.key != "asli":
                self.narr_fx.addItem(p.name, p.key)
        l2.addLayout(row(QLabel("Mulai di"), self.offset, QLabel("Efek"), self.narr_fx, None))
        self.narr_vol = LabeledSlider("Volume narasi", 0, 200, 100, step=5, unit="%")
        l2.addWidget(self.narr_vol)
        lay.addWidget(c2)

        c3, l3 = card("3. Musik latar (opsional)",
                      "Pakai musik bebas hak cipta kalau mau diposting, supaya tidak di-take down.")
        self.music = FilePicker("Pilih musik…", FILTER_AUDIO)
        l3.addWidget(self.music)
        self.music_vol = LabeledSlider("Volume musik", 0, 100, 25, step=5, unit="%")
        l3.addWidget(self.music_vol)
        self.loop = QCheckBox("Ulangi musik sampai video habis")
        self.loop.setChecked(True)
        self.fade = QCheckBox("Musik memudar di akhir")
        self.fade.setChecked(True)
        self.duck = QCheckBox("Kecilkan musik otomatis saat ada narasi")
        self.duck.setChecked(True)
        l3.addLayout(row(self.loop, self.fade, self.duck, None))
        lay.addWidget(c3)

        c4, l4 = card("4. Hasil")
        self.small = QCheckBox("Kecilkan ke 720p (lebih ringan untuk WA / LinkedIn)")
        l4.addWidget(self.small)
        self.go = QPushButton("Gabungkan")
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self._go)
        self.task = TaskPanel()
        self.task.busyChanged.connect(lambda b: self.go.setEnabled(not b))
        self.task.finished.connect(self._done)
        self.player = Player(video=True)
        self.player.setMinimumHeight(320)
        l4.addLayout(row(self.go, None))
        l4.addWidget(self.task)
        l4.addWidget(self.player, 1)
        lay.addWidget(c4)
        lay.addWidget(hint("Kalau narasi dan musik dikosongkan dan suara asli dihapus, hasilnya "
                           "video tanpa suara."))
        lay.addStretch(1)

    # ------------------------------------------------------------
    def set_video(self, path: str) -> None:
        self.video.set_path(path)

    def set_narration(self, path: str) -> None:
        self.narr.set_path(path)

    def _video_changed(self, path: str) -> None:
        info = self.video.info
        has_audio = bool(info and info.has_audio)
        self.keep_orig_yes.setEnabled(has_audio)
        if not has_audio:
            self.keep_orig_no.setChecked(True)
        if path and info and info.is_video:
            self.player.load(path, label=f"Video asli: {Path(path).name}")
        else:
            self.player.unload()

    def _go(self) -> None:
        vpath = self.video.path()
        if not vpath or not self.video.info or not self.video.info.is_video:
            common.error_box(self, "Pilih file video dulu.")
            return
        for picker, name in ((self.narr, "narasi"), (self.music, "musik")):
            if picker.edit.text().strip() and not picker.path():
                common.error_box(self, f"File {name} tidak ditemukan.")
                return
            if picker.path() and picker.info and not picker.info.has_audio:
                common.error_box(self, f"File {name} tidak punya suara.")
                return
        fx_key = self.narr_fx.currentData()
        o = jobs.MergeOptions(
            video=vpath,
            output=jobs.output_name(vpath, " - SuaraKu", ".mp4"),
            narration=self.narr.path() or None,
            narration_offset=self.offset.value(),
            narration_volume=self.narr_vol.value() / 100.0,
            narration_effect=effects.preset_settings(fx_key) if fx_key else None,
            music=self.music.path() or None,
            music_volume=self.music_vol.value() / 100.0,
            music_loop=self.loop.isChecked(),
            music_fade=self.fade.isChecked(),
            duck_music=self.duck.isChecked(),
            keep_original=self.keep_orig_yes.isChecked(),
            original_volume=self.orig_vol.value() / 100.0,
            scale_720=self.small.isChecked(),
        )
        try:
            job = jobs.merge(o, info=self.video.info)
        except Exception as exc:
            common.error_box(self, str(exc))
            return
        self.player.unload()
        self.task.start([job], "Menggabungkan…")

    def _done(self, outputs: list) -> None:
        if outputs:
            self.player.load(outputs[-1], autoplay=True, label=f"Hasil: {Path(outputs[-1]).name}")
