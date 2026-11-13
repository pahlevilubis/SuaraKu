"""Penyusun perintah FFmpeg untuk setiap fitur, plus penjalan (runner) dengan progress."""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import effects, tools
from .effects import VoiceSettings

COMMON = ["-hide_banner", "-y", "-nostdin", "-loglevel", "error",
          "-progress", "pipe:1", "-nostats"]

# Codec video yang aman disalin langsung ke MP4 tanpa encode ulang (cepat).
MP4_COPY_OK = {"h264", "hevc", "mpeg4", "av1"}

AUDIO_FORMATS = {
    # key: (label, ekstensi)
    "mp3": ("MP3 (paling umum)", ".mp3"),
    "m4a": ("M4A / AAC (iPhone, WA)", ".m4a"),
    "wav": ("WAV (tanpa kompresi)", ".wav"),
    "ogg": ("OGG", ".ogg"),
    "flac": ("FLAC (lossless)", ".flac"),
}
VIDEO_FORMATS = {
    "mp4": ("MP4 (kualitas bagus)", ".mp4"),
    "mp4_small": ("MP4 kecil (720p, buat WA/LinkedIn)", ".mp4"),
    "mp4_mute": ("MP4 tanpa suara", ".mp4"),
}
ALL_FORMATS = {**AUDIO_FORMATS, **VIDEO_FORMATS}


@dataclass
class Job:
    label: str
    args: list[str]          # argumen setelah nama program ffmpeg
    output: str
    duration: float          # perkiraan durasi hasil (detik) untuk progress bar
    cleanup: list[str] = field(default_factory=list)
    cwd: str | None = None   # folder kerja FFmpeg (dipakai subtitle)

    def command(self) -> list[str]:
        return [tools.ffmpeg_path(), *self.args]


class JobError(RuntimeError):
    pass


class JobCancelled(RuntimeError):
    pass


# ---------------------------------------------------------------- helpers

def _audio_codec_args(fmt: str, bitrate: int) -> list[str]:
    if fmt == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", f"{bitrate}k"]
    if fmt == "m4a":
        return ["-c:a", "aac", "-b:a", f"{bitrate}k", "-movflags", "+faststart"]
    if fmt == "wav":
        return ["-c:a", "pcm_s16le"]
    if fmt == "ogg":
        codec = "libvorbis" if tools.has_encoder("libvorbis") else "libopus"
        return ["-c:a", codec, "-b:a", f"{bitrate}k"]
    if fmt == "flac":
        return ["-c:a", "flac"]
    raise ValueError(f"Format audio tidak dikenal: {fmt}")


def audio_args_for_path(path: str, bitrate: int = 192) -> list[str]:
    ext = Path(path).suffix.lower()
    fmt = {".mp3": "mp3", ".m4a": "m4a", ".aac": "m4a", ".wav": "wav",
           ".ogg": "ogg", ".flac": "flac"}.get(ext, "mp3")
    return _audio_codec_args(fmt, bitrate)


def _h264(crf: int = 21) -> list[str]:
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-pix_fmt", "yuv420p"]


def _scale_720() -> list[str]:
    # Tinggi maksimal 720 (tidak membesarkan video kecil), lebar genap.
    return ["-vf", "scale=-2:'min(720,ih)'"]


def _video_args(info: tools.MediaInfo, scale_720: bool) -> list[str]:
    if scale_720:
        return [*_scale_720(), *_h264(23)]
    if info.video_codec in MP4_COPY_OK:
        return ["-c:v", "copy"]
    return _h264(21)


def _trim(info: tools.MediaInfo, start: float | None, end: float | None) -> tuple[list[str], list[str], float]:
    total = info.duration
    s = max(0.0, start or 0.0)
    e = end if end and end > 0 else total
    if total and e > total:
        e = total
    if e and e <= s:
        raise ValueError("Waktu selesai harus lebih besar dari waktu mulai.")
    pre = ["-ss", f"{s:.3f}"] if s > 0 else []
    post = ["-t", f"{e - s:.3f}"] if (end and end > 0) else []
    return pre, post, max(0.0, (e or 0) - s)


def output_name(src: str, suffix: str, ext: str, folder: str | None = None) -> str:
    p = Path(src)
    base = Path(folder) if folder else p.parent
    cand = base / f"{p.stem}{suffix}{ext}"
    n = 2
    while cand.exists():
        cand = base / f"{p.stem}{suffix} ({n}){ext}"
        n += 1
    return str(cand)


# ---------------------------------------------------------------- konversi

def convert(src: str, dst: str, fmt: str, *, bitrate: int = 192,
            start: float | None = None, end: float | None = None,
            info: tools.MediaInfo | None = None) -> Job:
    info = info or tools.probe(src)
    pre, post, dur = _trim(info, start, end)
    args = [*COMMON, *pre, "-i", src, *post]
    if fmt in AUDIO_FORMATS:
        if not info.has_audio:
            raise ValueError(f"{Path(src).name} tidak punya suara untuk diambil.")
        args += ["-map", "0:a:0", "-vn", *_audio_codec_args(fmt, bitrate)]
    elif fmt in VIDEO_FORMATS:
        if not info.is_video:
            raise ValueError(f"{Path(src).name} bukan file video.")
        args += ["-map", "0:v:0"]
        if fmt == "mp4":
            args += _h264(21)
        elif fmt == "mp4_small":
            args += [*_scale_720(), *_h264(27)]
        elif fmt == "mp4_mute":
            args += _h264(21)
        if fmt == "mp4_mute" or not info.has_audio:
            args += ["-an"]
        else:
            br = 128 if fmt == "mp4_small" else 192
            args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", f"{br}k"]
        args += ["-movflags", "+faststart"]
    else:
        raise ValueError(f"Format tidak dikenal: {fmt}")
    args.append(dst)
    return Job(f"Konversi {Path(src).name}", args, dst, dur)


def mute_video(src: str, dst: str, info: tools.MediaInfo | None = None) -> Job:
    info = info or tools.probe(src)
    if not info.is_video:
        raise ValueError("File ini bukan video.")
    args = [*COMMON, "-i", src, "-map", "0:v:0", *_video_args(info, False),
            "-an", "-movflags", "+faststart", dst]
    return Job("Hapus suara video", args, dst, info.duration)


# ---------------------------------------------------------------- efek suara

def voice_effect(src: str, dst: str, settings: VoiceSettings, *,
                 keep_video: bool = False, preview_seconds: float | None = None,
                 preview_start: float = 0.0, info: tools.MediaInfo | None = None,
                 use_rubberband: bool | None = None) -> Job:
    info = info or tools.probe(src)
    if not info.has_audio:
        raise ValueError("File ini tidak punya suara.")
    if use_rubberband is None:
        use_rubberband = tools.has_filter("rubberband")
    af = effects.build_voice_filter(settings, use_rubberband)
    args = [*COMMON]
    dur = info.duration
    if preview_seconds:
        start = min(preview_start, max(0.0, info.duration - 1))
        if start > 0:
            args += ["-ss", f"{start:.3f}"]
        dur = min(preview_seconds, max(0.1, info.duration - start)) if info.duration else preview_seconds
    args += ["-i", src]
    if preview_seconds:
        args += ["-t", f"{dur:.3f}"]
    if keep_video and info.is_video:
        args += ["-map", "0:v:0", *_video_args(info, False),
                 "-map", "0:a:0", "-af", af, "-c:a", "aac", "-b:a", "192k",
                 "-movflags", "+faststart"]
    else:
        args += ["-map", "0:a:0", "-vn", "-af", af, *audio_args_for_path(dst)]
    args.append(dst)
    return Job("Efek suara", args, dst, dur)


# ---------------------------------------------------------------- gabung video + suara

@dataclass
class MergeOptions:
    video: str
    output: str
    narration: str | None = None
    narration_offset: float = 0.0
    narration_volume: float = 1.0
    narration_effect: VoiceSettings | None = None
    music: str | None = None
    music_volume: float = 0.25
    music_loop: bool = True
    music_fade: bool = True
    duck_music: bool = True        # kecilkan musik otomatis saat ada narasi
    keep_original: bool = False    # False = suara asli video dihapus
    original_volume: float = 1.0
    scale_720: bool = False
    subtitles_ass: str | None = None   # path subs.ass (folder berisi fonts/)


def merge(o: MergeOptions, info: tools.MediaInfo | None = None,
          use_rubberband: bool | None = None) -> Job:
    info = info or tools.probe(o.video)
    if not info.is_video:
        raise ValueError("File utama harus video.")
    if use_rubberband is None:
        use_rubberband = tools.has_filter("rubberband")
    dur = info.duration
    if dur <= 0:
        raise ValueError("Durasi video tidak terbaca.")

    fmt = f"aformat=sample_rates={effects.SAMPLE_RATE}:channel_layouts=stereo"
    args = [*COMMON, "-i", o.video]
    chains: list[str] = []
    mix_inputs: list[str] = []
    idx = 1

    if o.keep_original and info.has_audio:
        chains.append(f"[0:a:0]{fmt},volume={o.original_volume:.3f}[orig]")
        mix_inputs.append("[orig]")

    narr_sc = None
    if o.narration:
        args += ["-i", o.narration]
        parts = [f"aresample={effects.SAMPLE_RATE}"]
        if o.narration_effect is not None:
            parts += effects.build_voice_filters(o.narration_effect, use_rubberband)
        parts += [fmt, f"volume={o.narration_volume:.3f}"]
        delay_ms = int(round(max(0.0, o.narration_offset) * 1000))
        if delay_ms > 0:
            parts.append(f"adelay=delays={delay_ms}:all=1")
        if o.music and o.duck_music:
            parts.append("asplit=2[narr][narrsc]")
            chains.append(f"[{idx}:a:0]" + ",".join(parts))
            narr_sc = "[narrsc]"
        else:
            chains.append(f"[{idx}:a:0]" + ",".join(parts) + "[narr]")
        mix_inputs.append("[narr]")
        idx += 1

    if o.music:
        if o.music_loop:
            args += ["-stream_loop", "-1"]
        args += ["-i", o.music]
        parts = [f"aresample={effects.SAMPLE_RATE}", fmt, f"volume={o.music_volume:.3f}"]
        if o.music_fade and dur > 4:
            parts.append(f"afade=t=out:st={dur - 3:.3f}:d=3")
        if narr_sc:
            chains.append(f"[{idx}:a:0]" + ",".join(parts) + "[mus0]")
            chains.append(f"[mus0]{narr_sc}sidechaincompress=threshold=0.02:ratio=8"
                          ":attack=20:release=500:makeup=1[mus]")
        else:
            chains.append(f"[{idx}:a:0]" + ",".join(parts) + "[mus]")
        mix_inputs.append("[mus]")
        idx += 1

    subs = o.subtitles_ass
    if not mix_inputs and not subs:
        out = o.output
        job = mute_video(o.video, out, info)
        job.label = "Gabung video"
        return job

    if len(mix_inputs) == 1:
        chains.append(f"{mix_inputs[0]}apad,alimiter=limit=0.97:level=false[aout]")
    elif mix_inputs:
        chains.append("".join(mix_inputs)
                      + f"amix=inputs={len(mix_inputs)}:duration=longest"
                        ":dropout_transition=0:normalize=0,apad,"
                        "alimiter=limit=0.97:level=false[aout]")

    vmap = ["-map", "0:v:0", *_video_args(info, o.scale_720)]
    if subs:
        vf = []
        if o.scale_720:
            vf.append("scale=-2:'min(720,ih)'")
        # path relatif terhadap cwd (folder subs) supaya tidak repot escape path Windows
        vf += [f"subtitles=filename={Path(subs).name}:fontsdir=fonts", "format=yuv420p"]
        chains.append("[0:v:0]" + ",".join(vf) + "[vout]")
        vmap = ["-map", "[vout]", *_h264(21)]
    amap = ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"] if mix_inputs else ["-an"]

    args += ["-filter_complex", ";".join(chains), *vmap, *amap,
             "-t", f"{dur:.3f}", "-movflags", "+faststart", o.output]
    job = Job("Gabung video + suara", args, o.output, dur)
    if subs:
        job.cwd = str(Path(subs).parent)
    return job


# ---------------------------------------------------------------- rekaman mentah → WAV

# Rapatkan jeda hening panjang (> 0,8 detik) supaya narasi lebih padat.
COMPACT_SILENCE = ("silenceremove=stop_periods=-1:stop_duration=0.8"
                   ":stop_threshold=-45dB:stop_silence=0.4")


def raw_to_wav(raw: str, dst: str, sample_rate: int, channels: int, sample_fmt: str,
               compact_silence: bool = False) -> Job:
    """sample_fmt: s16le / s32le / f32le / u8."""
    args = [*COMMON, "-f", sample_fmt, "-ar", str(sample_rate), "-ac", str(channels),
            "-i", raw]
    if compact_silence:
        args += ["-af", COMPACT_SILENCE]
    args += ["-c:a", "pcm_s16le", dst]
    size = os.path.getsize(raw) if os.path.exists(raw) else 0
    bps = {"s16le": 2, "s32le": 4, "f32le": 4, "u8": 1}.get(sample_fmt, 2)
    dur = size / float(bps * channels * sample_rate) if size else 0.0
    return Job("Simpan rekaman", args, dst, dur)


# ---------------------------------------------------------------- runner

def parse_progress(line: str) -> float | None:
    line = line.strip()
    for key in ("out_time_us=", "out_time_ms="):
        if line.startswith(key):
            try:
                return int(line[len(key):]) / 1_000_000.0
            except ValueError:
                return None
    return None


class Runner:
    """Menjalankan satu Job FFmpeg. Bisa dibatalkan dari thread lain."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def run(self, job: Job, on_progress: Callable[[float], None] | None = None) -> str:
        if self._cancel.is_set():
            raise JobCancelled()
        Path(job.output).parent.mkdir(parents=True, exist_ok=True)
        self._proc = subprocess.Popen(
            job.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            creationflags=tools.NO_WINDOW, cwd=job.cwd,
        )
        err_lines: list[str] = []

        def _read_err() -> None:
            assert self._proc and self._proc.stderr
            for ln in self._proc.stderr:
                err_lines.append(ln.rstrip())
                del err_lines[:-40]

        t = threading.Thread(target=_read_err, daemon=True)
        t.start()
        assert self._proc.stdout
        for line in self._proc.stdout:
            sec = parse_progress(line)
            if sec is not None and on_progress and job.duration > 0:
                on_progress(max(0.0, min(1.0, sec / job.duration)))
        code = self._proc.wait()
        t.join(timeout=2)
        if self._cancel.is_set():
            _safe_remove(job.output)
            raise JobCancelled()
        if code != 0:
            _safe_remove(job.output)
            detail = "\n".join(err_lines[-6:]) or f"kode keluar {code}"
            raise JobError(f"{job.label} gagal:\n{detail}")
        if on_progress:
            on_progress(1.0)
        for f in job.cleanup:
            _safe_remove(f)
        return job.output


def _safe_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
