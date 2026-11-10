"""Menu Rekam: rekam narasi dari mic, dengan teleprompter dan video referensi."""

from __future__ import annotations

import array
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from ..core import jobs
from . import common
from .common import FILTER_VIDEO, FilePicker, LabeledSlider, Player, TaskPanel, card, hint, page_header, row

_FMT_NAMES = {
    QAudioFormat.SampleFormat.Int16: ("s16le", 2, "h", 32768.0),
    QAudioFormat.SampleFormat.Int32: ("s32le", 4, "i", 2147483648.0),
    QAudioFormat.SampleFormat.Float: ("f32le", 4, "f", 1.0),
    QAudioFormat.SampleFormat.UInt8: ("u8", 1, None, 128.0),
}


class Recorder(QObject):
    level = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source: QAudioSource | None = None
        self.io = None
        self.fh = None
        self.raw_path = ""
        self.nbytes = 0
        self.fmt = QAudioFormat()

    @property
    def active(self) -> bool:
        return self.source is not None

    def start(self, device) -> None:
        fmt = QAudioFormat()
        fmt.setSampleRate(48000)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
        if fmt.sampleFormat() not in _FMT_NAMES:
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self.fmt = fmt
        self.raw_path = common.temp_file("rekaman", ".raw")
        self.fh = open(self.raw_path, "wb")
        self.nbytes = 0
        self.source = QAudioSource(device, fmt, self)
        self.source.setBufferSize(fmt.bytesForDuration(200_000))
        self.io = self.source.start()
        self.io.readyRead.connect(self._read)

    def seconds(self) -> float:
        bpf = max(1, self.fmt.bytesPerFrame())
        return self.nbytes / float(bpf * max(1, self.fmt.sampleRate()))

    def _read(self) -> None:
        if not self.io or not self.fh:
            return
        data = bytes(self.io.readAll().data())
        if not data:
            return
        self.fh.write(data)
        self.nbytes += len(data)
        name, width, code, full = _FMT_NAMES[self.fmt.sampleFormat()]
        usable = len(data) - (len(data) % width)
        if usable <= 0:
            return
        if code is None:
            peak = max(abs(b - 128) for b in data[:usable]) / full
        else:
            arr = array.array(code)
            arr.frombytes(data[:usable])
            peak = max(abs(max(arr)), abs(min(arr))) / full if len(arr) else 0.0
        self.level.emit(min(1.0, float(peak)))

    def stop(self) -> tuple[str, int, int, str]:
        if self.source:
            self._read()
            self.source.stop()
            self.source.deleteLater()
        if self.fh:
            self.fh.close()
        self.source = self.io = self.fh = None
        name = _FMT_NAMES[self.fmt.sampleFormat()][0]
        return self.raw_path, self.fmt.sampleRate(), self.fmt.channelCount(), name


class Teleprompter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            "Tempel naskah narasi di sini…\n\n"
            "Tips: minta Claude bikinin naskahnya dari poin-poin yang mau kamu pamerin, "
            "lalu tempel di sini. Saat merekam, teks akan bergulir pelan-pelan.")
        self.font_slider = LabeledSlider("Ukuran huruf", 14, 48, 24, unit="pt")
        self.speed = LabeledSlider("Kecepatan gulir", 1, 10, 3)
        self.font_slider.valueChanged.connect(self._font)
        self.auto = QCheckBox("Gulir otomatis saat merekam")
        self.auto.setChecked(True)
        self.try_btn = QPushButton("Coba gulir")
        self.try_btn.setCheckable(True)
        self.try_btn.toggled.connect(lambda on: self.start() if on else self.stop())
        top = QPushButton("Ke atas")
        top.clicked.connect(lambda: self.text.verticalScrollBar().setValue(0))
        lay.addWidget(self.text, 1)
        lay.addWidget(self.font_slider)
        lay.addWidget(self.speed)
        lay.addLayout(row(self.auto, None, self.try_btn, top))
        self._acc = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._tick)
        self._font(self.font_slider.value())

    def _font(self, v: float) -> None:
        f = QFont(self.text.font())
        f.setPointSizeF(v)
        self.text.setFont(f)

    def start(self) -> None:
        self._acc = 0.0
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()
        if self.try_btn.isChecked():
            self.try_btn.blockSignals(True)
            self.try_btn.setChecked(False)
            self.try_btn.blockSignals(False)

    def _tick(self) -> None:
        self._acc += self.speed.value() * 0.35 * (self.font_slider.value() / 24.0)
        step = int(self._acc)
        if step:
            self._acc -= step
            sb = self.text.verticalScrollBar()
            sb.setValue(sb.value() + step)


class RecordPage(QWidget):
    use_in_effect = Signal(str)
    use_in_merge = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.recorder = Recorder(self)
        self.recorder.level.connect(self._level)
        self.devices = QMediaDevices(self)
        self.devices.audioInputsChanged.connect(self._load_devices)
        self.last_file = ""
        self._t0 = 0.0
        self._quiet_since = 0.0
        self._count = 0
        self._devs: list = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(page_header(
            "Rekam Suara",
            "Rekam narasimu langsung dari mic. Naskah bisa ditampilkan sebagai teleprompter, "
            "dan video bisa diputar (tanpa suara) supaya narasi pas dengan gambarnya."))

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        ll.setSpacing(14)

        c1, l1 = card("Mikrofon")
        self.mic = QComboBox()
        refresh = QPushButton("Muat ulang")
        refresh.clicked.connect(self._load_devices)
        l1.addLayout(row(self.mic, refresh))
        self.meter = QProgressBar()
        self.meter.setObjectName("Level")
        self.meter.setRange(0, 100)
        self.meter.setValue(0)
        l1.addWidget(self.meter)
        self.mic_hint = hint("Tips: earphone yang ada mic-nya biasanya lebih jernih daripada mic "
                             "bawaan laptop. Rekam di ruangan yang tidak bergema.")
        l1.addWidget(self.mic_hint)
        ll.addWidget(c1)

        c2, l2 = card("Rekam")
        self.rec_btn = QPushButton("●  Mulai rekam")
        self.rec_btn.setObjectName("Record")
        self.rec_btn.clicked.connect(self._toggle)
        self.timer_lbl = QLabel("0:00")
        self.timer_lbl.setObjectName("Big")
        self.countdown = QCheckBox("Hitung mundur 3 detik")
        self.countdown.setChecked(True)
        l2.addLayout(row(self.rec_btn, None, self.timer_lbl))
        l2.addWidget(self.countdown)
        ll.addWidget(c2)

        c3, l3 = card("Video referensi (opsional)",
                      "Video diputar tanpa suara saat kamu mulai merekam, jadi narasi bisa pas "
                      "dengan gambar. Nanti di menu Gabung, narasinya tinggal dipasang dari detik 0.")
        self.vid_pick = FilePicker("Pilih video…", FILTER_VIDEO)
        self.vid_player = Player(video=True)
        self.vid_player.set_muted(True)
        self.vid_pick.changed.connect(self._video_changed)
        l3.addWidget(self.vid_pick)
        l3.addWidget(self.vid_player, 1)
        ll.addWidget(c3, 1)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        c4, l4 = card("Teleprompter")
        self.prompter = Teleprompter()
        l4.addWidget(self.prompter, 1)
        rl.addWidget(c4, 1)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        c5, l5 = card("Hasil rekaman")
        self.result_player = Player()
        self.task = TaskPanel()
        self.to_fx = QPushButton("Pakai di Efek Suara →")
        self.to_merge = QPushButton("Pakai di Gabung Video →")
        self.open_btn = QPushButton("Buka folder")
        for b in (self.to_fx, self.to_merge, self.open_btn):
            b.setEnabled(False)
        self.to_fx.clicked.connect(lambda: self.use_in_effect.emit(self.last_file))
        self.to_merge.clicked.connect(lambda: self.use_in_merge.emit(self.last_file))
        self.open_btn.clicked.connect(lambda: common.show_in_folder(self.last_file))
        self.task.finished.connect(self._saved)
        l5.addWidget(self.result_player)
        l5.addWidget(self.task)
        l5.addLayout(row(self.to_fx, self.to_merge, None, self.open_btn))
        lay.addWidget(c5)

        self.clock = QTimer(self)
        self.clock.setInterval(200)
        self.clock.timeout.connect(self._clock)
        self.count_timer = QTimer(self)
        self.count_timer.setInterval(1000)
        self.count_timer.timeout.connect(self._count_tick)
        self._load_devices()

    # ------------------------------------------------------------ perangkat
    def _load_devices(self) -> None:
        if self.recorder.active:
            return
        current = self.mic.currentText()
        self.mic.clear()
        default = QMediaDevices.defaultAudioInput()
        self._devs = list(QMediaDevices.audioInputs())
        for i, dev in enumerate(self._devs):
            label = dev.description() + ("  (bawaan)" if dev.id() == default.id() else "")
            self.mic.addItem(label, i)
        if self.mic.count() == 0:
            self.mic.addItem("Tidak ada mikrofon terdeteksi", None)
        idx = self.mic.findText(current)
        if idx >= 0:
            self.mic.setCurrentIndex(idx)
        else:
            for i in range(self.mic.count()):
                if self.mic.itemText(i).endswith("(bawaan)"):
                    self.mic.setCurrentIndex(i)

    def _video_changed(self, path: str) -> None:
        if path and self.vid_pick.info and self.vid_pick.info.is_video:
            self.vid_player.load(path)
        else:
            self.vid_player.unload()

    # ------------------------------------------------------------ rekam
    def _toggle(self) -> None:
        if self.recorder.active:
            self._stop()
        elif self.count_timer.isActive():
            self.count_timer.stop()
            self._set_btn(False)
            self.timer_lbl.setText("0:00")
        else:
            dev = self._device()
            if dev is None:
                common.error_box(self, "Mikrofon tidak terdeteksi. Colokkan mic/earphone lalu klik "
                                       "Muat ulang.")
                return
            self.result_player.unload()
            if self.countdown.isChecked():
                self._count = 3
                self.timer_lbl.setText("3")
                self.rec_btn.setText("Batal")
                self.count_timer.start()
            else:
                self._start()

    def _count_tick(self) -> None:
        self._count -= 1
        if self._count <= 0:
            self.count_timer.stop()
            self._start()
        else:
            self.timer_lbl.setText(str(self._count))

    def _device(self):
        idx = self.mic.currentData()
        if idx is None or not (0 <= idx < len(self._devs)):
            return None
        return self._devs[idx]

    def _start(self) -> None:
        dev = self._device()
        try:
            self.recorder.start(dev)
        except Exception as exc:
            common.error_box(self, f"Tidak bisa mulai merekam: {exc}")
            self._set_btn(False)
            return
        self._t0 = time.monotonic()
        self._quiet_since = self._t0
        self.mic_hint.setObjectName("Hint")
        self._repolish(self.mic_hint)
        self._set_btn(True)
        self.clock.start()
        if self.vid_pick.path() and self.vid_player.play_btn.isEnabled():
            self.vid_player.set_muted(True)
            self.vid_player.play_from_start()
        if self.prompter.auto.isChecked():
            self.prompter.start()

    def _stop(self) -> None:
        self.clock.stop()
        self.prompter.stop()
        self.vid_player.stop()
        raw, sr, ch, fmt = self.recorder.stop()
        self._set_btn(False)
        self.meter.setValue(0)
        if not raw or not os.path.exists(raw) or os.path.getsize(raw) == 0:
            common.error_box(self, "Tidak ada suara yang terekam. Cek mikrofon dan izin mikrofon "
                                   "Windows (Settings → Privacy → Microphone).")
            return
        dst = str(Path(common.work_dir("Rekaman")) / f"rekaman-{common.stamp()}.wav")
        job = jobs.raw_to_wav(raw, dst, sr, ch, fmt)
        job.cleanup.append(raw)
        self.task.start([job], "Menyimpan rekaman…")

    def _saved(self, outputs: list) -> None:
        if not outputs:
            return
        self.last_file = outputs[-1]
        self.result_player.load(self.last_file)
        for b in (self.to_fx, self.to_merge, self.open_btn):
            b.setEnabled(True)

    def _set_btn(self, recording: bool) -> None:
        self.rec_btn.setText("■  Stop" if recording else "●  Mulai rekam")
        self.rec_btn.setProperty("recording", "true" if recording else "false")
        self._repolish(self.rec_btn)
        self.mic.setEnabled(not recording)

    @staticmethod
    def _repolish(w: QWidget) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    def _clock(self) -> None:
        secs = self.recorder.seconds()
        m, s = divmod(int(secs), 60)
        self.timer_lbl.setText(f"{m}:{s:02d}")
        if time.monotonic() - self._quiet_since > 4 and self.mic_hint.objectName() != "Err":
            self.mic_hint.setText("Belum ada suara yang masuk. Cek mic yang dipilih, dan izin "
                                  "mikrofon Windows: Settings → Privacy → Microphone → "
                                  "Allow desktop apps.")
            self.mic_hint.setObjectName("Err")
            self._repolish(self.mic_hint)

    def _level(self, peak: float) -> None:
        self.meter.setValue(int(peak * 100))
        if peak > 0.02:
            self._quiet_since = time.monotonic()
            if self.mic_hint.objectName() == "Err":
                self.mic_hint.setText("Suara masuk. Lanjut!")
                self.mic_hint.setObjectName("Ok")
                self._repolish(self.mic_hint)

    def shutdown(self) -> None:
        if self.recorder.active:
            self._stop()
