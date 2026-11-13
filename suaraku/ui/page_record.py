"""Menu Rekam: rekam narasi dari mic (manual atau otomatis), teleprompter, video referensi,
lalu langsung diberi efek dan diketik jadi teks kalau diminta."""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QSplitter, QVBoxLayout, QWidget,
)

from ..core import effects, jobs, stt, tools, vad
from . import audio, common
from .common import (
    FILTER_VIDEO, FilePicker, LabeledSlider, Player, TaskPanel, card, hint, page_header, row,
)
from .page_transcribe import SttThread, current_model, language_combo


class Recorder(QObject):
    level = Signal(float)
    voice_started = Signal()
    voice_stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source: QAudioSource | None = None
        self.io = None
        self.fh = None
        self.raw_path = ""
        self.nbytes = 0
        self.fmt = QAudioFormat()
        self.gate: vad.VoiceGate | None = None

    @property
    def active(self) -> bool:
        return self.source is not None

    @property
    def capturing(self) -> bool:
        """True kalau suara sedang benar-benar direkam (bukan siaga)."""
        return self.active and (self.gate is None or self.gate.state != vad.VoiceGate.WAITING)

    def start(self, device, gate_cfg: dict | None = None) -> None:
        fmt = audio.pick_format(device, channels=(1, 2), rates=(48000, 44100))
        self.fmt = fmt
        self.raw_path = common.temp_file("rekaman", ".raw")
        self.fh = open(self.raw_path, "wb")
        self.nbytes = 0
        bps = fmt.bytesPerFrame() * fmt.sampleRate()
        self.gate = (vad.VoiceGate(bps, frame_bytes=fmt.bytesPerFrame(), **gate_cfg)
                     if gate_cfg is not None else None)
        self.source = QAudioSource(device, fmt, self)
        self.source.setBufferSize(fmt.bytesForDuration(100_000))
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
        p = audio.peak(data, self.fmt.sampleFormat())
        self.level.emit(p)
        if self.gate is None:
            self.fh.write(data)
            self.nbytes += len(data)
            return
        out, event = self.gate.feed(data, p)
        if out:
            self.fh.write(out)
            self.nbytes += len(out)
        if event == "start":
            self.voice_started.emit()
        elif event == "stop":
            self.voice_stopped.emit()

    def stop(self) -> tuple[str, int, int, str, bool]:
        """Kembalikan (file mentah, sample rate, channel, format FFmpeg, ada_suara)."""
        if self.source:
            self._read()
            self.source.stop()
            self.source.deleteLater()
        has_audio = self.nbytes > 0
        if self.fh:
            if self.gate is not None:
                if self.gate.state == vad.VoiceGate.WAITING:
                    has_audio = False
                else:
                    self.fh.truncate(self.gate.keep_bytes())
            self.fh.close()
        self.source = self.io = self.fh = None
        pcm = audio.to_pcm(self.fmt)
        return self.raw_path, pcm.sample_rate, pcm.channels, pcm.sample_fmt, has_audio


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
        ask = QPushButton("✨ Bikin naskah dengan Claude")
        ask.clicked.connect(lambda: common.navigate("claude"))
        lay.addLayout(row(ask, None))
        lay.addWidget(self.text, 1)
        lay.addWidget(self.font_slider)
        lay.addWidget(self.speed)
        lay.addLayout(row(self.auto, None, self.try_btn, top))
        self._acc = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._tick)
        self._font(self.font_slider.value())
        common.bind_setting(self.font_slider, "prompter_font", 24)
        common.bind_setting(self.speed, "prompter_speed", 3)

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
    use_in_cut = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.recorder = Recorder(self)
        self.recorder.level.connect(self._level)
        self.recorder.voice_started.connect(self._voice_started)
        self.recorder.voice_stopped.connect(self._stop)
        self.devices = QMediaDevices(self)
        self.devices.audioInputsChanged.connect(self._load_devices)
        self.last_file = ""
        self.last_wav = ""
        self.stt_thread: SttThread | None = None
        self._pending_stt = False
        self._t0 = 0.0
        self._quiet_since = 0.0
        self._count = 0
        self._devs: list = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(page_header(
            "Rekam Suara",
            "Rekam narasimu dari mic — tekan tombol, atau biarkan otomatis mulai saat kamu bicara "
            "dan berhenti sendiri saat kamu diam. Setelahnya bisa langsung diberi efek dan "
            "diketik jadi teks."))

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        ll.setSpacing(14)

        # --- mikrofon
        c1, l1 = card("Mikrofon")
        self.mic = QComboBox()
        refresh = QPushButton("Muat ulang")
        refresh.clicked.connect(self._load_devices)
        l1.addLayout(row(self.mic, refresh))
        self.meter = QProgressBar()
        self.meter.setObjectName("Level")
        self.meter.setRange(0, 100)
        self.meter.setValue(0)
        self.db_lbl = QLabel("")
        self.db_lbl.setObjectName("Info")
        self.db_lbl.setMinimumWidth(70)
        l1.addLayout(row(self.meter, self.db_lbl))
        self.mic_hint = hint("Tips: earphone yang ada mic-nya biasanya lebih jernih daripada mic "
                             "bawaan laptop. Rekam di ruangan yang tidak bergema.")
        l1.addWidget(self.mic_hint)
        ll.addWidget(c1)

        # --- cara merekam
        c2, l2 = card("Cara merekam")
        self.mode_manual = QRadioButton("Manual — tekan Mulai & Stop")
        self.mode_auto = QRadioButton("Otomatis — mulai saat aku bicara, berhenti saat diam")
        grp = QButtonGroup(self)
        grp.addButton(self.mode_manual)
        grp.addButton(self.mode_auto)
        self.mode_manual.setChecked(True)
        l2.addWidget(self.mode_manual)
        l2.addWidget(self.mode_auto)
        self.auto_box = QWidget()
        al = QVBoxLayout(self.auto_box)
        al.setContentsMargins(22, 0, 0, 0)
        al.setSpacing(4)
        self.threshold = LabeledSlider("Ambang suara", -60, -20, -42, unit="dB",
                                       tip="Suara di atas ambang ini dianggap bicara. Kalau rekaman "
                                           "mulai sendiri karena bising, naikkan (mis. −35). "
                                           "Kalau tidak mau mulai, turunkan (mis. −50).")
        self.stop_after = LabeledSlider("Berhenti setelah diam", 1, 8, 2.5, step=0.5, unit="detik")
        al.addWidget(self.threshold)
        al.addWidget(self.stop_after)
        al.addWidget(hint("Lihat angka dB di samping indikator volume: ambang sebaiknya di antara "
                          "angka saat kamu diam dan saat kamu bicara."))
        l2.addWidget(self.auto_box)
        self.compact = QCheckBox("Rapatkan jeda hening yang panjang")
        l2.addWidget(self.compact)
        ll.addWidget(c2)

        # --- tombol rekam
        c3, l3 = card("Rekam")
        self.rec_btn = QPushButton("●  Mulai rekam")
        self.rec_btn.setObjectName("Record")
        self.rec_btn.clicked.connect(self._toggle)
        self.timer_lbl = QLabel("0:00")
        self.timer_lbl.setObjectName("Big")
        self.state_lbl = QLabel("")
        self.state_lbl.setObjectName("Hint")
        self.state_lbl.setWordWrap(True)
        self.countdown = QCheckBox("Hitung mundur 3 detik")
        self.countdown.setChecked(True)
        l3.addLayout(row(self.rec_btn, None, self.timer_lbl))
        l3.addWidget(self.state_lbl)
        l3.addWidget(self.countdown)
        ll.addWidget(c3)

        # --- video referensi
        c4, l4 = card("Video referensi (opsional)",
                      "Video diputar tanpa suara saat kamu mulai bicara/merekam, supaya narasi pas "
                      "dengan gambar. Di menu Gabung, narasinya tinggal dipasang dari detik 0.")
        self.vid_pick = FilePicker("Pilih video…", FILTER_VIDEO)
        self.vid_player = Player(video=True)
        self.vid_player.set_muted(True)
        self.vid_pick.changed.connect(self._video_changed)
        l4.addWidget(self.vid_pick)
        l4.addWidget(self.vid_player, 1)
        ll.addWidget(c4, 1)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        c5, l5 = card("Teleprompter")
        self.prompter = Teleprompter()
        l5.addWidget(self.prompter, 1)
        rl.addWidget(c5, 1)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        # --- setelah rekam
        c6, l6 = card("Setelah rekam, otomatis…")
        self.fx_on = QCheckBox("Beri efek suara")
        self.fx = QComboBox()
        for p in effects.PRESETS:
            if p.key != "asli":
                self.fx.addItem(p.name, p.key)
        self.fx.setCurrentIndex(max(0, self.fx.findData("penyiar")))
        self.stt_on = QCheckBox("Ketik jadi teks")
        self.stt_lang = language_combo()
        l6.addLayout(row(self.fx_on, self.fx, None))
        l6.addLayout(row(self.stt_on, self.stt_lang, None))
        l6.addWidget(hint("Rekaman asli tetap disimpan. Hasil efek disimpan sebagai file terpisah; "
                          "teks disimpan sebagai .txt dan subtitle .srt di folder Documents\\SuaraKu\\Rekaman."))
        lay.addWidget(c6)

        # --- hasil
        c7, l7 = card("Hasil rekaman")
        self.result_player = Player()
        self.task = TaskPanel()
        self.task.finished.connect(self._saved)
        self.stt_status = QLabel("")
        self.stt_status.setObjectName("Hint")
        self.stt_status.setWordWrap(True)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setMaximumHeight(160)
        self.transcript.hide()
        self.copy_btn = QPushButton("Salin teks")
        self.copy_btn.hide()
        self.copy_btn.clicked.connect(self._copy_text)
        self.to_fx = QPushButton("Pakai di Efek Suara →")
        self.to_cut = QPushButton("Potong / rapikan →")
        self.to_merge = QPushButton("Pakai di Gabung Video →")
        self.open_btn = QPushButton("Buka folder")
        for b in (self.to_fx, self.to_cut, self.to_merge, self.open_btn):
            b.setEnabled(False)
        self.to_cut.clicked.connect(lambda: self.use_in_cut.emit(self.last_file))
        self.to_fx.clicked.connect(lambda: self.use_in_effect.emit(self.last_wav or self.last_file))
        self.to_merge.clicked.connect(lambda: self.use_in_merge.emit(self.last_file))
        self.open_btn.clicked.connect(lambda: common.show_in_folder(self.last_file))
        l7.addWidget(self.result_player)
        l7.addWidget(self.task)
        l7.addWidget(self.stt_status)
        l7.addWidget(self.transcript)
        l7.addLayout(row(self.copy_btn, self.to_fx, self.to_cut, self.to_merge, None, self.open_btn))
        lay.addWidget(c7)

        self.clock = QTimer(self)
        self.clock.setInterval(200)
        self.clock.timeout.connect(self._clock)
        self.count_timer = QTimer(self)
        self.count_timer.setInterval(1000)
        self.count_timer.timeout.connect(self._count_tick)

        common.bind_setting(self.mode_auto, "rec_auto", False)
        self.mode_manual.setChecked(not self.mode_auto.isChecked())
        common.bind_setting(self.threshold, "rec_threshold", -42)
        common.bind_setting(self.stop_after, "rec_stop_after", 2.5)
        common.bind_setting(self.compact, "rec_compact", False)
        common.bind_setting(self.countdown, "rec_countdown", True)
        common.bind_setting(self.fx_on, "rec_fx_on", False)
        common.bind_setting(self.fx, "rec_fx", "penyiar")
        common.bind_setting(self.stt_on, "rec_stt_on", False)
        self.fx.setEnabled(self.fx_on.isChecked())
        self.fx_on.toggled.connect(self.fx.setEnabled)
        self.stt_lang.setEnabled(self.stt_on.isChecked())
        self.stt_on.toggled.connect(self.stt_lang.setEnabled)
        self.mode_auto.toggled.connect(self._mode_changed)
        self._mode_changed()
        self._load_devices()

    # ------------------------------------------------------------ perangkat
    def _load_devices(self) -> None:
        if self.recorder.active:
            return
        current = self.mic.currentText()
        self.mic.clear()
        default = QMediaDevices.defaultAudioInput()
        self._devs = list(QMediaDevices.audioInputs())
        best = -1
        for i, dev in enumerate(self._devs):
            is_default = dev.id() == default.id()
            label = dev.description() + ("  (bawaan)" if is_default else "")
            self.mic.addItem(label, i)
            if not audio.is_virtual_cable(dev.description()) and (best < 0 or is_default):
                best = i
        if self.mic.count() == 0:
            self.mic.addItem("Tidak ada mikrofon terdeteksi", None)
        idx = self.mic.findText(current)
        self.mic.setCurrentIndex(idx if idx >= 0 else max(0, best))

    def _device(self):
        idx = self.mic.currentData()
        if idx is None or not (0 <= idx < len(self._devs)):
            return None
        return self._devs[idx]

    def _video_changed(self, path: str) -> None:
        if path and self.vid_pick.info and self.vid_pick.info.is_video:
            self.vid_player.load(path)
        else:
            self.vid_player.unload()

    def _mode_changed(self, *_a) -> None:
        auto = self.mode_auto.isChecked()
        self.auto_box.setVisible(auto)
        self.countdown.setVisible(not auto)
        if not self.recorder.active:
            self._set_btn(False)

    # ------------------------------------------------------------ rekam
    def _toggle(self) -> None:
        if self.recorder.active:
            self._stop()
            return
        if self.count_timer.isActive():
            self.count_timer.stop()
            self._set_btn(False)
            self.timer_lbl.setText("0:00")
            return
        if self._device() is None:
            common.error_box(self, "Mikrofon tidak terdeteksi. Colokkan mic/earphone lalu klik "
                                   "Muat ulang.")
            return
        dev = self._device()
        if audio.is_virtual_cable(dev.description()):
            common.error_box(self, "Yang terpilih adalah mic virtual (VB-CABLE), bukan mic "
                                   "sungguhan. Pilih mic laptop atau earphone.")
            return
        self.result_player.unload()
        if self.mode_auto.isChecked():
            self._start(auto=True)
        elif self.countdown.isChecked():
            self._count = 3
            self.timer_lbl.setText("3")
            self.rec_btn.setText("Batal")
            self.count_timer.start()
        else:
            self._start(auto=False)

    def _count_tick(self) -> None:
        self._count -= 1
        if self._count <= 0:
            self.count_timer.stop()
            self._start(auto=False)
        else:
            self.timer_lbl.setText(str(self._count))

    def _start(self, auto: bool) -> None:
        gate_cfg = None
        if auto:
            gate_cfg = dict(threshold_db=self.threshold.value(), stop_after=self.stop_after.value())
        try:
            self.recorder.start(self._device(), gate_cfg)
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
        if auto:
            self.timer_lbl.setText("siaga")
            self.state_lbl.setText("Siaga — mulai bicara kapan saja. Rekaman berhenti sendiri "
                                   f"setelah kamu diam {self.stop_after.value():g} detik.")
        else:
            self.state_lbl.setText("Merekam…")
            self._begin_extras()

    def _voice_started(self) -> None:
        self.state_lbl.setText("Merekam… (berhenti sendiri saat kamu diam)")
        self._begin_extras()

    def _begin_extras(self) -> None:
        if self.vid_pick.path() and self.vid_player.play_btn.isEnabled():
            self.vid_player.set_muted(True)
            self.vid_player.play_from_start()
        if self.prompter.auto.isChecked():
            self.prompter.start()

    def _stop(self) -> None:
        if not self.recorder.active:
            return
        self.clock.stop()
        self.prompter.stop()
        self.vid_player.stop()
        was_waiting = not self.recorder.capturing
        raw, sr, ch, fmt, has_audio = self.recorder.stop()
        self._set_btn(False)
        self.meter.setValue(0)
        self.db_lbl.setText("")
        self.state_lbl.setText("")
        if not has_audio or not raw or not os.path.exists(raw) or os.path.getsize(raw) == 0:
            self.timer_lbl.setText("0:00")
            try:
                if raw and os.path.exists(raw):
                    os.remove(raw)
            except OSError:
                pass
            if was_waiting:
                self.state_lbl.setText("Siaga dibatalkan — belum ada suara yang terdeteksi.")
            else:
                common.error_box(self, "Tidak ada suara yang terekam. Cek mikrofon dan izin "
                                       "mikrofon Windows (Settings → Privacy → Microphone).")
            return
        name = f"rekaman-{common.stamp()}"
        folder = Path(common.work_dir("Rekaman"))
        wav = str(folder / f"{name}.wav")
        first = jobs.raw_to_wav(raw, wav, sr, ch, fmt, compact_silence=self.compact.isChecked())
        first.cleanup.append(raw)
        job_list = [first]
        if self.fx_on.isChecked():
            key = self.fx.currentData()
            info = tools.MediaInfo(path=wav, duration=first.duration, has_audio=True,
                                   audio_codec="pcm_s16le")
            job_list.append(jobs.voice_effect(wav, str(folder / f"{name} - {key}.wav"),
                                              effects.preset_settings(key), info=info))
        self._pending_stt = self.stt_on.isChecked()
        self.transcript.hide()
        self.copy_btn.hide()
        self.stt_status.setText("")
        self.task.start(job_list, "Menyimpan rekaman…")

    def _saved(self, outputs: list) -> None:
        if not outputs:
            return
        self.last_wav = outputs[0]
        self.last_file = outputs[-1]
        label = Path(self.last_file).name
        self.result_player.load(self.last_file, label=label)
        for b in (self.to_fx, self.to_cut, self.to_merge, self.open_btn):
            b.setEnabled(True)
        if self._pending_stt:
            self._start_stt(self.last_wav)

    # ------------------------------------------------------------ suara → teks
    def _start_stt(self, path: str) -> None:
        if self.stt_thread is not None:
            return
        key = current_model()
        if not stt.model_path(key):
            self.stt_status.setText("Mesin suara → teks belum terpasang. Buka menu Suara → Teks.")
            return
        self.stt_status.setText("Mengetik jadi teks…")
        t = SttThread(path, key, self.stt_lang.currentData(), self)
        t.progress.connect(lambda f: self.stt_status.setText(f"Mengetik jadi teks… {int(f * 100)}%"))
        t.done.connect(lambda tr, p=path: self._stt_done(p, tr))
        t.failed.connect(lambda m: self.stt_status.setText("Gagal mengetik: " + m.splitlines()[0]))
        t.finished.connect(self._stt_finished)
        self.stt_thread = t
        t.start()

    def _stt_done(self, path: str, tr) -> None:
        text = tr.paragraphs() or "(tidak ada ucapan yang terdengar)"
        self.transcript.setPlainText(text)
        self.transcript.show()
        self.copy_btn.show()
        try:
            txt, _srt = stt.save_outputs(path, tr)
            self.stt_status.setText(f"Teks tersimpan: {Path(txt).name} (+ subtitle .srt)")
        except OSError as exc:
            self.stt_status.setText(f"Teks jadi, tapi gagal disimpan: {exc}")

    def _stt_finished(self) -> None:
        t, self.stt_thread = self.stt_thread, None
        if t:
            t.deleteLater()

    def _copy_text(self) -> None:
        QApplication.clipboard().setText(self.transcript.toPlainText())
        self.stt_status.setText("Teks disalin. Tinggal tempel (Ctrl+V).")

    # ------------------------------------------------------------ tampilan
    def _set_btn(self, recording: bool) -> None:
        if recording:
            text = "■  Stop" if not self.mode_auto.isChecked() else "■  Stop / batal siaga"
        else:
            text = "◉  Siaga rekam otomatis" if self.mode_auto.isChecked() else "●  Mulai rekam"
        self.rec_btn.setText(text)
        self.rec_btn.setProperty("recording", "true" if recording else "false")
        self._repolish(self.rec_btn)
        self.mic.setEnabled(not recording)
        self.mode_auto.setEnabled(not recording)
        self.mode_manual.setEnabled(not recording)

    @staticmethod
    def _repolish(w: QWidget) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    def _clock(self) -> None:
        if self.recorder.capturing:
            secs = self.recorder.seconds()
            m, s = divmod(int(secs), 60)
            self.timer_lbl.setText(f"{m}:{s:02d}")
        if time.monotonic() - self._quiet_since > 5 and self.mic_hint.objectName() != "Err":
            self.mic_hint.setText("Belum ada suara yang masuk. Cek mic yang dipilih, dan izin "
                                  "mikrofon Windows: Settings → Privacy → Microphone → "
                                  "Allow desktop apps.")
            self.mic_hint.setObjectName("Err")
            self._repolish(self.mic_hint)

    def _level(self, peak: float) -> None:
        self.meter.setValue(int(peak * 100))
        self.db_lbl.setText(f"{vad.peak_to_db(peak):.0f} dB")
        if peak > 0.01:
            self._quiet_since = time.monotonic()
            if self.mic_hint.objectName() == "Err":
                self.mic_hint.setText("Suara masuk. Lanjut!")
                self.mic_hint.setObjectName("Ok")
                self._repolish(self.mic_hint)

    def shutdown(self) -> None:
        if self.recorder.active:
            self._stop()
        if self.stt_thread:
            self.stt_thread.cancel()
            self.stt_thread.wait(5000)
