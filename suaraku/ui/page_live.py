"""Menu Mode Live: ganti suara secara langsung saat telepon/video call.

mic asli → SuaraKu (efek real-time) → "CABLE Input" (VB-CABLE)
→ di WhatsApp Desktop / Teams pilih mikrofon "CABLE Output".
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioSink, QAudioSource, QMediaDevices
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QGridLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

from ..core import effects, live
from ..core.effects import VoiceSettings
from . import audio, claude_ui, common
from .common import card, hint, page_header, row
from .page_effect import VoiceControls

VB_CABLE_URL = "https://vb-audio.com/Cable/"
PITCH_PRESETS = {"macho", "husky", "raksasa", "tupai"}


class LiveEngine(QObject):
    out_ready = Signal(bytes)
    failed = Signal(str)
    level_in = Signal(float)
    level_out = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = self.src_io = None
        self.sink = self.sink_io = None
        self.mon = self.mon_io = None
        self.proc: live.LiveProcessor | None = None
        self.in_fmt = self.out_fmt = None
        self.dropped = 0
        self.monitor_note = ""
        self.out_ready.connect(self._play)

    @property
    def running(self) -> bool:
        return self.source is not None

    def start(self, in_dev, out_dev, monitor_dev, settings: VoiceSettings) -> None:
        self.in_fmt = audio.pick_format(in_dev, channels=(1, 2))
        self.out_fmt = audio.pick_format(out_dev, channels=(2, 1))
        self.dropped = 0
        self.sink = QAudioSink(out_dev, self.out_fmt, self)
        self.sink.setBufferSize(self.out_fmt.bytesForDuration(100_000))
        self.sink_io = self.sink.start()
        self.monitor_note = ""
        if monitor_dev is not None:
            if monitor_dev.isFormatSupported(self.out_fmt):
                self.mon = QAudioSink(monitor_dev, self.out_fmt, self)
                self.mon.setBufferSize(self.out_fmt.bytesForDuration(100_000))
                self.mon_io = self.mon.start()
            else:
                self.monitor_note = "Earphone/speaker ini tidak mendukung format suara Mode Live, jadi suaramu tidak diputar ulang."
        self._start_proc(settings)
        self.source = QAudioSource(in_dev, self.in_fmt, self)
        self.source.setBufferSize(self.in_fmt.bytesForDuration(40_000))
        self.src_io = self.source.start()
        self.src_io.readyRead.connect(self._mic)

    def _start_proc(self, settings: VoiceSettings) -> None:
        self.proc = live.LiveProcessor(settings, audio.to_pcm(self.in_fmt), audio.to_pcm(self.out_fmt),
                                       self.out_ready.emit, self.failed.emit)
        self.proc.start()

    def set_settings(self, settings: VoiceSettings) -> None:
        if not self.running:
            return
        old, self.proc = self.proc, None
        if old:
            old.stop()
        self._start_proc(settings)

    def _mic(self) -> None:
        if not self.src_io:
            return
        data = bytes(self.src_io.readAll().data())
        if not data:
            return
        self.level_in.emit(audio.peak(data, self.in_fmt.sampleFormat()))
        if self.proc:
            self.proc.feed(data)

    def _play(self, data: bytes) -> None:
        if not self.sink_io:
            return
        # Kalau antrean penuh, buang potongan ini supaya jeda tidak makin panjang.
        if self.sink.bytesFree() >= len(data):
            self.sink_io.write(data)
        else:
            self.dropped += len(data)
        if self.mon_io and self.mon.bytesFree() >= len(data):
            self.mon_io.write(data)
        self.level_out.emit(audio.peak(data, self.out_fmt.sampleFormat()))

    def queued_ms(self) -> int:
        if not self.sink or not self.out_fmt:
            return 0
        queued = max(0, self.sink.bufferSize() - self.sink.bytesFree())
        bps = max(1, self.out_fmt.bytesPerFrame() * self.out_fmt.sampleRate())
        return int(queued * 1000 / bps)

    def stop(self) -> None:
        if self.source:
            self.source.stop()
            self.source.deleteLater()
        if self.proc:
            self.proc.stop()
        for s in (self.sink, self.mon):
            if s:
                s.stop()
                s.deleteLater()
        self.source = self.src_io = self.sink = self.sink_io = self.mon = self.mon_io = None
        self.proc = None


class LivePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.engine = LiveEngine(self)
        self.engine.failed.connect(self._failed)
        self.engine.level_in.connect(lambda p: self.meter_in.setValue(int(p * 100)))
        self.engine.level_out.connect(lambda p: self.meter_out.setValue(int(p * 100)))
        self.devices = QMediaDevices(self)
        self.devices.audioInputsChanged.connect(self._load_devices)
        self.devices.audioOutputsChanged.connect(self._load_devices)
        self._ins: list = []
        self._outs: list = []
        self._restart = QTimer(self)
        self._restart.setSingleShot(True)
        self._restart.setInterval(350)
        self._restart.timeout.connect(lambda: self.engine.set_settings(self.controls.settings()))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(page_header(
            "Mode Live",
            "Ganti suaramu secara langsung saat telepon atau video call di laptop — WhatsApp "
            "Desktop, Teams, Zoom, Google Meet. Lawan bicara mendengar suara yang sudah diberi efek."))

        # --- VB-CABLE
        c1, l1 = card("1. Mic virtual (pasang sekali saja)")
        self.cable_lbl = QLabel("")
        self.cable_lbl.setWordWrap(True)
        dl = QPushButton("Download VB-CABLE (gratis)")
        dl.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(VB_CABLE_URL)))
        recheck = QPushButton("Cek ulang")
        recheck.clicked.connect(self._load_devices)
        self.cable_steps = hint(
            "Cara pasang: download VB-CABLE Driver Pack → extract ZIP → klik kanan "
            "VBCABLE_Setup_x64.exe → Run as administrator → Install Driver → restart laptop. "
            "Setelah itu muncul perangkat baru “CABLE Input” dan “CABLE Output”.")
        l1.addWidget(self.cable_lbl)
        l1.addWidget(self.cable_steps)
        l1.addLayout(row(dl, recheck, None))
        lay.addWidget(c1)

        # --- perangkat
        c2, l2 = card("2. Perangkat")
        self.mic = QComboBox()
        self.out = QComboBox()
        l2.addLayout(row(QLabel("Mic asli"), self.mic, None))
        l2.addLayout(row(QLabel("Kirim ke"), self.out, None))
        self.monitor = QCheckBox("Dengarkan suaraku sendiri di earphone (untuk mengecek efek)")
        l2.addWidget(self.monitor)
        l2.addWidget(hint("Wajib pakai earphone/headset saat menelepon, supaya suara lawan bicara "
                          "tidak masuk lagi ke mic."))
        lay.addWidget(c2)

        # --- gaya suara
        c3, l3 = card("3. Gaya suara",
                      "Boleh diganti saat sedang menelepon. Preset ubah nada (Macho, Husky, "
                      "Raksasa, Tupai) menambah jeda sekitar 0,1 detik.")
        grid = QGridLayout()
        grid.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.preset_btns: dict[str, QPushButton] = {}
        for i, key in enumerate(live.LIVE_PRESETS):
            p = effects.PRESETS_BY_KEY[key]
            b = QPushButton(p.name)
            b.setObjectName("Preset")
            b.setCheckable(True)
            b.setToolTip(p.hint)
            b.clicked.connect(lambda _c=False, k=key: self.apply_preset(k))
            self.group.addButton(b)
            self.preset_btns[key] = b
            grid.addWidget(b, i // 4, i % 4)
        l3.addLayout(grid)
        self.ask = claude_ui.VoiceAsk(self._from_claude)
        l3.addWidget(self.ask)
        l3.addWidget(self.ask.note)
        self.manual_btn = QPushButton("Atur manual ▾")
        self.manual_btn.setCheckable(True)
        self.controls = VoiceControls()
        self.controls.normalize.setText("Ratakan volume (kompresor ringan)")
        self.controls.setVisible(False)
        self.controls.changed.connect(self._manual_changed)
        self.manual_btn.toggled.connect(self._toggle_manual)
        l3.addLayout(row(self.manual_btn, None))
        l3.addWidget(self.controls)
        lay.addWidget(c3)

        # --- mulai
        c4, l4 = card("4. Mulai")
        self.go = QPushButton("▶  Mulai Mode Live")
        self.go.setObjectName("Primary")
        self.go.setMinimumHeight(48)
        self.go.clicked.connect(self._toggle)
        self.status = QLabel("Mati.")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        l4.addLayout(row(self.go, self.status, None))
        self.meter_in = QProgressBar()
        self.meter_in.setObjectName("Level")
        self.meter_in.setRange(0, 100)
        self.meter_out = QProgressBar()
        self.meter_out.setRange(0, 100)
        lbl_in, lbl_out = QLabel("Mic"), QLabel("Terkirim")
        for lb in (lbl_in, lbl_out):
            lb.setMinimumWidth(70)
        l4.addLayout(row(lbl_in, self.meter_in))
        l4.addLayout(row(lbl_out, self.meter_out))
        lay.addWidget(c4)

        # --- cara pakai
        c5, l5 = card("5. Pilih mic di aplikasi telepon")
        for t in [
            "WhatsApp Desktop: saat panggilan, buka pilihan perangkat audio (ikon ⋯ atau menu "
            "pengaturan panggilan) → Mikrofon: “CABLE Output (VB-Audio Virtual Cable)”.",
            "Microsoft Teams: Pengaturan → Perangkat → Mikrofon: “CABLE Output”. Atur Peredam "
            "bising (Noise suppression) ke Rendah/Mati supaya efeknya tidak dibersihkan Teams.",
            "Zoom / Google Meet: pengaturan audio → Mikrofon: “CABLE Output”.",
            "Selesai menelepon, kembalikan mikrofon ke mic asli — “CABLE Output” hanya bersuara "
            "saat Mode Live sedang jalan.",
        ]:
            l5.addWidget(hint("• " + t))
        snd = QPushButton("Buka pengaturan suara Windows")
        snd.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("ms-settings:sound")))
        l5.addLayout(row(snd, None))
        lay.addWidget(c5)
        lay.addStretch(1)

        self.lat_timer = QTimer(self)
        self.lat_timer.setInterval(500)
        self.lat_timer.timeout.connect(self._update_status)
        common.bind_setting(self.monitor, "live_monitor", False)
        self.apply_preset(str(common.settings().value("live_preset", "penyiar")))
        self._load_devices()

    # ------------------------------------------------------------ perangkat
    def _load_devices(self) -> None:
        if self.engine.running:
            return
        cur_in, cur_out = self.mic.currentText(), self.out.currentText()
        default_in = QMediaDevices.defaultAudioInput()
        self._ins = [d for d in QMediaDevices.audioInputs() if not audio.is_virtual_cable(d.description())]
        self._outs = list(QMediaDevices.audioOutputs())
        self.mic.clear()
        best_in = 0
        for i, d in enumerate(self._ins):
            self.mic.addItem(d.description(), i)
            if d.id() == default_in.id():
                best_in = i
        if not self._ins:
            self.mic.addItem("Tidak ada mikrofon terdeteksi", None)
        self.out.clear()
        cable = -1
        for i, d in enumerate(self._outs):
            self.out.addItem(d.description(), i)
            if cable < 0 and audio.is_virtual_cable(d.description()) and "input" in d.description().lower():
                cable = i
        if cable < 0:
            for i, d in enumerate(self._outs):
                if audio.is_virtual_cable(d.description()):
                    cable = i
                    break
        idx = self.mic.findText(cur_in)
        self.mic.setCurrentIndex(idx if idx >= 0 else best_in)
        idx = self.out.findText(cur_out)
        self.out.setCurrentIndex(idx if idx >= 0 else max(0, cable))
        if cable >= 0:
            self.cable_lbl.setText(f"✓ Mic virtual terpasang: {self._outs[cable].description()}")
            self.cable_lbl.setObjectName("Ok")
            self.cable_steps.hide()
        else:
            self.cable_lbl.setText("Mic virtual (VB-CABLE) belum terpasang. Tanpa ini, aplikasi "
                                   "telepon tidak bisa menerima suara dari SuaraKu.")
            self.cable_lbl.setObjectName("Err")
            self.cable_steps.show()
        self.cable_lbl.style().unpolish(self.cable_lbl)
        self.cable_lbl.style().polish(self.cable_lbl)

    def _pick(self, combo: QComboBox, devs: list):
        i = combo.currentData()
        return devs[i] if i is not None and 0 <= i < len(devs) else None

    # ------------------------------------------------------------ gaya suara
    def apply_preset(self, key: str) -> None:
        if key not in self.preset_btns:
            key = "penyiar"
        self.preset_btns[key].setChecked(True)
        self.controls.set_settings(effects.preset_settings(key))
        common.settings().setValue("live_preset", key)
        self._restart.start()

    def _from_claude(self, s: VoiceSettings) -> None:
        self.controls.set_settings(s)
        self._manual_changed()

    def _manual_changed(self) -> None:
        checked = self.group.checkedButton()
        if checked:
            self.group.setExclusive(False)
            checked.setChecked(False)
            self.group.setExclusive(True)
        self._restart.start()

    def _toggle_manual(self, on: bool) -> None:
        self.controls.setVisible(on)
        self.manual_btn.setText("Atur manual ▴" if on else "Atur manual ▾")

    # ------------------------------------------------------------ jalan / stop
    def _toggle(self) -> None:
        if self.engine.running:
            self.stop()
            return
        in_dev = self._pick(self.mic, self._ins)
        out_dev = self._pick(self.out, self._outs)
        if in_dev is None or out_dev is None:
            common.error_box(self, "Pilih mic asli dan tujuan (CABLE Input) dulu.")
            return
        if not audio.is_virtual_cable(out_dev.description()):
            ans = QMessageBox.question(
                self, "Bukan mic virtual",
                "Tujuan yang dipilih bukan VB-CABLE, jadi suaranya cuma keluar di speaker/earphone "
                "dan tidak terkirim ke aplikasi telepon. Lanjut untuk coba-coba saja?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        monitor = QMediaDevices.defaultAudioOutput() if self.monitor.isChecked() else None
        if monitor is not None and monitor.id() == out_dev.id():
            monitor = None
        try:
            self.engine.start(in_dev, out_dev, monitor, self.controls.settings())
        except Exception as exc:
            self.engine.stop()
            common.error_box(self, f"Mode Live tidak bisa dimulai: {exc}")
            return
        self.go.setText("■  Stop Mode Live")
        for w in (self.mic, self.out, self.monitor):
            w.setEnabled(False)
        self.lat_timer.start()
        self._update_status()

    def _update_status(self) -> None:
        if not self.engine.running:
            return
        key = self.group.checkedButton()
        extra = 120 if (key and any(self.preset_btns[k] is key for k in PITCH_PRESETS)) else 30
        if abs(self.controls.pitch.value()) >= 0.05:
            extra = max(extra, 120)
        ms = 40 + extra + self.engine.queued_ms()
        note = f" {self.engine.monitor_note}" if self.engine.monitor_note else ""
        self.status.setText(f"● Aktif · jeda sekitar {ms} ms. Pilih “CABLE Output” sebagai "
                            f"mikrofon di aplikasi telepon.{note}")

    def _failed(self, msg: str) -> None:
        if self.engine.running:
            self.stop()
            common.error_box(self, msg)

    def stop(self) -> None:
        self.lat_timer.stop()
        self.engine.stop()
        self.go.setText("▶  Mulai Mode Live")
        self.status.setText("Mati.")
        self.meter_in.setValue(0)
        self.meter_out.setValue(0)
        for w in (self.mic, self.out, self.monitor):
            w.setEnabled(True)

    def shutdown(self) -> None:
        if self.engine.running:
            self.stop()
