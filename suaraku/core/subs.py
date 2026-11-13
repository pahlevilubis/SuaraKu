"""Subtitle otomatis: kata dari Whisper → potongan pendek → file .ass bergaya → ditempel ke video."""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import stt, tools

FONT_FILE = "DejaVuSans-Bold.ttf"
FONT_NAME = "DejaVu Sans"

STYLES = {
    "putih": "Putih + garis hitam (paling aman)",
    "kuning": "Kuning + garis hitam",
    "kotak": "Putih di kotak hitam",
}
SIZES = {"kecil": 0.045, "sedang": 0.058, "besar": 0.075}


@dataclass
class Caption:
    start: float
    end: float
    text: str


def _ends_sentence(word: str) -> bool:
    return word.rstrip().endswith((".", "?", "!", "…"))


def build_captions(t: stt.Transcript, offset: float = 0.0, max_chars: int = 42,
                   max_dur: float = 3.5, gap_break: float = 0.6) -> list[Caption]:
    words = t.words
    caps: list[Caption] = []
    if not words:
        for s in t.segments:
            text = s.text.strip()
            if text:
                caps.append(Caption(s.start + offset, s.end + offset, text))
        return caps

    cur: list[stt.Word] = []

    def flush() -> None:
        if cur:
            text = " ".join(w.text.strip() for w in cur if w.text.strip())
            if text:
                caps.append(Caption(cur[0].start + offset, cur[-1].end + offset, text))
        cur.clear()

    for w in words:
        if not w.text.strip():
            continue
        if cur:
            length = len(" ".join(x.text.strip() for x in cur)) + 1 + len(w.text.strip())
            too_long = length > max_chars or (w.end - cur[0].start) > max_dur
            gap = w.start - cur[-1].end > gap_break
            sentence = _ends_sentence(cur[-1].text) and len(cur) >= 2
            if too_long or gap or sentence:
                flush()
        cur.append(w)
    flush()

    # rapikan waktu: minimal 0,7 detik tampil, tidak bertumpuk
    for i, c in enumerate(caps):
        nxt = caps[i + 1].start if i + 1 < len(caps) else None
        end = max(c.end + 0.15, c.start + 0.7)
        if nxt is not None:
            end = min(end, nxt - 0.02)
        c.end = max(c.start + 0.2, end)
    return caps


def _ass_time(sec: float) -> str:
    cs = int(round(max(0.0, sec) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def ass_document(caps: list[Caption], width: int, height: int, style: str = "putih",
                 size: str = "sedang") -> str:
    width = width or 1280
    height = height or 720
    base = min(width, height)
    portrait = height > width
    fs = max(16, int(round(base * SIZES.get(size, SIZES["sedang"]) * (1.15 if portrait else 1.0))))
    margin_v = int(round(height * (0.14 if portrait else 0.07)))
    margin_lr = int(round(width * 0.06))
    primary, outline_col, back = "&H00FFFFFF", "&H00000000", "&H80000000"
    border_style, outline, shadow = 1, max(2, fs // 14), 1
    if style == "kuning":
        primary = "&H0000E1FF"
    elif style == "kotak":
        border_style, outline, shadow = 3, max(4, fs // 6), 0
        outline_col = "&H40000000"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT_NAME},{fs},{primary},&H000000FF,{outline_col},{back},-1,0,0,0,100,100,0,0,{border_style},{outline},{shadow},2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,{_ass_text(c.text)}"
             for c in caps]
    return head + "\n".join(lines) + "\n"


def prepare_dir(caps: list[Caption], width: int, height: int, style: str = "putih",
                size: str = "sedang") -> str:
    """Buat folder sementara berisi subs.ass + fonts/ (dipakai sebagai cwd FFmpeg).

    Kembalikan path subs.ass.
    """
    root = Path(tempfile.gettempdir()) / "SuaraKu" / f"subs-{int(time.time() * 1000)}"
    (root / "fonts").mkdir(parents=True, exist_ok=True)
    font = tools.asset_path(f"fonts/{FONT_FILE}")
    if font:
        shutil.copy(font, root / "fonts" / FONT_FILE)
    ass = root / "subs.ass"
    ass.write_text(ass_document(caps, width, height, style, size), encoding="utf-8")
    return str(ass)


def to_srt(caps: list[Caption]) -> str:
    out = []
    for i, c in enumerate(caps, 1):
        out += [str(i), f"{stt._srt_time(c.start)} --> {stt._srt_time(c.end)}", c.text, ""]
    return "\n".join(out)
