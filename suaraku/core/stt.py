"""Suara jadi teks (offline) memakai Whisper lewat faster-whisper.

Model "base" ikut terpasang di installer (folder models\\whisper-base).
Model "small" lebih akurat untuk Bahasa Indonesia, diunduh sekali (±480 MB)
ke %LOCALAPPDATA%\\SuaraKu\\models.
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import tools

MODELS = {
    "base": ("Cepat (base) — sudah terpasang", "±145 MB"),
    "small": ("Akurat (small) — unduh sekali ±480 MB", "±480 MB"),
}
LANGUAGES = [("Otomatis", None), ("Bahasa Indonesia", "id"), ("English", "en")]
_REQUIRED = ("model.bin", "config.json")

_lock = threading.Lock()
_cache: dict[str, object] = {}


class SttError(RuntimeError):
    pass


class SttCancelled(RuntimeError):
    pass


def user_models_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    d = Path(base) / "SuaraKu" / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_model(d: Path) -> bool:
    return all((d / f).exists() for f in _REQUIRED)


def model_path(key: str) -> str | None:
    roots = [tools.app_dir() / "models", user_models_dir()]
    override = os.environ.get("SUARAKU_MODELS")
    if override:
        roots.insert(0, Path(override))
    for root in roots:
        d = root / f"whisper-{key}"
        if _is_model(d):
            return str(d)
    return None


def download_model(key: str) -> str:
    """Unduh model ke folder user (butuh internet). Kembalikan path-nya."""
    try:
        from faster_whisper.utils import download_model as _dl
    except Exception as exc:
        raise SttError(f"Modul suara jadi teks tidak tersedia: {exc}") from exc
    target = user_models_dir() / f"whisper-{key}"
    try:
        _dl(key, output_dir=str(target))
    except Exception as exc:
        raise SttError("Gagal mengunduh model. Pastikan tersambung ke internet, lalu coba lagi."
                       f"\n\nDetail: {exc}") from exc
    if not _is_model(target):
        raise SttError("Unduhan model tidak lengkap. Coba lagi.")
    return str(target)


def _load(key: str):
    path = model_path(key)
    if not path:
        raise SttError(f"Model '{key}' belum ada. Unduh dulu.")
    with _lock:
        model = _cache.get(path)
        if model is None:
            try:
                from faster_whisper import WhisperModel
            except Exception as exc:
                raise SttError(f"Modul suara jadi teks tidak tersedia: {exc}") from exc
            threads = max(1, (os.cpu_count() or 2) - 1)
            model = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=threads)
            _cache.clear()   # simpan satu model saja supaya RAM hemat
            _cache[path] = model
        return model


def load_audio(path: str):
    """Baca audio/video apa pun jadi array float32 mono 16 kHz (pakai FFmpeg)."""
    import numpy as np

    res = subprocess.run(
        [tools.ffmpeg_path(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", path,
         "-vn", "-f", "f32le", "-ac", "1", "-ar", "16000", "pipe:1"],
        capture_output=True, creationflags=tools.NO_WINDOW,
    )
    if res.returncode != 0:
        msg = res.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["?"]
        raise SttError(f"Audio tidak bisa dibaca: {msg[0]}")
    audio = np.frombuffer(res.stdout, dtype=np.float32)
    if audio.size == 0:
        raise SttError("File ini tidak punya suara.")
    return audio


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    language: str = ""
    duration: float = 0.0
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip()).strip()

    def paragraphs(self) -> str:
        """Teks dengan baris baru di jeda panjang (lebih enak dibaca)."""
        out, last_end = [], None
        for s in self.segments:
            t = s.text.strip()
            if not t:
                continue
            if last_end is not None and s.start - last_end > 1.5:
                out.append("\n\n")
            elif out:
                out.append(" ")
            out.append(t)
            last_end = s.end
        return "".join(out).strip()

    def to_srt(self) -> str:
        lines = []
        n = 0
        for s in self.segments:
            t = s.text.strip()
            if not t:
                continue
            n += 1
            lines += [str(n), f"{_srt_time(s.start)} --> {_srt_time(s.end)}", t, ""]
        return "\n".join(lines)


def _srt_time(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe(path: str, model_key: str = "base", language: str | None = None,
               on_progress: Callable[[float], None] | None = None,
               cancel: threading.Event | None = None) -> Transcript:
    audio = load_audio(path)
    model = _load(model_key)
    duration = audio.size / 16000.0
    try:
        segments, info = model.transcribe(
            audio, language=language, task="transcribe", beam_size=5,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
        )
        result = Transcript(language=getattr(info, "language", "") or "", duration=duration)
        for seg in segments:
            if cancel is not None and cancel.is_set():
                raise SttCancelled()
            result.segments.append(Segment(float(seg.start), float(seg.end), seg.text))
            if on_progress and duration > 0:
                on_progress(min(1.0, float(seg.end) / duration))
    except (SttCancelled, SttError):
        raise
    except Exception as exc:
        raise SttError(f"Gagal mengubah suara jadi teks: {exc}") from exc
    if on_progress:
        on_progress(1.0)
    return result


def save_outputs(src: str, t: Transcript, folder: str | None = None) -> tuple[str, str]:
    """Simpan <nama>.txt dan <nama>.srt di sebelah file asli."""
    p = Path(src)
    base = Path(folder) if folder else p.parent
    txt = base / f"{p.stem}.txt"
    srt = base / f"{p.stem}.srt"
    txt.write_text(t.paragraphs() + "\n", encoding="utf-8")
    srt.write_text(t.to_srt(), encoding="utf-8")
    return str(txt), str(srt)
