"""Komponen tampilan yang dipakai bersama oleh semua menu."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from ..core import jobs, tools

VIDEO_EXT = "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.3gp *.wmv *.flv *.mts *.ts"
AUDIO_EXT = "*.mp3 *.wav *.m4a *.aac *.ogg *.opus *.flac *.wma *.amr"
FILTER_VIDEO = f"Video ({VIDEO_EXT});;Semua file (*.*)"
FILTER_AUDIO = f"Audio ({AUDIO_EXT});;Semua file (*.*)"
FILTER_MEDIA = f"Audio & video ({AUDIO_EXT} {VIDEO_EXT});;Semua file (*.*)"


# ---------------------------------------------------------------- folder & pengaturan

def settings() -> QSettings:
    return QSettings("SuaraKu", "SuaraKu")


def last_dir() -> str:
    d = settings().value("last_dir", "")
    if d and os.path.isdir(str(d)):
        return str(d)
    return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)


def remember_dir(path: str) -> None:
    if path:
        settings().setValue("last_dir", str(Path(path).parent))


def work_dir(sub: str) -> str:
    """Documents\\SuaraKu\\<sub> untuk hasil rekaman & suara AI."""
    docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    d = Path(docs) / "SuaraKu" / sub
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


_TMP = Path(tempfile.gettempdir()) / "SuaraKu"


def temp_file(prefix: str, ext: str) -> str:
    _TMP.mkdir(parents=True, exist_ok=True)
    return str(_TMP / f"{prefix}-{int(time.time() * 1000)}{ext}")


def clean_temp() -> None:
    import shutil
    if _TMP.exists():
        for f in _TMP.iterdir():
            try:
                if f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink()
            except OSError:
                pass


# Pindah menu dari mana saja: MainWindow mengisi ini dengan fungsi navigasi.
_NAVIGATE: Callable[[str], None] | None = None


def set_navigator(fn: Callable[[str], None]) -> None:
    global _NAVIGATE
    _NAVIGATE = fn


def navigate(page: str) -> None:
    if _NAVIGATE:
        _NAVIGATE(page)


def bind_setting(widget, key: str, default) -> None:
    """Ingat pilihan user (centang, pilihan daftar, slider) antar sesi."""
    st = settings()
    val = st.value(key, default)
    if isinstance(widget, QAbstractButton):
        widget.setChecked(str(val).lower() in ("true", "1"))
        widget.toggled.connect(lambda v: settings().setValue(key, bool(v)))
    elif isinstance(widget, QComboBox):
        idx = widget.findData(val)
        if idx >= 0:
            widget.setCurrentIndex(idx)
        widget.currentIndexChanged.connect(lambda _i: settings().setValue(key, widget.currentData()))
    elif isinstance(widget, LabeledSlider):
        try:
            widget.setValue(float(val))
        except (TypeError, ValueError):
            widget.setValue(float(default))
        widget.valueChanged.connect(lambda v: settings().setValue(key, v))


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def show_in_folder(path: str) -> None:
    if not path:
        return
    p = Path(path)
    if sys.platform == "win32" and p.exists():
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent if p.is_file() else p)))


def error_box(parent: QWidget, text: str, title: str = "Ada masalah") -> None:
    QMessageBox.warning(parent, title, text)


# ---------------------------------------------------------------- tata letak

def page_header(title: str, subtitle: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 6)
    lay.setSpacing(2)
    h = QLabel(title)
    h.setObjectName("H1")
    s = QLabel(subtitle)
    s.setObjectName("Sub")
    s.setWordWrap(True)
    lay.addWidget(h)
    lay.addWidget(s)
    return w


def card(title: str | None = None, hint: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("Card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 16)
    lay.setSpacing(10)
    if title:
        t = QLabel(title)
        t.setObjectName("CardTitle")
        lay.addWidget(t)
    if hint:
        h = QLabel(hint)
        h.setObjectName("Hint")
        h.setWordWrap(True)
        lay.addWidget(h)
    return f, lay


def hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Hint")
    lbl.setWordWrap(True)
    return lbl


def scroll_page(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setWidget(inner)
    return sa


def row(*widgets, stretch_last: bool = False, spacing: int = 8) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    if stretch_last:
        lay.addStretch(1)
    return lay


class LabeledSlider(QWidget):
    """Slider dengan judul dan angka. Nilai disimpan sebagai float (step)."""

    valueChanged = Signal(float)

    def __init__(self, label: str, lo: float, hi: float, value: float = 0.0,
                 step: float = 1.0, unit: str = "", tip: str = "", parent=None):
        super().__init__(parent)
        self.step = step
        self.unit = unit
        self.signed = lo < 0  # tampilkan tanda +/− kalau rentangnya bisa negatif
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel(label)
        self.name.setMinimumWidth(130)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(int(round(lo / step)), int(round(hi / step)))
        self.value_lbl = QLabel()
        self.value_lbl.setMinimumWidth(64)
        self.value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.name)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value_lbl)
        if tip:
            self.setToolTip(tip)
        self.slider.valueChanged.connect(self._changed)
        self.setValue(value)
        self._changed(self.slider.value())

    def _fmt(self, v: float) -> str:
        if self.step < 1:
            txt = f"{v:+.1f}" if self.signed else f"{v:.1f}"
        else:
            txt = f"{int(round(v)):+d}" if self.signed else f"{int(round(v))}"
        return f"{txt} {self.unit}".strip()

    def _changed(self, raw: int) -> None:
        v = raw * self.step
        self.value_lbl.setText(self._fmt(v))
        self.valueChanged.emit(v)

    def value(self) -> float:
        return self.slider.value() * self.step

    def setValue(self, v: float) -> None:
        self.slider.setValue(int(round(v / self.step)))


# ---------------------------------------------------------------- pemilih file

class FilePicker(QWidget):
    """Kolom file + tombol Pilih, bisa juga drag & drop file ke sini."""

    changed = Signal(str)

    def __init__(self, placeholder: str, file_filter: str = FILTER_MEDIA,
                 clearable: bool = True, parent=None):
        super().__init__(parent)
        self.file_filter = file_filter
        self.info: tools.MediaInfo | None = None
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        top = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.editingFinished.connect(lambda: self.set_path(self.edit.text().strip()))
        self.btn = QPushButton("Pilih…")
        self.btn.clicked.connect(self.browse)
        top.addWidget(self.edit, 1)
        top.addWidget(self.btn)
        if clearable:
            self.clear_btn = QPushButton("✕")
            self.clear_btn.setToolTip("Kosongkan")
            self.clear_btn.setFixedWidth(36)
            self.clear_btn.clicked.connect(lambda: self.set_path(""))
            top.addWidget(self.clear_btn)
        self.info_lbl = QLabel("")
        self.info_lbl.setObjectName("Info")
        lay.addLayout(top)
        lay.addWidget(self.info_lbl)

    def path(self) -> str:
        p = self.edit.text().strip()
        return p if p and os.path.isfile(p) else ""

    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Pilih file", last_dir(), self.file_filter)
        if path:
            self.set_path(path)

    def set_path(self, path: str) -> None:
        path = (path or "").strip().strip('"')
        self.edit.setText(path)
        self.info = None
        self.info_lbl.setObjectName("Info")
        if not path:
            self.info_lbl.setText("")
        elif not os.path.isfile(path):
            self.info_lbl.setText("File tidak ditemukan.")
        else:
            remember_dir(path)
            try:
                self.info = tools.probe(path)
                self.info_lbl.setText(self.info.describe())
            except Exception as exc:
                self.info_lbl.setText(str(exc))
        self.info_lbl.style().unpolish(self.info_lbl)
        self.info_lbl.style().polish(self.info_lbl)
        self.changed.emit(self.path())

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.set_path(urls[0])


# ---------------------------------------------------------------- thread kerja

class JobThread(QThread):
    progress = Signal(int, float)
    done = Signal(list)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, job_list: list[jobs.Job], parent: QObject | None = None):
        super().__init__(parent)
        self.job_list = job_list
        self._runner: jobs.Runner | None = None
        self._cancel = False

    def run(self) -> None:  # dijalankan di thread terpisah
        outputs: list[str] = []
        try:
            for i, job in enumerate(self.job_list):
                if self._cancel:
                    raise jobs.JobCancelled()
                self._runner = jobs.Runner()
                outputs.append(self._runner.run(job, lambda f, i=i: self.progress.emit(i, f)))
            self.done.emit(outputs)
        except jobs.JobCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))

    def cancel(self) -> None:
        self._cancel = True
        if self._runner:
            self._runner.cancel()


class FuncThread(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable, *args, parent: QObject | None = None, **kwargs):
        super().__init__(parent)
        self.fn, self.args, self.kwargs = fn, args, kwargs

    def run(self) -> None:
        try:
            self.done.emit(self.fn(*self.args, **self.kwargs))
        except Exception as exc:
            self.failed.emit(str(exc))


class TaskPanel(QWidget):
    """Progress bar + tombol Batal + tombol Buka folder untuk hasil terakhir."""

    finished = Signal(list)      # daftar file hasil
    busyChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread: JobThread | None = None
        self.last_output = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.status = QLabel("")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        self.cancel_btn = QPushButton("Batal")
        self.cancel_btn.clicked.connect(self.cancel)
        self.open_btn = QPushButton("Buka folder hasil")
        self.open_btn.clicked.connect(lambda: show_in_folder(self.last_output))
        self.cancel_btn.hide()
        self.open_btn.hide()
        lay.addWidget(self.bar)
        lay.addLayout(row(self.status, None, self.cancel_btn, self.open_btn))
        self.bar.hide()

    @property
    def busy(self) -> bool:
        return self.thread is not None

    def start(self, job_list: list[jobs.Job], label: str = "Memproses…") -> bool:
        if self.busy or not job_list:
            return False
        self.n = len(job_list)
        self.label = label
        self.bar.setValue(0)
        self.bar.show()
        self.open_btn.hide()
        self.cancel_btn.show()
        self.status.setObjectName("Hint")
        self._set_status(label)
        self.thread = JobThread(job_list, self)
        self.thread.progress.connect(self._progress)
        self.thread.done.connect(self._done)
        self.thread.failed.connect(self._failed)
        self.thread.cancelled.connect(self._cancelled)
        self.thread.finished.connect(self._cleanup)
        self.busyChanged.emit(True)
        self.thread.start()
        return True

    def _set_status(self, text: str, obj: str = "Hint") -> None:
        self.status.setObjectName(obj)
        self.status.setText(text)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _progress(self, i: int, frac: float) -> None:
        total = (i + frac) / max(1, self.n)
        self.bar.setValue(int(total * 1000))
        extra = f" ({i + 1}/{self.n})" if self.n > 1 else ""
        self._set_status(f"{self.label}{extra} {int(total * 100)}%")

    def _done(self, outputs: list) -> None:
        self.bar.setValue(1000)
        self.last_output = outputs[-1] if outputs else ""
        if outputs:
            names = ", ".join(Path(o).name for o in outputs[:3])
            more = f" dan {len(outputs) - 3} lainnya" if len(outputs) > 3 else ""
            self._set_status(f"Selesai: {names}{more}", "Ok")
            self.open_btn.show()
        self.finished.emit(outputs)

    def _failed(self, msg: str) -> None:
        self._set_status("Gagal. " + msg.splitlines()[0], "Err")
        error_box(self, msg)

    def _cancelled(self) -> None:
        self._set_status("Dibatalkan.")
        self.bar.setValue(0)

    def _cleanup(self) -> None:
        self.cancel_btn.hide()
        t, self.thread = self.thread, None
        if t:
            t.deleteLater()
        self.busyChanged.emit(False)

    def cancel(self) -> None:
        if self.thread:
            self.thread.cancel()


# ---------------------------------------------------------------- pemutar audio/video

def _ms(ms: int) -> str:
    return tools.fmt_time(ms / 1000.0)


class Player(QWidget):
    def __init__(self, video: bool = False, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(1.0)
        self.player.setAudioOutput(self.audio)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.video: QVideoWidget | None = None
        if video:
            self.video = QVideoWidget()
            self.video.setMinimumHeight(200)
            self.video.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.video.setStyleSheet("background: black; border-radius: 8px;")
            self.player.setVideoOutput(self.video)
            lay.addWidget(self.video, 1)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(44)
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setEnabled(False)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setObjectName("Info")
        self.name_lbl = QLabel("")
        self.name_lbl.setObjectName("Info")
        lay.addLayout(row(self.play_btn, self.slider, self.time_lbl))
        lay.addWidget(self.name_lbl)
        self.player.positionChanged.connect(self._pos)
        self.player.durationChanged.connect(self._dur)
        self.player.playbackStateChanged.connect(self._state)
        self.player.errorOccurred.connect(lambda _e, s: self.name_lbl.setText(f"Tidak bisa diputar: {s}"))

    def load(self, path: str, autoplay: bool = False, label: str | None = None) -> None:
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.play_btn.setEnabled(True)
        self.slider.setEnabled(True)
        self.name_lbl.setText(label if label is not None else Path(path).name)
        if autoplay:
            self.player.play()

    def unload(self) -> None:
        """Lepas file (penting di Windows supaya file tidak terkunci)."""
        self.player.stop()
        self.player.setSource(QUrl())
        self.play_btn.setEnabled(False)
        self.slider.setEnabled(False)
        self.slider.setValue(0)
        self.time_lbl.setText("0:00 / 0:00")
        self.name_lbl.setText("")

    def toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def play_from_start(self) -> None:
        self.player.setPosition(0)
        self.player.play()

    def stop(self) -> None:
        self.player.stop()

    def set_muted(self, muted: bool) -> None:
        self.audio.setMuted(muted)

    def _pos(self, ms: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.time_lbl.setText(f"{_ms(ms)} / {_ms(self.player.duration())}")

    def _dur(self, ms: int) -> None:
        self.slider.setRange(0, max(0, ms))
        self.time_lbl.setText(f"{_ms(self.player.position())} / {_ms(ms)}")

    def _state(self, st) -> None:
        playing = st == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("❚❚" if playing else "▶")
