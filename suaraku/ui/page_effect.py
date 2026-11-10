"""Menu Efek Suara: preset (Penyiar, Macho, Husky, …) + atur manual + dengar contoh."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QGridLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from ..core import effects, jobs, tools
from ..core.effects import VoiceSettings
from . import common
from .common import FILTER_MEDIA, FilePicker, LabeledSlider, Player, TaskPanel, card, hint, page_header, row


class VoiceControls(QWidget):
    """Kumpulan slider untuk atur suara manual. Dipakai juga oleh menu lain."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.pitch = LabeledSlider("Nada (pitch)", -12, 12, 0, step=0.5, unit="st",
                                   tip="Negatif = suara lebih berat. −2 sampai −4 masih natural.")
        self.bass = LabeledSlider("Bass", -12, 12, 0, step=0.5, unit="dB",
                                  tip="Tebal-tipisnya suara bawah.")
        self.presence = LabeledSlider("Kejelasan", -12, 12, 0, step=0.5, unit="dB",
                                      tip="Bikin kata-kata lebih jelas terdengar.")
        self.treble = LabeledSlider("Treble", -12, 12, 0, step=0.5, unit="dB",
                                    tip="Turunkan supaya suara lebih lembut/hangat.")
        self.reverb = LabeledSlider("Gema", 0, 100, 0, unit="%")
        self.denoise = LabeledSlider("Pembersih noise", 0, 100, 0, unit="%",
                                     tip="Membuang suara kipas, AC, dengung. Terlalu tinggi bisa bikin suara aneh.")
        self.compress = QCheckBox("Padatkan volume (suara lebih tebal & stabil)")
        self.deess = QCheckBox("Redam desis huruf “s”")
        self.normalize = QCheckBox("Samakan volume (standar media sosial)")
        self.robot = QCheckBox("Robot")
        self._extra = VoiceSettings()
        for s in (self.pitch, self.bass, self.presence, self.treble, self.reverb, self.denoise):
            lay.addWidget(s)
            s.valueChanged.connect(lambda _v: self.changed.emit())
        grid = QGridLayout()
        grid.addWidget(self.compress, 0, 0)
        grid.addWidget(self.deess, 0, 1)
        grid.addWidget(self.normalize, 1, 0)
        grid.addWidget(self.robot, 1, 1)
        lay.addLayout(grid)
        for c in (self.compress, self.deess, self.normalize, self.robot):
            c.toggled.connect(lambda _v: self.changed.emit())

    def set_settings(self, s: VoiceSettings) -> None:
        widgets = [self.pitch, self.bass, self.presence, self.treble, self.reverb, self.denoise,
                   self.compress, self.deess, self.normalize, self.robot]
        for w in widgets:
            w.blockSignals(True)
        self._extra = s
        self.pitch.setValue(s.pitch)
        self.bass.setValue(s.bass)
        self.presence.setValue(s.presence)
        self.treble.setValue(s.treble)
        self.reverb.setValue(s.reverb)
        self.denoise.setValue(s.denoise)
        self.compress.setChecked(s.compress)
        self.deess.setChecked(s.deess)
        self.normalize.setChecked(s.normalize)
        self.robot.setChecked(s.robot)
        for w in widgets:
            w.blockSignals(False)

    def settings(self) -> VoiceSettings:
        e = self._extra
        return VoiceSettings(
            pitch=self.pitch.value(), formant_shift=e.formant_shift, bass=self.bass.value(),
            mud_cut=e.mud_cut, presence=self.presence.value(), treble=self.treble.value(),
            highpass=e.highpass if e.highpass else (80 if self.denoise.value() > 0 else 0),
            lowpass=e.lowpass, denoise=int(self.denoise.value()), deess=self.deess.isChecked(),
            compress=self.compress.isChecked(), reverb=int(self.reverb.value()),
            robot=self.robot.isChecked(), normalize=self.normalize.isChecked(),
        )


class EffectPage(QWidget):
    use_in_merge = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_file = ""
        self._preview_kind = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(page_header(
            "Efek Suara",
            "Bikin suaramu terdengar lebih keren: pilih gaya, dengarkan contohnya, lalu simpan. "
            "Bisa untuk file audio, atau langsung ke suara di dalam video."))

        c1, l1 = card("1. File suara atau video")
        self.pick = FilePicker("Pilih rekaman / audio / video…", FILTER_MEDIA)
        self.pick.changed.connect(self._file_changed)
        l1.addWidget(self.pick)
        lay.addWidget(c1)

        c2, l2 = card("2. Pilih gaya suara")
        grid = QGridLayout()
        grid.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.preset_btns: dict[str, QPushButton] = {}
        for i, p in enumerate(effects.PRESETS):
            b = QPushButton(p.name)
            b.setObjectName("Preset")
            b.setCheckable(True)
            b.setToolTip(p.hint)
            b.clicked.connect(lambda _c=False, k=p.key: self.apply_preset(k))
            self.group.addButton(b)
            self.preset_btns[p.key] = b
            grid.addWidget(b, i // 4, i % 4)
        l2.addLayout(grid)
        self.preset_hint = hint("")
        l2.addWidget(self.preset_hint)
        self.manual_btn = QPushButton("Atur manual ▾")
        self.manual_btn.setCheckable(True)
        self.controls = VoiceControls()
        self.controls.setVisible(False)
        self.controls.changed.connect(self._manual_changed)
        self.manual_btn.toggled.connect(self._toggle_manual)
        l2.addLayout(row(self.manual_btn, None))
        l2.addWidget(self.controls)
        lay.addWidget(c2)

        c3, l3 = card("3. Dengar contoh",
                      "Bandingkan potongan 12 detik sebelum dan sesudah efek.")
        self.prev_start = QLineEdit()
        self.prev_start.setPlaceholderText("0:00")
        self.prev_start.setMaximumWidth(90)
        self.btn_after = QPushButton("▶ Dengar dengan efek")
        self.btn_after.setObjectName("Primary")
        self.btn_before = QPushButton("▶ Dengar aslinya")
        self.btn_after.clicked.connect(lambda: self._preview(True))
        self.btn_before.clicked.connect(lambda: self._preview(False))
        self.player = Player()
        self.prev_task = TaskPanel()
        self.prev_task.finished.connect(self._preview_ready)
        l3.addLayout(row(QLabel("Mulai dari"), self.prev_start, self.btn_after, self.btn_before, None))
        l3.addWidget(self.player)
        l3.addWidget(self.prev_task)
        lay.addWidget(c3)

        c4, l4 = card("4. Simpan")
        self.out_fmt = QComboBox()
        self._fill_formats(False)
        self.save_btn = QPushButton("Simpan hasil")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self._save)
        self.to_merge = QPushButton("Pakai di Gabung Video →")
        self.to_merge.setEnabled(False)
        self.to_merge.clicked.connect(lambda: self.use_in_merge.emit(self.last_file))
        self.task = TaskPanel()
        self.task.finished.connect(self._saved)
        self.task.busyChanged.connect(lambda b: self.save_btn.setEnabled(not b))
        l4.addLayout(row(QLabel("Simpan sebagai"), self.out_fmt, self.save_btn, None, self.to_merge))
        l4.addWidget(self.task)
        lay.addWidget(c4)
        lay.addWidget(hint("Catatan jujur: ini olah suara (nada, bass, gema), bukan AI pengubah "
                           "suara — hasilnya tetap suaramu, tapi lebih “mahal”. Kalau nada diturunkan "
                           "lebih dari −5, mulai terdengar seperti robot/monster."))
        lay.addStretch(1)
        self.apply_preset("penyiar")

    # ------------------------------------------------------------
    def set_file(self, path: str) -> None:
        self.pick.set_path(path)

    def _fill_formats(self, is_video: bool) -> None:
        self.out_fmt.clear()
        if is_video:
            self.out_fmt.addItem("Video MP4 (gambar tetap, suara diganti)", "video")
        self.out_fmt.addItem("MP3", ".mp3")
        self.out_fmt.addItem("WAV", ".wav")
        self.out_fmt.addItem("M4A", ".m4a")

    def _file_changed(self, path: str) -> None:
        info = self.pick.info
        self._fill_formats(bool(info and info.is_video))
        self.player.unload()

    def apply_preset(self, key: str) -> None:
        p = effects.PRESETS_BY_KEY[key]
        self.preset_btns[key].setChecked(True)
        self.preset_hint.setText(p.hint)
        self.controls.set_settings(effects.preset_settings(key))

    def _manual_changed(self) -> None:
        self.preset_hint.setText("Pengaturan manual.")
        checked = self.group.checkedButton()
        if checked:
            self.group.setExclusive(False)
            checked.setChecked(False)
            self.group.setExclusive(True)

    def _toggle_manual(self, on: bool) -> None:
        self.controls.setVisible(on)
        self.manual_btn.setText("Atur manual ▴" if on else "Atur manual ▾")

    def _need_file(self) -> tools.MediaInfo | None:
        info = self.pick.info
        if not self.pick.path() or not info:
            common.error_box(self, "Pilih file suara atau video dulu.")
            return None
        if not info.has_audio:
            common.error_box(self, "File ini tidak punya suara.")
            return None
        return info

    def _preview(self, with_effect: bool) -> None:
        info = self._need_file()
        if not info or self.prev_task.busy:
            return
        try:
            start = tools.parse_time(self.prev_start.text()) or 0.0
        except ValueError as exc:
            common.error_box(self, str(exc))
            return
        self.player.unload()
        out = common.temp_file("contoh", ".wav")
        s = self.controls.settings() if with_effect else VoiceSettings(normalize=False)
        job = jobs.voice_effect(self.pick.path(), out, s, preview_seconds=12,
                                preview_start=start, info=info)
        self._preview_kind = "dengan efek" if with_effect else "asli"
        self.prev_task.start([job], "Menyiapkan contoh…")

    def _preview_ready(self, outputs: list) -> None:
        if outputs:
            self.player.load(outputs[-1], autoplay=True, label=f"Contoh ({self._preview_kind})")
            self.prev_task.bar.hide()
            self.prev_task.open_btn.hide()
            self.prev_task.status.setText("")

    def _save(self) -> None:
        info = self._need_file()
        if not info:
            return
        kind = self.out_fmt.currentData()
        keep_video = kind == "video"
        ext = ".mp4" if keep_video else kind
        dst = jobs.output_name(self.pick.path(), " - efek", ext)
        try:
            job = jobs.voice_effect(self.pick.path(), dst, self.controls.settings(),
                                    keep_video=keep_video, info=info)
        except Exception as exc:
            common.error_box(self, str(exc))
            return
        self.player.unload()
        self.task.start([job], "Menerapkan efek…")

    def _saved(self, outputs: list) -> None:
        if outputs:
            self.last_file = outputs[-1]
            self.player.load(self.last_file, label=f"Hasil: {Path(self.last_file).name}")
            self.to_merge.setEnabled(not self.last_file.lower().endswith(".mp4"))
