"""Mencari FFmpeg/FFprobe dan membaca info file media."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
# Supaya jendela hitam (console) FFmpeg tidak muncul di Windows.
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


class FfmpegMissing(RuntimeError):
    pass


def app_dir() -> Path:
    """Folder tempat SuaraKu.exe berada (atau root repo saat dijalankan dari kode)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _candidates(name: str) -> list[Path]:
    exe = name + (".exe" if IS_WINDOWS else "")
    roots = [app_dir()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    out: list[Path] = []
    for root in roots:
        out += [root / "ffmpeg" / exe, root / "ffmpeg" / "bin" / exe]
    return out


def _find(name: str) -> str | None:
    override = os.environ.get(f"SUARAKU_{name.upper()}")
    if override and Path(override).exists():
        return override
    for cand in _candidates(name):
        if cand.exists():
            return str(cand)
    return shutil.which(name)


@lru_cache(maxsize=None)
def ffmpeg_path() -> str:
    path = _find("ffmpeg")
    if not path:
        raise FfmpegMissing("FFmpeg tidak ditemukan. Coba install ulang SuaraKu.")
    return path


@lru_cache(maxsize=None)
def ffprobe_path() -> str:
    path = _find("ffprobe")
    if not path:
        raise FfmpegMissing("FFprobe tidak ditemukan. Coba install ulang SuaraKu.")
    return path


def run(args: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW,
        stdin=subprocess.DEVNULL,
    )


@lru_cache(maxsize=None)
def available_filters() -> frozenset[str]:
    res = run([ffmpeg_path(), "-hide_banner", "-filters"])
    names = set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and "->" in parts[2]:
            names.add(parts[1])
    return frozenset(names)


def has_filter(name: str) -> bool:
    try:
        return name in available_filters()
    except Exception:
        return False


@lru_cache(maxsize=None)
def available_encoders() -> frozenset[str]:
    res = run([ffmpeg_path(), "-hide_banner", "-encoders"])
    names = set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


def has_encoder(name: str) -> bool:
    try:
        return name in available_encoders()
    except Exception:
        return False


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    video_codec: str = ""
    audio_codec: str = ""
    width: int = 0
    height: int = 0

    @property
    def is_video(self) -> bool:
        return self.has_video and self.video_codec not in ("mjpeg", "png", "bmp")

    def describe(self) -> str:
        bits = [fmt_time(self.duration)]
        if self.is_video:
            bits.append(f"video {self.width}×{self.height} ({self.video_codec})")
        if self.has_audio:
            bits.append(f"audio ({self.audio_codec})")
        else:
            bits.append("tanpa suara")
        return " · ".join(bits)


def probe(path: str) -> MediaInfo:
    res = run([
        ffprobe_path(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ], timeout=60)
    if res.returncode != 0:
        msg = res.stderr.strip().splitlines()[-1:] or ["file tidak bisa dibaca"]
        raise ValueError(f"File tidak bisa dibaca: {msg[0]}")
    data = json.loads(res.stdout or "{}")
    info = MediaInfo(path=path)
    try:
        info.duration = float(data.get("format", {}).get("duration") or 0)
    except ValueError:
        info.duration = 0.0
    for st in data.get("streams", []):
        kind = st.get("codec_type")
        if kind == "video" and not info.has_video:
            if st.get("disposition", {}).get("attached_pic"):
                continue  # gambar sampul MP3, bukan video
            info.has_video = True
            info.video_codec = st.get("codec_name", "")
            info.width = int(st.get("width") or 0)
            info.height = int(st.get("height") or 0)
        elif kind == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = st.get("codec_name", "")
        if not info.duration and st.get("duration"):
            try:
                info.duration = float(st["duration"])
            except ValueError:
                pass
    return info


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_time(text: str) -> float | None:
    """'90', '1:30', '0:01:30', '1:30.5' → detik. Kosong → None."""
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"Format waktu salah: {text}")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    if total < 0:
        raise ValueError(f"Waktu tidak boleh negatif: {text}")
    return total
