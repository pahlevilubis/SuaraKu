"""Tema gelap SuaraKu (aksen oranye hangat)."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG = "#121317"
SURFACE = "#1b1d22"
SURFACE_2 = "#24272e"
FIELD = "#16181c"
BORDER = "#2f333b"
TEXT = "#ECEAE6"
MUTED = "#9aa0a9"
ACCENT = "#FF8A3D"
ACCENT_HOVER = "#ff9d5c"
ACCENT_DIM = "#2d241d"
DANGER = "#e5484d"
OK = "#5dd39e"

QSS = f"""
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
QLabel {{ background: transparent; }}
QLabel#H1 {{ font-size: 18pt; font-weight: 600; }}
QLabel#Sub, QLabel#Hint {{ color: {MUTED}; }}
QLabel#CardTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#Info {{ color: {MUTED}; font-size: 9pt; }}
QLabel#Big {{ font-size: 26pt; font-weight: 300; }}
QLabel#Ok {{ color: {OK}; }}
QLabel#Err {{ color: {DANGER}; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid #2a2d34; border-radius: 12px; }}
QFrame#Sidebar {{ background: #0e0f12; }}
QLabel#Brand {{ font-size: 15pt; font-weight: 700; color: {TEXT}; }}
QLabel#BrandSub {{ color: {MUTED}; font-size: 8.5pt; }}

QPushButton {{ background: #2a2d35; border: 1px solid #353943; border-radius: 8px;
    padding: 7px 14px; color: {TEXT}; }}
QPushButton:hover {{ background: #333743; }}
QPushButton:pressed {{ background: #262930; }}
QPushButton:disabled {{ color: #666b74; background: #1f2126; border-color: #2a2d34; }}
QPushButton#Primary {{ background: {ACCENT}; color: #1a1209; border: none; font-weight: 600;
    padding: 9px 18px; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: #4a3a2e; color: #8f7a68; }}
QPushButton#Record {{ background: {DANGER}; color: white; border: none; border-radius: 30px;
    font-size: 12pt; font-weight: 700; min-height: 60px; padding: 0 28px; }}
QPushButton#Record:hover {{ background: #f05a5f; }}
QPushButton#Record[recording="true"] {{ background: #3a3d45; color: {TEXT}; }}
QPushButton#Preset {{ text-align: left; padding: 9px 12px; }}
QPushButton#Preset:checked {{ border: 1px solid {ACCENT}; background: {ACCENT_DIM}; color: {ACCENT}; }}
QPushButton#Link {{ background: transparent; border: none; color: {ACCENT}; padding: 2px 4px; }}
QPushButton#Link:hover {{ text-decoration: underline; }}

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {FIELD}; border: 1px solid #30343c; border-radius: 8px; padding: 6px 8px;
    selection-background-color: {ACCENT}; selection-color: #1a1209; color: {TEXT}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid #30343c;
    selection-background-color: #3a2a1e; selection-color: {TEXT}; }}
QListWidget#Files {{ background: {FIELD}; border: 1px dashed #3a3f48; border-radius: 10px; padding: 6px; }}
QListWidget#Files::item {{ padding: 6px; border-radius: 6px; }}
QListWidget#Files::item:selected {{ background: #2a2d35; color: {TEXT}; }}

QProgressBar {{ background: {FIELD}; border: 1px solid #30343c; border-radius: 6px;
    min-height: 10px; max-height: 10px; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QProgressBar#Level::chunk {{ background: {OK}; }}

QSlider::groove:horizontal {{ height: 4px; background: #30343c; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0;
    border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: white; }}

QListWidget#Nav {{ background: transparent; border: none; outline: 0; }}
QListWidget#Nav::item {{ padding: 10px 12px; border-radius: 8px; margin: 2px 0; color: #b9bec6; }}
QListWidget#Nav::item:selected {{ background: #2a1f17; color: {ACCENT}; }}
QListWidget#Nav::item:hover:!selected {{ background: #1a1c21; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #33373f; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QSplitter::handle {{ background: transparent; }}
QStatusBar {{ background: #0e0f12; color: {MUTED}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: FIELD,
        QPalette.ColorRole.AlternateBase: SURFACE,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: SURFACE_2,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: "#1a1209",
        QPalette.ColorRole.ToolTipBase: SURFACE_2,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.PlaceholderText: "#6c727c",
        QPalette.ColorRole.Link: ACCENT,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#666b74"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#666b74"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#666b74"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
