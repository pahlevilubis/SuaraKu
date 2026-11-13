"""Menu Asisten Claude: sambungkan API key, bikin naskah, bikin caption."""

from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from ..core import claude, stt
from . import claude_ui, common
from .common import FILTER_MEDIA, FuncThread, card, hint, page_header, row
from .page_transcribe import current_model


def _copy(text: str, status: QLabel) -> None:
    QApplication.clipboard().setText(text)
    status.setText("Disalin. Tinggal tempel (Ctrl+V).")


class ClaudePage(QWidget):
    send_to_prompter = Signal(str)
    send_to_tts = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stt_thread: FuncThread | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(page_header(
            "Asisten Claude",
            "Claude bantu bikin naskah, merapikan rekaman, meracik efek suara dari kalimat biasa, "
            "dan bikin caption LinkedIn/WA. Yang dikirim ke Claude cuma teks — bukan suaramu."))

        # --- sambungkan
        c1, l1 = card("1. Sambungkan (sekali saja)")
        self.state = QLabel("")
        self.state.setWordWrap(True)
        l1.addWidget(self.state)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Tempel API key di sini (diawali sk-ant-…)")
        show = QCheckBox("Lihat")
        show.toggled.connect(lambda on: self.key.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        l1.addLayout(row(QLabel("API key"), self.key, show))
        self.model = QComboBox()
        for mid, label in claude.MODELS:
            self.model.addItem(label, mid)
        self.model.setCurrentIndex(max(0, self.model.findData(claude_ui.get_model())))
        self.model.currentIndexChanged.connect(lambda _i: claude_ui.set_model(self.model.currentData()))
        l1.addLayout(row(QLabel("Model"), self.model, None))
        self.save_btn = QPushButton("Simpan & tes")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self._save_key)
        forget = QPushButton("Hapus kunci")
        forget.clicked.connect(self._forget)
        get = QPushButton("Bikin API key ↗")
        get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(claude.KEY_URL)))
        l1.addLayout(row(self.save_btn, forget, None, get))
        l1.addWidget(hint(
            "Cara dapat API key: buka platform.claude.com → login → Settings → API keys → Create "
            "key → salin. Pemakaian API dibayar per pakai, terpisah dari langganan Claude.ai "
            "(paket Max/Team/Ultra dapat kredit API bulanan; Pro tidak). Dengan Haiku 5.5, sekali "
            "pakai biasanya jauh di bawah Rp100. API key disimpan terenkripsi di laptop ini."))
        lay.addWidget(c1)

        # --- naskah
        c2, l2 = card("2. Bikin naskah narasi",
                      "Tulis poin-poin yang mau kamu pamerkan, Claude yang merangkai jadi naskah "
                      "enak dibaca — lalu kirim ke teleprompter.")
        self.points = QPlainTextEdit()
        self.points.setPlaceholderText(
            "- Aku bikin aplikasi SuaraKu sendiri bareng Claude\n"
            "- Bisa ubah MP4 ke MP3, rekam suara, kasih efek suara penyiar\n"
            "- Ada mode live buat telepon")
        self.points.setMaximumHeight(130)
        l2.addWidget(self.points)
        self.seconds = QComboBox()
        for sec in (15, 30, 45, 60, 90):
            self.seconds.addItem(f"{sec} detik", sec)
        self.seconds.setCurrentIndex(1)
        self.style = QComboBox()
        self.style.addItems(claude.STYLES)
        self.style.setCurrentIndex(1)
        self.lang = QComboBox()
        self.lang.addItems(claude.LANGS)
        l2.addLayout(row(QLabel("Durasi"), self.seconds, QLabel("Gaya"), self.style,
                         QLabel("Bahasa"), self.lang, None))
        self.script_btn = QPushButton("✨ Bikin naskah")
        self.script_btn.setObjectName("Primary")
        self.script_btn.clicked.connect(self._script)
        self.script_status = QLabel("")
        self.script_status.setObjectName("Hint")
        l2.addLayout(row(self.script_btn, self.script_status, None))
        self.script = QPlainTextEdit()
        self.script.setPlaceholderText("Naskah dari Claude muncul di sini — bisa kamu edit.")
        self.script.setMinimumHeight(170)
        l2.addWidget(self.script)
        to_prompter = QPushButton("Kirim ke Teleprompter →")
        to_prompter.clicked.connect(lambda: self._send(self.send_to_prompter))
        to_tts = QPushButton("Kirim ke Suara AI →")
        to_tts.clicked.connect(lambda: self._send(self.send_to_tts))
        cp = QPushButton("Salin")
        cp.clicked.connect(lambda: _copy(self.script.toPlainText(), self.script_status))
        l2.addLayout(row(to_prompter, to_tts, None, cp))
        lay.addWidget(c2)

        # --- caption
        c3, l3 = card("3. Bikin caption LinkedIn & WA",
                      "Dari isi narasi videomu. Tempel teksnya, atau ambil otomatis dari file.")
        self.source = QPlainTextEdit()
        self.source.setPlaceholderText("Teks narasi / transkrip video…")
        self.source.setMaximumHeight(120)
        from_file = QPushButton("Ambil teks dari file…")
        from_file.clicked.connect(self._from_file)
        self.src_status = QLabel("")
        self.src_status.setObjectName("Hint")
        l3.addWidget(self.source)
        l3.addLayout(row(from_file, self.src_status, None))
        self.context = QLineEdit()
        self.context.setPlaceholderText("Konteks singkat (opsional), mis. “pamer app buatan sendiri, "
                                        "aku programmer AS400”")
        self.cap_lang = QComboBox()
        self.cap_lang.addItems(claude.LANGS)
        l3.addLayout(row(QLabel("Konteks"), self.context, QLabel("Bahasa"), self.cap_lang))
        self.cap_btn = QPushButton("✨ Bikin caption")
        self.cap_btn.setObjectName("Primary")
        self.cap_btn.clicked.connect(self._captions)
        self.cap_status = QLabel("")
        self.cap_status.setObjectName("Hint")
        l3.addLayout(row(self.cap_btn, self.cap_status, None))
        self.title_out = QLineEdit()
        self.title_out.setPlaceholderText("Judul video")
        self.li = QPlainTextEdit()
        self.li.setPlaceholderText("Caption LinkedIn")
        self.li.setMinimumHeight(160)
        self.wa = QPlainTextEdit()
        self.wa.setPlaceholderText("Status WhatsApp")
        self.wa.setMaximumHeight(70)
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("Hashtag")
        cp_li = QPushButton("Salin LinkedIn + hashtag")
        cp_li.clicked.connect(lambda: _copy(
            (self.li.toPlainText().strip() + "\n\n" + self.tags.text().strip()).strip(), self.cap_status))
        cp_wa = QPushButton("Salin status WA")
        cp_wa.clicked.connect(lambda: _copy(self.wa.toPlainText(), self.cap_status))
        l3.addWidget(self.title_out)
        l3.addWidget(self.li)
        l3.addWidget(self.tags)
        l3.addWidget(self.wa)
        l3.addLayout(row(cp_li, cp_wa, None))
        lay.addWidget(c3)
        lay.addStretch(1)
        self._refresh_state()

    # ------------------------------------------------------------ kunci
    def _refresh_state(self, extra: str = "") -> None:
        has = bool(claude_ui.get_key())
        self.state.setText(("✓ Claude tersambung. " if has else
                            "Belum tersambung — isi API key di bawah. ") + extra)
        self.state.setObjectName("Ok" if has else "Err")
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)

    def _save_key(self) -> None:
        key = self.key.text().strip()
        if not key:
            if claude_ui.get_key():
                key = claude_ui.get_key()
            else:
                common.error_box(self, "Tempel API key-nya dulu.")
                return
        self.save_btn.setEnabled(False)
        self.state.setText("Mengetes API key…")
        t = FuncThread(claude.test_key, key, parent=self)

        def ok(n) -> None:
            claude_ui.set_key(key)
            self.key.clear()
            self._refresh_state(f"API key valid ({n} model tersedia).")

        def bad(msg: str) -> None:
            self._refresh_state()
            common.error_box(self, msg, "API key")

        t.done.connect(ok)
        t.failed.connect(bad)
        t.finished.connect(lambda: (self.save_btn.setEnabled(True), t.deleteLater()))
        t.start()

    def _forget(self) -> None:
        claude_ui.clear_key()
        self.key.clear()
        self._refresh_state("API key dihapus dari laptop ini.")

    # ------------------------------------------------------------ naskah
    def _script(self) -> None:
        pts = self.points.toPlainText().strip()
        if not pts:
            common.error_box(self, "Tulis poin-poinnya dulu.")
            return
        claude_ui.run(self, claude.write_script, pts, self.seconds.currentData(),
                      self.style.currentText(), self.lang.currentText(),
                      on_done=self._script_done, button=self.script_btn, status=self.script_status,
                      busy_text="Claude sedang menulis naskah…")

    def _script_done(self, r: dict) -> None:
        self.script.setPlainText(r["naskah"])
        words = len(r["naskah"].split())
        self.script_status.setText(f"“{r['judul']}” · {words} kata · ±{int(words / 2.3)} detik dibaca")

    def _send(self, signal) -> None:
        text = self.script.toPlainText().strip()
        if not text:
            common.error_box(self, "Naskahnya masih kosong.")
            return
        signal.emit(text)

    # ------------------------------------------------------------ caption
    def set_source_text(self, text: str) -> None:
        self.source.setPlainText(text)

    def _from_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        if self._stt_thread is not None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Pilih video / rekaman", common.last_dir(),
                                              FILTER_MEDIA)
        if not path:
            return
        key = current_model()
        if not stt.model_path(key):
            common.error_box(self, "Mesin suara → teks belum terpasang.")
            return
        self.src_status.setText("Mengetik isi file (offline)…")
        t = FuncThread(stt.transcribe, path, key, None, parent=self)
        t.done.connect(lambda tr: (self.source.setPlainText(tr.paragraphs()),
                                   self.src_status.setText("Teks diambil dari file.")))
        t.failed.connect(lambda m: (self.src_status.setText("Gagal."), common.error_box(self, m)))

        def fin() -> None:
            self._stt_thread = None
            t.deleteLater()

        t.finished.connect(fin)
        self._stt_thread = t
        t.start()

    def _captions(self) -> None:
        text = self.source.toPlainText().strip()
        if not text:
            common.error_box(self, "Isi teks narasinya dulu, atau ambil dari file.")
            return
        claude_ui.run(self, claude.captions, text, self.context.text(), self.cap_lang.currentText(),
                      on_done=self._cap_done, button=self.cap_btn, status=self.cap_status,
                      busy_text="Claude sedang bikin caption…")

    def _cap_done(self, c: dict) -> None:
        self.title_out.setText(c["judul"])
        self.li.setPlainText(c["linkedin"])
        self.wa.setPlainText(c["wa_status"])
        self.tags.setText(" ".join(c["hashtag"]))
        self.cap_status.setText("Selesai. Edit sesukamu, lalu salin.")
