"""Menu Konversi: MP4 → MP3 dan format lain, bisa banyak file sekaligus + potong."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QFileDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from ..core import jobs, tools
from . import common
from .common import FILTER_MEDIA, TaskPanel, card, hint, page_header, row


ROLE_PATH = int(Qt.ItemDataRole.UserRole)
ROLE_INFO = ROLE_PATH + 1


class FileList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Files")
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMinimumHeight(150)

    def add_paths(self, paths: list[str]) -> None:
        existing = {self.item(i).data(ROLE_PATH) for i in range(self.count())}
        for p in paths:
            if not os.path.isfile(p) or p in existing:
                continue
            try:
                info = tools.probe(p)
                text = f"{Path(p).name}    ·    {info.describe()}"
            except Exception as exc:
                info, text = None, f"{Path(p).name}    ·    {exc}"
            it = QListWidgetItem(text)
            it.setData(ROLE_PATH, p)
            it.setData(ROLE_INFO, info)
            self.addItem(it)
            common.remember_dir(p)

    def items_info(self) -> list[tuple[str, tools.MediaInfo | None]]:
        return [(self.item(i).data(ROLE_PATH),
                 self.item(i).data(ROLE_INFO)) for i in range(self.count())]

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
        e.acceptProposedAction()


class ConvertPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.out_folder = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(page_header(
            "Konversi",
            "Ubah video jadi MP3, ganti format audio/video, kecilkan ukuran video, "
            "atau potong bagian tertentu. Bisa banyak file sekaligus."))

        c1, l1 = card("1. File", "Tarik (drag) file ke kotak di bawah, atau klik Tambah file.")
        self.files = FileList()
        add = QPushButton("Tambah file…")
        add.clicked.connect(self._add)
        rem = QPushButton("Hapus yang dipilih")
        rem.clicked.connect(self._remove)
        clr = QPushButton("Kosongkan")
        clr.clicked.connect(self.files.clear)
        l1.addWidget(self.files)
        l1.addLayout(row(add, rem, clr, None))
        lay.addWidget(c1)

        c2, l2 = card("2. Jadi format apa?")
        self.fmt = QComboBox()
        self.fmt.addItem("— Audio —")
        self.fmt.model().item(0).setEnabled(False)
        for key, (label, _) in jobs.AUDIO_FORMATS.items():
            self.fmt.addItem(label, key)
        self.fmt.addItem("— Video —")
        self.fmt.model().item(self.fmt.count() - 1).setEnabled(False)
        for key, (label, _) in jobs.VIDEO_FORMATS.items():
            self.fmt.addItem(label, key)
        self.fmt.setCurrentIndex(1)
        self.bitrate = QComboBox()
        for br, lbl in [(128, "128 kbps (hemat)"), (192, "192 kbps (bagus)"),
                        (256, "256 kbps"), (320, "320 kbps (terbaik)")]:
            self.bitrate.addItem(lbl, br)
        self.bitrate.setCurrentIndex(1)
        self.br_lbl = QLabel("Kualitas audio")
        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        l2.addLayout(row(QLabel("Format"), self.fmt, self.br_lbl, self.bitrate, None))
        lay.addWidget(c2)

        c3, l3 = card("3. Potong (opsional)",
                      "Isi kalau cuma mau ambil sebagian. Format waktu: 90, 1:30, atau 0:01:30. "
                      "Kosongkan = dari awal / sampai habis.")
        self.start = QLineEdit()
        self.start.setPlaceholderText("dari awal")
        self.end = QLineEdit()
        self.end.setPlaceholderText("sampai habis")
        for w in (self.start, self.end):
            w.setMaximumWidth(140)
        l3.addLayout(row(QLabel("Mulai"), self.start, QLabel("Selesai"), self.end, None))
        lay.addWidget(c3)

        c4, l4 = card("4. Simpan di")
        self.same = QRadioButton("Folder yang sama dengan file asli")
        self.other = QRadioButton("Folder lain…")
        self.same.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.same)
        grp.addButton(self.other)
        self.other.toggled.connect(self._pick_folder)
        self.folder_lbl = QLabel("")
        self.folder_lbl.setObjectName("Info")
        l4.addLayout(row(self.same, self.other, self.folder_lbl, None))
        lay.addWidget(c4)

        self.go = QPushButton("Mulai konversi")
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self._go)
        self.task = TaskPanel()
        self.task.busyChanged.connect(lambda b: self.go.setEnabled(not b))
        lay.addLayout(row(self.go, None))
        lay.addWidget(self.task)
        lay.addWidget(hint("Tips: “MP4 kecil (720p)” cocok buat status WA dan LinkedIn — ukurannya "
                           "jauh lebih kecil tapi masih tajam di HP."))
        lay.addStretch(1)

    # ------------------------------------------------------------
    def _add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Pilih file", common.last_dir(), FILTER_MEDIA)
        self.files.add_paths(paths)

    def _remove(self) -> None:
        for it in self.files.selectedItems():
            self.files.takeItem(self.files.row(it))

    def _fmt_changed(self) -> None:
        key = self.fmt.currentData()
        audio = key in ("mp3", "m4a", "ogg")
        self.bitrate.setVisible(audio)
        self.br_lbl.setVisible(audio)

    def _pick_folder(self, checked: bool) -> None:
        if not checked:
            self.folder_lbl.setText("")
            return
        d = QFileDialog.getExistingDirectory(self, "Simpan di folder", common.last_dir())
        if d:
            self.out_folder = d
            self.folder_lbl.setText(d)
        else:
            self.same.setChecked(True)

    def add_files(self, paths: list[str]) -> None:
        self.files.add_paths(paths)

    def _go(self) -> None:
        items = self.files.items_info()
        if not items:
            common.error_box(self, "Tambahkan file dulu ya.")
            return
        key = self.fmt.currentData()
        if not key:
            common.error_box(self, "Pilih formatnya dulu.")
            return
        try:
            start = tools.parse_time(self.start.text())
            end = tools.parse_time(self.end.text())
        except ValueError as exc:
            common.error_box(self, str(exc))
            return
        ext = jobs.ALL_FORMATS[key][1]
        suffix = {"mp4_small": " - kecil", "mp4_mute": " - tanpa suara"}.get(key, "")
        if start or end:
            suffix += " - potongan"
        folder = self.out_folder if self.other.isChecked() and self.out_folder else None
        job_list, problems = [], []
        for path, info in items:
            try:
                if info is None:
                    info = tools.probe(path)
                sfx = suffix
                if Path(path).suffix.lower() == ext and not sfx:
                    sfx = " - SuaraKu"
                dst = jobs.output_name(path, sfx, ext, folder)
                job_list.append(jobs.convert(path, dst, key, bitrate=self.bitrate.currentData(),
                                             start=start, end=end, info=info))
            except Exception as exc:
                problems.append(f"• {Path(path).name}: {exc}")
        if problems:
            common.error_box(self, "Beberapa file dilewati:\n" + "\n".join(problems))
        if job_list:
            self.task.start(job_list, "Mengonversi…")
