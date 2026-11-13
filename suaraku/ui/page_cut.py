"""Menu Potong: potong audio & video dengan gelombang suara, edit pakai teks, rapikan dengan Claude."""

from __future__ import annotations

import bisect
import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QTextCharFormat, QTextCursor
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QLabel, QPushButton, QScrollBar, QSlider, QTextEdit,
    QVBoxLayout, QWidget,
)

from ..core import claude, edl, jobs, stt, tools
from . import claude_ui, common, theme
from .common import (
    FILTER_MEDIA, FilePicker, FuncThread, LabeledSlider, Player, TaskPanel, card, hint, page_header,
    row,
)
from .page_transcribe import SttThread, current_model, language_combo


def fmt_t(sec: float, decimals: bool = True) -> str:
    """75.25 → '1:15.3' (atau '1:15'); 3725 → '1:02:05.0'."""
    sec = round(max(0.0, sec), 1) if decimals else float(int(max(0.0, sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    sec_txt = f"{s:04.1f}" if decimals else f"{int(s):02d}"
    return f"{int(h)}:{int(m):02d}:{sec_txt}" if h else f"{int(m)}:{sec_txt}"


# ---------------------------------------------------------------- gelombang suara

class Waveform(QWidget):
    seek = Signal(float)
    selectionChanged = Signal(float, float)     # (-1, -1) = tidak ada pilihan
    viewChanged = Signal()
    keyAction = Signal(str)

    RULER = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        import numpy as np
        self._np = np
        self.setMinimumHeight(190)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.peaks = np.zeros(0, dtype=np.float32)
        self.per_sec = 100
        self.duration = 0.0
        self.view_start = 0.0
        self.view_len = 0.0
        self.removed: list[edl.Range] = []
        self.splits: list[float] = []
        self.sel: edl.Range | None = None
        self.playhead = 0.0
        self._drag_from: float | None = None
        self._press_x = 0.0
        self._moved = False

    # ---- data & tampilan
    def set_data(self, peaks, per_sec: int, duration: float) -> None:
        self.peaks, self.per_sec, self.duration = peaks, per_sec, max(0.0, duration)
        self.view_start, self.view_len = 0.0, self.duration
        self.sel = None
        self.playhead = 0.0
        self.viewChanged.emit()
        self.update()

    def set_edit(self, removed: list[edl.Range], splits: list[float]) -> None:
        self.removed, self.splits = removed, splits
        self.update()

    def set_selection(self, sel: edl.Range | None) -> None:
        self.sel = sel
        self.update()

    def set_playhead(self, t: float) -> None:
        old_x = self.t_to_x(self.playhead)
        self.playhead = t
        if self.view_len and not (self.view_start <= t <= self.view_start + self.view_len):
            # ikuti playhead saat diputar
            self.scroll_to(t - self.view_len * 0.1)
        new_x = self.t_to_x(t)
        if abs(new_x - old_x) >= 1:
            self.update()

    def zoom_factor(self) -> float:
        return self.duration / self.view_len if self.view_len else 1.0

    def set_zoom(self, factor: float, center: float | None = None) -> None:
        if not self.duration:
            return
        factor = max(1.0, min(factor, self.duration / 0.5))
        center = (self.view_start + self.view_len / 2) if center is None else center
        frac = (center - self.view_start) / self.view_len if self.view_len else 0.5
        self.view_len = self.duration / factor
        self.scroll_to(center - frac * self.view_len)

    def scroll_to(self, start: float) -> None:
        self.view_start = max(0.0, min(start, self.duration - self.view_len))
        self.viewChanged.emit()
        self.update()

    def _wave_rect(self) -> QRectF:
        return QRectF(0, self.RULER, self.width(), self.height() - self.RULER)

    def x_to_t(self, x: float) -> float:
        if not self.width() or not self.view_len:
            return 0.0
        return max(0.0, min(self.duration, self.view_start + x / self.width() * self.view_len))

    def t_to_x(self, t: float) -> float:
        if not self.view_len:
            return 0.0
        return (t - self.view_start) / self.view_len * self.width()

    # ---- gambar
    def paintEvent(self, _e):  # noqa: N802
        np = self._np
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(theme.FIELD))
        wr = self._wave_rect()
        if not self.duration or not len(self.peaks):
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Pilih file di atas — gelombang suaranya muncul di sini")
            p.end()
            return

        # penggaris waktu
        p.setPen(QColor("#3a3f48"))
        p.drawLine(0, self.RULER - 1, w, self.RULER - 1)
        step = self._tick_step()
        t = (int(self.view_start / step) + 1) * step
        f = QFont(self.font())
        f.setPointSizeF(8)
        p.setFont(f)
        while t < self.view_start + self.view_len:
            x = self.t_to_x(t)
            p.setPen(QColor("#4a505a"))
            p.drawLine(int(x), self.RULER - 6, int(x), self.RULER - 1)
            p.setPen(QColor(theme.MUTED))
            p.drawText(int(x) + 3, self.RULER - 7, fmt_t(t, step < 1))
            t += step

        # puncak per piksel
        cols = max(1, int(w))
        edges = np.linspace(self.view_start, self.view_start + self.view_len, cols + 1)
        idx = np.clip((edges * self.per_sec).astype(np.int64), 0, len(self.peaks) - 1)
        starts = idx[:-1]
        stop = min(len(self.peaks), int(idx[-1]) + 1)
        # maksimum per kolom; potong array dulu supaya kolom terakhir tidak ikut sisa file
        window = self.peaks[int(starts[0]):max(stop, int(starts[-1]) + 1)]
        vals = np.maximum.reduceat(window, starts - starts[0])
        vals = np.power(np.clip(vals, 0, 1), 0.6)
        centers = (edges[:-1] + edges[1:]) / 2
        removed_mask = np.zeros(cols, dtype=bool)
        for a, b in self.removed:
            removed_mask |= (centers >= a) & (centers < b)
        mid = wr.top() + wr.height() / 2
        amp = wr.height() / 2 - 6
        keep_pen = QPen(QColor(theme.ACCENT))
        gone_pen = QPen(QColor("#4b4f57"))
        for i in range(cols):
            hh = max(0.5, float(vals[i]) * amp)
            p.setPen(gone_pen if removed_mask[i] else keep_pen)
            p.drawLine(QPointF(i + 0.5, mid - hh), QPointF(i + 0.5, mid + hh))

        # bagian dibuang
        hatch = QBrush(QColor(229, 72, 77, 90), Qt.BrushStyle.BDiagPattern)
        for a, b in self.removed:
            x1, x2 = self.t_to_x(a), self.t_to_x(b)
            if x2 < 0 or x1 > w:
                continue
            r = QRectF(x1, wr.top(), max(1.0, x2 - x1), wr.height())
            p.fillRect(r, QColor(229, 72, 77, 28))
            p.fillRect(r, hatch)

        # pilihan
        if self.sel:
            x1, x2 = self.t_to_x(self.sel[0]), self.t_to_x(self.sel[1])
            p.fillRect(QRectF(x1, wr.top(), x2 - x1, wr.height()), QColor(255, 255, 255, 34))
            p.setPen(QPen(QColor(theme.ACCENT), 1.5))
            p.drawLine(QPointF(x1, wr.top()), QPointF(x1, wr.bottom()))
            p.drawLine(QPointF(x2, wr.top()), QPointF(x2, wr.bottom()))

        # penanda pecah
        pen = QPen(QColor(theme.OK), 1.5, Qt.PenStyle.DashLine)
        for i, s in enumerate(sorted(self.splits), 1):
            x = self.t_to_x(s)
            if 0 <= x <= w:
                p.setPen(pen)
                p.drawLine(QPointF(x, wr.top()), QPointF(x, wr.bottom()))
                p.setPen(QColor(theme.OK))
                p.drawText(int(x) + 4, int(wr.top()) + 14, f"✂{i}")

        # playhead
        x = self.t_to_x(self.playhead)
        if 0 <= x <= w:
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawLine(QPointF(x, 0), QPointF(x, h))
        if self.hasFocus():
            p.setPen(QPen(QColor(theme.ACCENT), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    def _tick_step(self) -> float:
        target = self.view_len / max(1, self.width() / 90)
        for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800):
            if s >= target:
                return s
        return 3600

    # ---- mouse & keyboard
    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.duration:
            self.setFocus()
            self._press_x = e.position().x()
            self._drag_from = self.x_to_t(self._press_x)
            self._moved = False

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._drag_from is None:
            return
        x = e.position().x()
        if abs(x - self._press_x) > 3:
            self._moved = True
        if self._moved:
            t = self.x_to_t(x)
            self.sel = (min(self._drag_from, t), max(self._drag_from, t))
            self.selectionChanged.emit(*self.sel)
            self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._drag_from is None:
            return
        if not self._moved:
            self.sel = None
            self.selectionChanged.emit(-1.0, -1.0)
            self.seek.emit(self.x_to_t(e.position().x()))
        self._drag_from = None
        self.update()

    def wheelEvent(self, e):  # noqa: N802
        if not self.duration:
            return
        dy = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            center = self.x_to_t(e.position().x())
            self.set_zoom(self.zoom_factor() * (1.25 if dy > 0 else 0.8), center)
        else:
            self.scroll_to(self.view_start - (dy / 120.0) * self.view_len * 0.1)
        e.accept()

    def keyPressEvent(self, e):  # noqa: N802
        k = e.key()
        ctrl = e.modifiers() & Qt.KeyboardModifier.ControlModifier
        if k == Qt.Key.Key_Space:
            self.keyAction.emit("play")
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.keyAction.emit("delete")
        elif k == Qt.Key.Key_Z and ctrl:
            self.keyAction.emit("undo")
        elif k == Qt.Key.Key_S and not ctrl:
            self.keyAction.emit("split")
        elif k == Qt.Key.Key_K:
            self.keyAction.emit("keep")
        elif k == Qt.Key.Key_Escape:
            self.sel = None
            self.selectionChanged.emit(-1.0, -1.0)
            self.update()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            d = -1 if k == Qt.Key.Key_Left else 1
            self.seek.emit(max(0.0, min(self.duration, self.playhead + d * (5 if ctrl else 1))))
        else:
            super().keyPressEvent(e)
            return
        e.accept()


# ---------------------------------------------------------------- teks per kata

class TranscriptView(QTextEdit):
    wordClicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMinimumHeight(170)
        f = QFont(self.font())
        f.setPointSizeF(f.pointSizeF() + 1.5)
        self.setFont(f)
        self.spans: list[tuple[int, int]] = []
        self.starts: list[float] = []
        self._removed: list[bool] = []
        self._current = -1

    def set_words(self, words: list[stt.Word]) -> None:
        self.clear()
        self.spans, self.starts = [], [w.start for w in words]
        cur = self.textCursor()
        plain = QTextCharFormat()
        last_end = None
        for w in words:
            txt = w.text.strip()
            if last_end is not None:
                cur.insertText("\n\n" if w.start - last_end > 1.5 else " ", plain)
            a = cur.position()
            cur.insertText(txt, plain)
            self.spans.append((a, cur.position()))
            last_end = w.end
        self._removed = [False] * len(words)
        self._current = -1
        self.moveCursor(QTextCursor.MoveOperation.Start)
        self._refresh()

    def set_removed(self, mask: list[bool]) -> None:
        self._removed = mask
        self._refresh()

    def set_current_time(self, t: float) -> None:
        i = bisect.bisect_right(self.starts, t) - 1
        if i != self._current:
            self._current = i
            self._refresh()
            if 0 <= i < len(self.spans):
                c = QTextCursor(self.document())
                c.setPosition(self.spans[i][0])
                rect = self.cursorRect(c)
                if not self.viewport().rect().contains(rect.center()):
                    sb = self.verticalScrollBar()
                    sb.setValue(sb.value() + rect.top() - self.viewport().height() // 3)

    def _refresh(self) -> None:
        sels = []
        for i, (a, b) in enumerate(self.spans):
            removed = i < len(self._removed) and self._removed[i]
            if not removed and i != self._current:
                continue
            s = QTextEdit.ExtraSelection()
            c = QTextCursor(self.document())
            c.setPosition(a)
            c.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
            s.cursor = c
            fmt = QTextCharFormat()
            if removed:
                fmt.setFontStrikeOut(True)
                fmt.setForeground(QColor("#6c727c"))
            if i == self._current:
                fmt.setBackground(QColor("#3a2a1e"))
                if not removed:
                    fmt.setForeground(QColor(theme.ACCENT))
            s.format = fmt
            sels.append(s)
        self.setExtraSelections(sels)

    def selected_words(self) -> tuple[int, int] | None:
        c = self.textCursor()
        if not c.hasSelection() or not self.spans:
            return None
        a, b = c.selectionStart(), c.selectionEnd()
        hit = [i for i, (x, y) in enumerate(self.spans) if y > a and x < b]
        return (hit[0], hit[-1]) if hit else None

    def mouseReleaseEvent(self, e):  # noqa: N802
        super().mouseReleaseEvent(e)
        if e.button() == Qt.MouseButton.LeftButton and not self.textCursor().hasSelection():
            pos = self.cursorForPosition(e.position().toPoint()).position()
            for i, (a, b) in enumerate(self.spans):
                if a <= pos <= b:
                    self.wordClicked.emit(i)
                    break


# ---------------------------------------------------------------- halaman

class CutPage(QWidget):
    use_in_merge = Signal(str)       # file audio → narasi
    use_video_in_merge = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.info: tools.MediaInfo | None = None
        self.removed: list[edl.Range] = []
        self.splits: list[float] = []
        self.undo: list[tuple[list, list]] = []
        self.sel: edl.Range | None = None
        self.words: list[stt.Word] = []
        self._play_until: float | None = None
        self._load_thread: FuncThread | None = None
        self._stt: SttThread | None = None
        self._pending_claude = False
        self.last_outputs: list[str] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(page_header(
            "Potong",
            "Potong MP3, rekaman, atau video: drag di gelombang suara untuk memilih, lalu buang atau "
            "ambil bagian itu. Bisa buang hening otomatis, edit lewat teks, atau minta Claude "
            "merapikan “eh/emm” dan kalimat yang diulang."))

        # 1. file
        c1, l1 = card("1. File")
        self.pick = FilePicker("Pilih MP3 / rekaman / video…", FILTER_MEDIA, clearable=False)
        self.pick.changed.connect(self._load)
        l1.addWidget(self.pick)
        lay.addWidget(c1)

        # 2. editor
        c2, l2 = card("2. Pilih & potong")
        self.player = Player(video=True)
        self.player.slider.hide()
        self.player.time_lbl.hide()
        self.player.name_lbl.hide()
        self.player.play_btn.hide()
        self.player.video.setMinimumHeight(220)
        self.player.video.hide()
        l2.addWidget(self.player)
        self.wave = Waveform()
        self.wave.seek.connect(self._seek)
        self.wave.selectionChanged.connect(self._sel_changed)
        self.wave.keyAction.connect(self._key)
        self.wave.viewChanged.connect(self._view_changed)
        l2.addWidget(self.wave)
        self.hbar = QScrollBar(Qt.Orientation.Horizontal)
        self.hbar.setRange(0, 0)
        self.hbar.valueChanged.connect(self._hbar_moved)
        l2.addWidget(self.hbar)
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(0, 100)
        self.zoom.setMaximumWidth(220)
        self.zoom.valueChanged.connect(lambda v: self.wave.set_zoom(2 ** (v / 12.5)))
        self.play_btn = QPushButton("▶ Putar")
        self.play_btn.clicked.connect(self._toggle_play)
        self.play_sel_btn = QPushButton("▶ Putar pilihan")
        self.play_sel_btn.clicked.connect(self._play_selection)
        self.time_lbl = QLabel("0:00.0 / 0:00.0")
        self.time_lbl.setObjectName("Info")
        self.skip = QCheckBox("Lewati bagian yang dibuang")
        self.skip.setChecked(True)
        l2.addLayout(row(self.play_btn, self.play_sel_btn, self.time_lbl, None, self.skip,
                         QLabel("Zoom"), self.zoom))
        self.sel_lbl = QLabel("Belum ada pilihan — drag di gelombang suara.")
        self.sel_lbl.setObjectName("Hint")
        l2.addWidget(self.sel_lbl)
        self.btn_del = QPushButton("✂ Buang pilihan")
        self.btn_del.setObjectName("Primary")
        self.btn_del.clicked.connect(self._delete_sel)
        self.btn_keep = QPushButton("Ambil hanya pilihan")
        self.btn_keep.clicked.connect(self._keep_sel)
        self.btn_restore = QPushButton("↺ Kembalikan pilihan")
        self.btn_restore.clicked.connect(self._restore_sel)
        self.btn_split = QPushButton("┆ Pecah di sini")
        self.btn_split.clicked.connect(self._split_here)
        self.btn_clear_splits = QPushButton("Hapus penanda")
        self.btn_clear_splits.clicked.connect(self._clear_splits)
        self.btn_undo = QPushButton("↶ Undo")
        self.btn_undo.clicked.connect(self._undo)
        self.btn_reset = QPushButton("Reset")
        self.btn_reset.clicked.connect(self._reset)
        l2.addLayout(row(self.btn_del, self.btn_keep, self.btn_restore, self.btn_split,
                         self.btn_clear_splits, None, self.btn_undo, self.btn_reset))
        self.summary = QLabel("")
        self.summary.setObjectName("Info")
        l2.addWidget(self.summary)
        l2.addWidget(hint("Pintasan (klik gelombang dulu): Spasi = putar/jeda · Delete = buang "
                          "pilihan · K = ambil pilihan · S = pecah di playhead · Ctrl+Z = undo · "
                          "←/→ = geser 1 detik · Ctrl+scroll = zoom."))
        lay.addWidget(c2)

        # 3. bersihkan otomatis
        c3, l3 = card("3. Bersihkan otomatis")
        self.sil_db = LabeledSlider("Ambang hening", -60, -25, -40, unit="dB",
                                    tip="Suara di bawah ini dianggap hening.")
        self.sil_min = LabeledSlider("Hening minimal", 0.3, 3.0, 0.8, step=0.1, unit="detik")
        self.sil_keep = LabeledSlider("Sisakan jeda", 0.0, 1.0, 0.3, step=0.05, unit="detik",
                                      tip="Jeda yang tetap disisakan supaya bicara tidak terlalu rapat.")
        self.sil_btn = QPushButton("Cari & buang hening")
        self.sil_btn.clicked.connect(self._silence)
        l3.addWidget(self.sil_db)
        l3.addWidget(self.sil_min)
        l3.addWidget(self.sil_keep)
        l3.addLayout(row(self.sil_btn, None))

        sub = QLabel("Edit pakai teks")
        sub.setObjectName("CardTitle")
        l3.addWidget(sub)
        l3.addWidget(hint("Suara diketik jadi teks (offline). Blok kata di teks lalu klik Buang — "
                          "suaranya ikut terpotong. Klik satu kata untuk lompat ke situ."))
        self.stt_lang = language_combo()
        self.stt_btn = QPushButton("Tampilkan teks")
        self.stt_btn.clicked.connect(lambda: self._transcribe(then_claude=False))
        self.stt_status = QLabel("")
        self.stt_status.setObjectName("Hint")
        l3.addLayout(row(self.stt_btn, QLabel("Bahasa"), self.stt_lang, self.stt_status, None))
        self.tview = TranscriptView()
        self.tview.wordClicked.connect(lambda i: self._seek(self.words[i].start))
        self.tview.hide()
        l3.addWidget(self.tview)
        self.w_del = QPushButton("✂ Buang kata terpilih")
        self.w_del.clicked.connect(self._delete_words)
        self.w_restore = QPushButton("↺ Kembalikan kata terpilih")
        self.w_restore.clicked.connect(self._restore_words)
        self.claude_btn = QPushButton("✨ Rapikan dengan Claude")
        self.claude_btn.setToolTip("Claude menandai “eh/emm”, salah awal, dan kalimat yang diulang "
                                   "(yang dipakai versi terakhir). Bisa di-undo.")
        self.claude_btn.clicked.connect(self._claude_clean)
        self.claude_status = QLabel("")
        self.claude_status.setObjectName("Hint")
        self.claude_status.setWordWrap(True)
        l3.addLayout(row(self.w_del, self.w_restore, None, self.claude_btn))
        l3.addWidget(self.claude_status)
        lay.addWidget(c3)

        # 4. simpan
        c4, l4 = card("4. Simpan")
        self.fmt = QComboBox()
        self.bitrate = QComboBox()
        for br, lbl in [(128, "128 kbps"), (192, "192 kbps"), (256, "256 kbps"), (320, "320 kbps")]:
            self.bitrate.addItem(lbl, br)
        self.bitrate.setCurrentIndex(3)
        self.fade_in = QDoubleSpinBox()
        self.fade_out = QDoubleSpinBox()
        for sp in (self.fade_in, self.fade_out):
            sp.setRange(0, 10)
            sp.setSingleStep(0.5)
            sp.setDecimals(1)
            sp.setSuffix(" dtk")
        l4.addLayout(row(QLabel("Format"), self.fmt, QLabel("Kualitas"), self.bitrate,
                         QLabel("Fade in"), self.fade_in, QLabel("Fade out"), self.fade_out, None))
        self.save_btn = QPushButton("Simpan hasil")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(lambda: self._save(False))
        self.save_pieces_btn = QPushButton("Simpan per bagian")
        self.save_pieces_btn.clicked.connect(lambda: self._save(True))
        self.to_merge = QPushButton("Pakai di Gabung Video →")
        self.to_merge.setEnabled(False)
        self.to_merge.clicked.connect(self._to_merge)
        self.task = TaskPanel()
        self.task.finished.connect(self._saved)
        self.task.busyChanged.connect(lambda b: (self.save_btn.setEnabled(not b),
                                                 self.save_pieces_btn.setEnabled(not b)))
        l4.addLayout(row(self.save_btn, self.save_pieces_btn, None, self.to_merge))
        l4.addWidget(self.task)
        l4.addWidget(hint("File asli tidak diubah. Hasil disimpan di sebelahnya: “… - potong” atau "
                          "“… - bagian 1, 2, …”."))
        lay.addWidget(c4)
        lay.addStretch(1)

        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._tick)
        self.player.player.playbackStateChanged.connect(self._state)
        self._enable(False)

    # ------------------------------------------------------------ muat file
    def set_file(self, path: str) -> None:
        self.pick.set_path(path)

    def _enable(self, on: bool) -> None:
        for w in (self.play_btn, self.play_sel_btn, self.btn_del, self.btn_keep, self.btn_restore,
                  self.btn_split, self.btn_clear_splits, self.btn_undo, self.btn_reset, self.sil_btn,
                  self.stt_btn, self.claude_btn, self.save_btn, self.save_pieces_btn, self.zoom):
            w.setEnabled(on)

    def _load(self, path: str) -> None:
        self.player.unload()
        self.timer.stop()
        self.info = self.pick.info if path else None
        self.removed, self.splits, self.undo, self.sel = [], [], [], None
        self.words = []
        self.tview.hide()
        self.tview.clear()
        self.claude_status.setText("")
        self.stt_status.setText("")
        self.wave.set_data(self.wave._np.zeros(0, dtype="float32"), 100, 0)
        self._enable(False)
        if not path or not self.info or self.info.duration <= 0:
            self._update_summary()
            return
        self._fill_formats()
        self.player.video.setVisible(self.info.is_video)
        self.player.load(path)
        self.sel_lbl.setText("Memuat gelombang suara…")
        t = FuncThread(edl.load_peaks, path, 100, parent=self)
        t.done.connect(lambda peaks, p=path: self._loaded(p, peaks))
        t.failed.connect(lambda m: common.error_box(self, m))
        t.finished.connect(t.deleteLater)
        self._load_thread = t
        t.start()

    def _loaded(self, path: str, peaks) -> None:
        if path != self.pick.path() or not self.info:
            return
        self.wave.set_data(peaks, 100, self.info.duration)
        self.zoom.setValue(0)
        self._enable(True)
        self.sel_lbl.setText("Drag di gelombang suara untuk memilih bagian. Klik untuk pindah posisi.")
        self._refresh()
        self.wave.setFocus()

    def _fill_formats(self) -> None:
        self.fmt.clear()
        if self.info and self.info.is_video:
            self.fmt.addItem("Video MP4", "video")
        for key, (label, _ext) in jobs.AUDIO_FORMATS.items():
            if self.info and self.info.has_audio:
                self.fmt.addItem(label, key)
        idx = self.fmt.findData(edl.default_format(self.info)) if self.info else 0
        self.fmt.setCurrentIndex(max(0, idx))

    # ------------------------------------------------------------ edit
    @property
    def duration(self) -> float:
        return self.info.duration if self.info else 0.0

    def _push(self) -> None:
        self.undo.append((list(self.removed), list(self.splits)))
        del self.undo[:-200]

    def _apply(self, removed=None, splits=None) -> None:
        self._push()
        if removed is not None:
            self.removed = edl.merge_ranges(removed, self.duration)
        if splits is not None:
            self.splits = sorted(splits)
        self._refresh()

    def _refresh(self) -> None:
        self.wave.set_edit(self.removed, self.splits)
        if self.words:
            mask = edl.words_removed_mask([(w.start, w.end) for w in self.words], self.removed)
            self.tview.set_removed(mask)
        self._update_summary()

    def _update_summary(self) -> None:
        if not self.duration:
            self.summary.setText("")
            self.save_pieces_btn.setText("Simpan per bagian")
            return
        keep = edl.total(edl.keep_segments(self.duration, self.removed))
        gone = self.duration - keep
        n_pieces = len(edl.pieces(self.duration, self.splits))
        parts = [f"Hasil: {fmt_t(keep)} dari {fmt_t(self.duration)}"]
        if self.removed:
            parts.append(f"dibuang {fmt_t(gone)} di {len(self.removed)} tempat")
        if self.splits:
            parts.append(f"{n_pieces} bagian")
        self.summary.setText(" · ".join(parts))
        self.save_pieces_btn.setText(f"Simpan per bagian ({n_pieces} file)")
        self.save_pieces_btn.setEnabled(bool(self.splits) and not self.task.busy)

    def _need_sel(self) -> edl.Range | None:
        if not self.sel or self.sel[1] - self.sel[0] < edl.MIN_LEN:
            self.sel_lbl.setText("Pilih bagiannya dulu: drag di gelombang suara.")
            return None
        return self.sel

    def _delete_sel(self) -> None:
        s = self._need_sel()
        if s:
            self._apply(removed=edl.remove(self.removed, *s, self.duration))
            self._clear_sel()

    def _keep_sel(self) -> None:
        s = self._need_sel()
        if s:
            self._apply(removed=edl.keep_only(self.removed, *s, self.duration))
            self._clear_sel()

    def _restore_sel(self) -> None:
        s = self._need_sel()
        if s:
            self._apply(removed=edl.subtract(self.removed, *s))
            self._clear_sel()

    def _split_here(self) -> None:
        t = self.wave.playhead
        if edl.MIN_LEN < t < self.duration - edl.MIN_LEN and all(abs(t - s) > 0.05 for s in self.splits):
            self._apply(splits=[*self.splits, t])

    def _clear_splits(self) -> None:
        if self.splits:
            self._apply(splits=[])

    def _undo(self) -> None:
        if self.undo:
            self.removed, self.splits = self.undo.pop()
            self._refresh()

    def _reset(self) -> None:
        if self.removed or self.splits:
            self._apply(removed=[], splits=[])

    def _clear_sel(self) -> None:
        self.sel = None
        self.wave.set_selection(None)
        self._sel_changed(-1, -1)

    def _sel_changed(self, a: float, b: float) -> None:
        if a < 0:
            self.sel = None
            self.sel_lbl.setText("Belum ada pilihan — drag di gelombang suara.")
            return
        self.sel = (a, b)
        self.sel_lbl.setText(f"Pilihan: {fmt_t(a)} – {fmt_t(b)}  ({b - a:.1f} detik)")

    def _key(self, action: str) -> None:
        {"play": self._toggle_play, "delete": self._delete_sel, "undo": self._undo,
         "split": self._split_here, "keep": self._keep_sel}.get(action, lambda: None)()

    # ------------------------------------------------------------ tampilan / putar
    def _view_changed(self) -> None:
        w = self.wave
        if not w.duration or w.view_len >= w.duration:
            self.hbar.setRange(0, 0)
            return
        page = int(w.view_len * 1000)
        self.hbar.blockSignals(True)
        self.hbar.setRange(0, int((w.duration - w.view_len) * 1000))
        self.hbar.setPageStep(page)
        self.hbar.setSingleStep(max(1, page // 10))
        self.hbar.setValue(int(w.view_start * 1000))
        self.hbar.blockSignals(False)
        z = int(round(12.5 * math.log2(max(1.0, w.zoom_factor()))))
        self.zoom.blockSignals(True)
        self.zoom.setValue(max(0, min(100, z)))
        self.zoom.blockSignals(False)

    def _hbar_moved(self, v: int) -> None:
        self.wave.scroll_to(v / 1000.0)

    def _seek(self, t: float) -> None:
        self._play_until = None
        self.player.player.setPosition(int(t * 1000))
        self.wave.set_playhead(t)
        self._update_time(t)

    def _toggle_play(self) -> None:
        if not self.info:
            return
        mp = self.player.player
        if mp.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            mp.pause()
        else:
            self._play_until = None
            mp.play()

    def _play_selection(self) -> None:
        s = self._need_sel()
        if not s:
            return
        self.player.player.setPosition(int(s[0] * 1000))
        self._play_until = s[1]
        self.player.player.play()

    def _state(self, st) -> None:
        playing = st == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("❚❚ Jeda" if playing else "▶ Putar")
        if playing:
            self.timer.start()
        else:
            self.timer.stop()
            self._tick()

    def _tick(self) -> None:
        mp = self.player.player
        t = mp.position() / 1000.0
        if self._play_until is not None and t >= self._play_until:
            mp.pause()
            self._play_until = None
        elif self.skip.isChecked() and self._play_until is None:
            r = edl.is_removed(self.removed, t)
            if r:
                if r[1] >= self.duration - 0.05:
                    mp.pause()
                else:
                    mp.setPosition(int(r[1] * 1000) + 5)
                    t = r[1]
        self.wave.set_playhead(t)
        self._update_time(t)
        if self.words and self.tview.isVisible():
            self.tview.set_current_time(t)

    def _update_time(self, t: float) -> None:
        self.time_lbl.setText(f"{fmt_t(t)} / {fmt_t(self.duration)}")

    # ------------------------------------------------------------ hening
    def _silence(self) -> None:
        if not self.info:
            return
        self.sil_btn.setEnabled(False)
        self.sel_lbl.setText("Mencari bagian hening…")
        t = FuncThread(edl.detect_silence, self.pick.path(), self.sil_db.value(),
                       self.sil_min.value(), self.duration, parent=self)

        def done(sil) -> None:
            cuts = edl.silence_cuts(sil, self.duration, self.sil_keep.value())
            if not cuts:
                self.sel_lbl.setText("Tidak ada hening panjang yang ditemukan. Coba naikkan ambang "
                                     "(mis. −35 dB) atau perkecil hening minimal.")
                return
            before = edl.total(edl.keep_segments(self.duration, self.removed))
            self._apply(removed=[*self.removed, *cuts])
            after = edl.total(edl.keep_segments(self.duration, self.removed))
            self.sel_lbl.setText(f"{len(cuts)} bagian hening dibuang (hemat {before - after:.1f} "
                                 "detik). Tidak cocok? Klik Undo.")

        t.done.connect(done)
        t.failed.connect(lambda m: common.error_box(self, m))
        t.finished.connect(lambda: (self.sil_btn.setEnabled(True), t.deleteLater()))
        t.start()

    # ------------------------------------------------------------ teks
    def _transcribe(self, then_claude: bool) -> None:
        if not self.info or self._stt is not None:
            return
        key = current_model()
        if not stt.model_path(key):
            common.error_box(self, "Mesin suara → teks belum terpasang.")
            return
        self._pending_claude = then_claude
        self.stt_btn.setEnabled(False)
        self.stt_status.setText("Mengetik suara jadi teks (offline)…")
        th = _WordsThread(self.pick.path(), key, self.stt_lang.currentData(), self)
        th.progress.connect(lambda f: self.stt_status.setText(f"Mengetik… {int(f * 100)}%"))
        th.done.connect(self._words_ready)
        th.failed.connect(lambda m: (self.stt_status.setText("Gagal."), common.error_box(self, m)))
        th.finished.connect(self._stt_finished)
        self._stt = th
        th.start()

    def _stt_finished(self) -> None:
        th, self._stt = self._stt, None
        self.stt_btn.setEnabled(bool(self.info))
        if th:
            th.deleteLater()

    def _words_ready(self, tr: stt.Transcript) -> None:
        self.words = [w for w in tr.words if w.text.strip()]
        if not self.words:
            self.stt_status.setText("Tidak ada ucapan yang terdengar.")
            return
        self.tview.set_words(self.words)
        self.tview.show()
        self._refresh()
        self.stt_status.setText(f"{len(self.words)} kata.")
        self.stt_btn.setText("Ketik ulang")
        if self._pending_claude:
            self._pending_claude = False
            self._claude_clean()

    def _word_ranges(self) -> list[edl.Range]:
        return [(w.start, w.end) for w in self.words]

    def _delete_words(self) -> None:
        r = self.tview.selected_words()
        if not r:
            self.stt_status.setText("Blok dulu kata yang mau dibuang di teks.")
            return
        cut = edl.words_cut(self._word_ranges(), r[0], r[1], self.duration)
        self._apply(removed=[*self.removed, cut])

    def _restore_words(self) -> None:
        r = self.tview.selected_words()
        if not r:
            self.stt_status.setText("Blok dulu kata yang mau dikembalikan.")
            return
        a, b = edl.words_cut(self._word_ranges(), r[0], r[1], self.duration)
        self._apply(removed=edl.subtract(self.removed, a, b))

    def _claude_clean(self) -> None:
        if not self.info:
            return
        if not claude_ui.get_key():
            claude_ui.require_key(self)
            return
        if not self.words:
            self.claude_status.setText("Mengetik suara jadi teks dulu, lalu dikirim ke Claude…")
            self._transcribe(then_claude=True)
            return
        texts = [w.text.strip() for w in self.words]
        claude_ui.run(self, claude.clean_take, texts, self.stt_lang.currentText(),
                      on_done=self._claude_done, button=self.claude_btn, status=self.claude_status,
                      busy_text="Claude sedang membaca transkrip…")

    def _claude_done(self, cuts: list) -> None:
        if not cuts:
            self.claude_status.setText("✨ Claude: rekamanmu sudah rapi, tidak ada yang perlu dibuang.")
            return
        wr = self._word_ranges()
        ranges = [edl.words_cut(wr, a, b, self.duration) for a, b, _why in cuts]
        self._apply(removed=[*self.removed, *ranges])
        counts: dict[str, int] = {}
        for _a, _b, why in cuts:
            counts[why] = counts.get(why, 0) + 1
        detail = ", ".join(f"{n}× {why}" for why, n in counts.items())
        examples = "; ".join("“" + " ".join(w.text.strip() for w in self.words[a:b + 1])[:40] + "”"
                             for a, b, _w in cuts[:4])
        self.claude_status.setText(f"✨ Claude membuang {len(cuts)} bagian ({detail}). Contoh: "
                                   f"{examples}. Dengarkan dulu — tidak cocok? Klik Undo.")

    # ------------------------------------------------------------ simpan
    def _save(self, per_piece: bool) -> None:
        if not self.info:
            return
        fmt = self.fmt.currentData()
        try:
            job_list = edl.save_jobs(self.pick.path(), self.removed, self.splits, info=self.info,
                                     fmt=fmt, bitrate=self.bitrate.currentData(),
                                     fade_in=self.fade_in.value(), fade_out=self.fade_out.value(),
                                     per_piece=per_piece)
        except Exception as exc:
            common.error_box(self, str(exc))
            return
        self.player.player.pause()
        self.task.start(job_list, "Menyimpan…")

    def _saved(self, outputs: list) -> None:
        self.last_outputs = outputs
        self.to_merge.setEnabled(bool(outputs))

    def _to_merge(self) -> None:
        if not self.last_outputs:
            return
        out = self.last_outputs[0]
        if out.lower().endswith(".mp4"):
            self.use_video_in_merge.emit(out)
        else:
            self.use_in_merge.emit(out)

    def shutdown(self) -> None:
        self.timer.stop()
        if self._stt:
            self._stt.cancel()
            self._stt.wait(5000)


class _WordsThread(SttThread):
    """Sama seperti SttThread, tapi minta waktu per kata + mode 'apa adanya' (ikut eh/emm)."""

    def run(self) -> None:
        try:
            t = stt.transcribe(self.path, self.model, self.language, on_progress=self.progress.emit,
                               cancel=self._cancel, words=True, verbatim=True)
            self.done.emit(t)
        except stt.SttCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))

