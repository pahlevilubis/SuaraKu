"""Menu Suara → Teks: ketik otomatis isi rekaman/video (offline, Whisper)."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
    QWidget,
)

from ..core import stt
from . import common
from .common import FILTER_MEDIA, FilePicker, FuncThread, card, hint, page_header, row


def current_model() -> str:
    key = str(common.settings().value("stt_model", "base"))
    if key not in stt.MODELS or not stt.model_path(key):
        key = "base"
    return key


class SttThread(QThread):
    progress = Signal(float)
    done = Signal(object)        # stt.Transcript
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, path: str, model: str, language: str | None, parent=None):
        super().__init__(parent)
        self.path, self.model, self.language = path, model, language
        self._cancel = threading.Event()

    def run(self) -> None:
        try:
            t = stt.transcribe(self.path, self.model, self.language,
                               on_progress=self.progress.emit, cancel=self._cancel)
            self.done.emit(t)
        except stt.SttCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))

    def cancel(self) -> None:
        self._cancel.set()


def language_combo() -> QComboBox:
    c = QComboBox()
    for label, code in stt.LANGUAGES:
        c.addItem(label, code)
    saved = str(common.settings().value("stt_lang", "id"))
    idx = 0 if saved == "" else c.findData(saved)   # "" = Otomatis
    c.setCurrentIndex(idx if idx >= 0 else 1)
    c.currentIndexChanged.connect(
        lambda _i: common.settings().setValue("stt_lang", c.currentData() or ""))
    return c


class TranscribePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread: SttThread | None = None
        self.dl_thread: FuncThread | None = None
        self.txt_path = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(page_header(
            "Suara → Teks",
            "Ubah rekaman, voice note, atau video jadi tulisan — offline, tanpa internet. "
            "Sekaligus dibuatkan file subtitle (.srt) untuk video LinkedIn."))

        c1, l1 = card("1. File")
        self.pick = FilePicker("Pilih rekaman / audio / video…", FILTER_MEDIA)
        l1.addWidget(self.pick)
        lay.addWidget(c1)

        c2, l2 = card("2. Pengaturan")
        self.lang = language_combo()
        self.model = QComboBox()
        for key, (label, _size) in stt.MODELS.items():
            self.model.addItem(label, key)
        self.model.setCurrentIndex(max(0, self.model.findData(current_model())))
        self.model.currentIndexChanged.connect(self._model_changed)
        self.dl_btn = QPushButton("Unduh model ini")
        self.dl_btn.clicked.connect(self._download)
        self.model_lbl = QLabel("")
        self.model_lbl.setObjectName("Info")
        l2.addLayout(row(QLabel("Bahasa"), self.lang, QLabel("Mesin"), self.model, self.dl_btn, None))
        l2.addWidget(self.model_lbl)
        lay.addWidget(c2)

        c3, l3 = card("3. Ketik jadi teks")
        self.go = QPushButton("Ketik jadi teks")
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self._go)
        self.cancel_btn = QPushButton("Batal")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self._cancel)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.hide()
        self.status = QLabel("")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        l3.addLayout(row(self.go, self.cancel_btn, self.status, None))
        l3.addWidget(self.bar)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Hasil teks muncul di sini. Bisa langsung diedit.")
        self.text.setMinimumHeight(220)
        l3.addWidget(self.text)
        self.copy_btn = QPushButton("Salin teks")
        self.copy_btn.clicked.connect(self._copy)
        self.save_btn = QPushButton("Simpan perubahan")
        self.save_btn.clicked.connect(self._save_edit)
        self.open_btn = QPushButton("Buka folder")
        self.open_btn.clicked.connect(lambda: common.show_in_folder(self.txt_path))
        for b in (self.copy_btn, self.save_btn, self.open_btn):
            b.setEnabled(False)
        l3.addLayout(row(self.copy_btn, self.save_btn, None, self.open_btn))
        lay.addWidget(c3)
        lay.addWidget(hint("Hasil disimpan otomatis di sebelah file aslinya: <nama>.txt dan "
                           "<nama>.srt. Mesin Cepat cukup untuk catatan; pilih Akurat kalau "
                           "banyak kata yang meleset."))
        lay.addStretch(1)
        self._model_changed()

    # ------------------------------------------------------------
    def set_file(self, path: str) -> None:
        self.pick.set_path(path)

    def _model_key(self) -> str:
        key = self.model.currentData()
        return key if key in stt.MODELS else "base"

    def _model_changed(self, *_a) -> None:
        key = self._model_key()
        have = bool(stt.model_path(key))
        self.dl_btn.setVisible(not have)
        self.model_lbl.setText("Siap dipakai." if have else
                               f"Belum terpasang — perlu diunduh sekali ({stt.MODELS[key][1]}, butuh internet).")
        if have:
            common.settings().setValue("stt_model", key)

    def _download(self) -> None:
        if self.dl_thread is not None:
            return
        key = self._model_key()
        self.dl_btn.setEnabled(False)
        self.model_lbl.setText("Mengunduh model… bisa beberapa menit tergantung internet.")
        t = FuncThread(stt.download_model, key, parent=self)
        t.done.connect(lambda _p: self._model_changed())
        t.failed.connect(lambda m: (self.model_lbl.setText("Gagal mengunduh."),
                                    common.error_box(self, m)))
        t.finished.connect(self._dl_finished)
        self.dl_thread = t
        t.start()

    def _dl_finished(self) -> None:
        t, self.dl_thread = self.dl_thread, None
        self.dl_btn.setEnabled(True)
        if t:
            t.deleteLater()

    def _go(self) -> None:
        if self.thread is not None:
            return
        path = self.pick.path()
        if not path or not self.pick.info or not self.pick.info.has_audio:
            common.error_box(self, "Pilih file yang ada suaranya dulu.")
            return
        key = self._model_key()
        if not stt.model_path(key):
            common.error_box(self, "Model ini belum diunduh. Klik “Unduh model ini” dulu, "
                                   "atau pilih mesin Cepat.")
            return
        self.text.clear()
        self.bar.setValue(0)
        self.bar.show()
        self.go.setEnabled(False)
        self.cancel_btn.show()
        self.status.setText("Memuat mesin… lalu mendengarkan dan mengetik.")
        t = SttThread(path, key, self.lang.currentData(), self)
        t.progress.connect(lambda f: (self.bar.setValue(int(f * 1000)),
                                      self.status.setText(f"Mengetik… {int(f * 100)}%")))
        t.done.connect(self._done)
        t.failed.connect(lambda m: (self.status.setText("Gagal."), common.error_box(self, m)))
        t.cancelled.connect(lambda: self.status.setText("Dibatalkan."))
        t.finished.connect(self._finished)
        self.thread = t
        t.start()

    def _cancel(self) -> None:
        if self.thread:
            self.thread.cancel()

    def _finished(self) -> None:
        t, self.thread = self.thread, None
        self.go.setEnabled(True)
        self.cancel_btn.hide()
        self.bar.hide()
        if t:
            t.deleteLater()

    def _done(self, t: stt.Transcript) -> None:
        self.text.setPlainText(t.paragraphs() or "(tidak ada ucapan yang terdengar)")
        try:
            self.txt_path, srt = stt.save_outputs(self.pick.path(), t)
            self.status.setText(f"Selesai · bahasa terdeteksi: {t.language or '?'} · tersimpan "
                                f"{Path(self.txt_path).name} & {Path(srt).name}")
        except OSError as exc:
            self.txt_path = ""
            self.status.setText(f"Selesai, tapi gagal menyimpan file: {exc}")
        for b in (self.copy_btn, self.save_btn, self.open_btn):
            b.setEnabled(True)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())
        self.status.setText("Teks disalin. Tinggal tempel (Ctrl+V).")

    def _save_edit(self) -> None:
        if not self.txt_path:
            return
        try:
            Path(self.txt_path).write_text(self.text.toPlainText() + "\n", encoding="utf-8")
            self.status.setText(f"Perubahan disimpan ke {Path(self.txt_path).name}")
        except OSError as exc:
            common.error_box(self, f"Gagal menyimpan: {exc}")
