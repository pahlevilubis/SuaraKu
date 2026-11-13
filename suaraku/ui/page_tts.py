"""Menu Suara AI: ketik naskah, dibacakan suara AI (butuh internet)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..core import effects, jobs, tts
from . import common
from .common import FuncThread, LabeledSlider, Player, card, hint, page_header, row


def _make_voice(text: str, voice: str, rate: int, pitch: int, fx_key: str | None) -> str:
    """Jalan di thread: buat suara AI lalu (opsional) beri efek."""
    folder = Path(common.work_dir("Suara AI"))
    base = f"suara-ai-{common.stamp()}"
    raw = str(folder / f"{base}.mp3")
    tts.synthesize(text, voice, raw, rate_pct=rate, pitch_hz=pitch)
    if not fx_key:
        return raw
    final = str(folder / f"{base} - {fx_key}.mp3")
    job = jobs.voice_effect(raw, final, effects.preset_settings(fx_key))
    jobs.Runner().run(job)
    return final


class TtsPage(QWidget):
    use_in_merge = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread: FuncThread | None = None
        self.last_file = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(page_header(
            "Suara AI",
            "Ketik atau tempel naskah, lalu dibacakan suara AI yang natural. "
            "Cocok kalau lagi malas rekam sendiri. Butuh internet."))

        c1, l1 = card("1. Naskah")
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            "Contoh: Halo semuanya! Ini aplikasi SuaraKu yang saya bikin sendiri…\n\n"
            "Tips: minta Claude rapikan naskahnya dulu, lalu tempel di sini. "
            "Pakai titik dan koma supaya jedanya pas.")
        self.text.setMinimumHeight(180)
        self.count = QLabel("0 karakter")
        self.count.setObjectName("Info")
        self.text.textChanged.connect(
            lambda: self.count.setText(f"{len(self.text.toPlainText())} karakter"))
        l1.addWidget(self.text)
        l1.addWidget(self.count)
        lay.addWidget(c1)

        c2, l2 = card("2. Suara")
        self.voice = QComboBox()
        self._set_voices(tts.CURATED)
        self.load_btn = QPushButton("Muat semua suara")
        self.load_btn.setToolTip("Ambil daftar lengkap suara dari internet")
        self.load_btn.clicked.connect(self._load_voices)
        l2.addLayout(row(self.voice, self.load_btn))
        self.rate = LabeledSlider("Kecepatan", -50, 50, 0, step=5, unit="%")
        self.pitch = LabeledSlider("Nada", -30, 30, 0, step=2, unit="Hz",
                                   tip="Negatif = lebih berat.")
        l2.addWidget(self.rate)
        l2.addWidget(self.pitch)
        self.fx_on = QCheckBox("Tambah efek suara setelah dibuat")
        self.fx = QComboBox()
        for p in effects.PRESETS:
            if p.key != "asli":
                self.fx.addItem(p.name, p.key)
        self.fx.setCurrentIndex(max(0, self.fx.findData("penyiar")))
        self.fx.setEnabled(False)
        self.fx_on.toggled.connect(self.fx.setEnabled)
        l2.addLayout(row(self.fx_on, self.fx, None))
        lay.addWidget(c2)

        c3, l3 = card("3. Buat & dengarkan")
        self.go = QPushButton("Buat suara")
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self._go)
        self.status = QLabel("")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        self.player = Player()
        self.to_merge = QPushButton("Pakai di Gabung Video →")
        self.open_btn = QPushButton("Buka folder")
        for b in (self.to_merge, self.open_btn):
            b.setEnabled(False)
        self.to_merge.clicked.connect(lambda: self.use_in_merge.emit(self.last_file))
        self.open_btn.clicked.connect(lambda: common.show_in_folder(self.last_file))
        l3.addLayout(row(self.go, self.status, None))
        l3.addWidget(self.player)
        l3.addLayout(row(self.to_merge, None, self.open_btn))
        lay.addWidget(c3)
        lay.addWidget(hint("Suara AI ini memakai layanan suara Microsoft Edge lewat jalur tidak "
                           "resmi. Kalau suatu saat gagal terus, biasanya cukup build ulang "
                           "SuaraKu supaya pakai versi terbaru."))
        lay.addStretch(1)

    def set_text(self, text: str) -> None:
        self.text.setPlainText(text)

    def _set_voices(self, voices: list[tts.Voice]) -> None:
        current = self.voice.currentData()
        self.voice.clear()
        for v in voices:
            self.voice.addItem(v.label, v.short_name)
        idx = self.voice.findData(current) if current else 0
        self.voice.setCurrentIndex(max(0, idx))

    def _busy(self, on: bool, msg: str = "") -> None:
        self.go.setEnabled(not on)
        self.load_btn.setEnabled(not on)
        self.status.setText(msg)

    def _run(self, fn, *args, on_done) -> None:
        if self.thread is not None:
            return
        t = FuncThread(fn, *args, parent=self)
        t.done.connect(on_done)
        t.failed.connect(self._failed)
        t.finished.connect(self._finished)
        self.thread = t
        t.start()

    def _finished(self) -> None:
        t, self.thread = self.thread, None
        if t:
            t.deleteLater()

    def _failed(self, msg: str) -> None:
        self._busy(False, "Gagal.")
        common.error_box(self, msg)

    def _load_voices(self) -> None:
        self._busy(True, "Memuat daftar suara…")
        self._run(tts.list_voices, on_done=self._voices_loaded)

    def _voices_loaded(self, voices) -> None:
        if voices:
            self._set_voices(voices)
        self._busy(False, f"{len(voices)} suara tersedia.")

    def _go(self) -> None:
        text = self.text.toPlainText().strip()
        if not text:
            common.error_box(self, "Naskahnya masih kosong.")
            return
        fx = self.fx.currentData() if self.fx_on.isChecked() else None
        self.player.unload()
        self._busy(True, "Membuat suara… (butuh internet)")
        self._run(_make_voice, text, self.voice.currentData(), int(self.rate.value()),
                  int(self.pitch.value()), fx, on_done=self._made)

    def _made(self, path: str) -> None:
        self.last_file = path
        self._busy(False, f"Selesai: {Path(path).name}")
        self.player.load(path, autoplay=True)
        for b in (self.to_merge, self.open_btn):
            b.setEnabled(True)
