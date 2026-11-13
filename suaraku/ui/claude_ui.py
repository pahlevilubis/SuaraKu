"""Bantuan tampilan untuk fitur Claude: ambil API key, jalankan permintaan di latar."""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QWidget

from ..core import claude, secret
from ..core.effects import VoiceSettings
from . import common
from .common import FuncThread


def get_key() -> str:
    return secret.unprotect(str(common.settings().value("claude_key", "") or ""))


def set_key(key: str) -> None:
    common.settings().setValue("claude_key", secret.protect(key.strip()))


def clear_key() -> None:
    common.settings().remove("claude_key")


def get_model() -> str:
    m = str(common.settings().value("claude_model", claude.DEFAULT_MODEL) or claude.DEFAULT_MODEL)
    return m if m in dict(claude.MODELS) else claude.DEFAULT_MODEL


def set_model(model: str) -> None:
    common.settings().setValue("claude_model", model)


def require_key(parent: QWidget) -> str | None:
    key = get_key()
    if key:
        return key
    box = QMessageBox(parent)
    box.setWindowTitle("Sambungkan Claude dulu")
    box.setText("Fitur ini memakai Claude lewat API key. Isi API key di menu Asisten Claude dulu, "
                "ya. Cukup sekali.")
    go = box.addButton("Buka Asisten Claude", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Nanti", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is go:
        common.navigate("claude")
    return None


def run(parent: QWidget, fn: Callable, *args, on_done: Callable, button: QPushButton | None = None,
        status: QLabel | None = None, busy_text: str = "Claude sedang berpikir…") -> bool:
    """Jalankan fn(key, model, *args) di thread. Kembalikan False kalau belum ada key."""
    key = require_key(parent)
    if not key:
        return False
    if getattr(parent, "_claude_busy", False):
        return False
    parent._claude_busy = True
    old_text = button.text() if button else ""
    if button:
        button.setEnabled(False)
        button.setText("⏳ " + old_text.lstrip("✨ "))
    if status:
        status.setText(busy_text)

    t = FuncThread(fn, key, get_model(), *args, parent=parent)

    def done(result) -> None:
        if status:
            status.setText("")
        on_done(result)

    def failed(msg: str) -> None:
        if status:
            status.setText("Gagal.")
        common.error_box(parent, msg, "Claude")

    def finished() -> None:
        parent._claude_busy = False
        if button:
            button.setEnabled(True)
            button.setText(old_text)
        t.deleteLater()

    t.done.connect(done)
    t.failed.connect(failed)
    t.finished.connect(finished)
    t.start()
    return True


class VoiceAsk(QWidget):
    """Kolom 'ceritakan suara yang kamu mau' + tombol Minta Claude."""

    def __init__(self, apply: Callable[[VoiceSettings], None], parent=None):
        super().__init__(parent)
        self._apply = apply
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Ceritakan suara yang kamu mau, mis. “berat & hangat kayak "
                                     "penyiar radio malam, sedikit gema”")
        self.btn = QPushButton("✨ Minta Claude")
        self.btn.clicked.connect(self._go)
        self.edit.returnPressed.connect(self._go)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.btn)
        self.note = QLabel("")
        self.note.setObjectName("Hint")
        self.note.setWordWrap(True)

    def _go(self) -> None:
        text = self.edit.text().strip()
        if not text:
            self.edit.setFocus()
            return
        run(self, claude.voice_settings, text, on_done=self._done, button=self.btn,
            status=self.note, busy_text="Claude sedang meracik suaranya…")

    def _done(self, result) -> None:
        settings, why = result
        self._apply(settings)
        self.note.setText(f"✨ {why or 'Pengaturan dari Claude sudah dipasang.'} "
                          "Bisa diatur lagi di Atur manual.")
