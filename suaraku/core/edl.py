"""Mesin potong: daftar bagian yang dibuang, gelombang suara, deteksi hening,
dan perintah FFmpeg untuk menyimpan hasil potongan (audio maupun video).

Semua waktu dalam detik pada timeline file asli.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import jobs, tools

Range = tuple[float, float]
MIN_LEN = 0.01          # potongan lebih pendek dari ini diabaikan
JOINT_FADE = 0.012      # fade super pendek di setiap sambungan supaya tidak "klik"


# ---------------------------------------------------------------- rentang

def merge_ranges(ranges: list[Range], duration: float | None = None) -> list[Range]:
    out: list[list[float]] = []
    for a, b in sorted((min(a, b), max(a, b)) for a, b in ranges):
        a = max(0.0, a)
        if duration is not None:
            b = min(duration, b)
        if b - a < MIN_LEN:
            continue
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def subtract(ranges: list[Range], a: float, b: float) -> list[Range]:
    """Buang [a, b] dari daftar rentang (dipakai untuk 'kembalikan')."""
    a, b = min(a, b), max(a, b)
    out: list[Range] = []
    for x, y in ranges:
        if y <= a or x >= b:
            out.append((x, y))
            continue
        if x < a:
            out.append((x, a))
        if y > b:
            out.append((b, y))
    return merge_ranges(out)


def remove(removed: list[Range], a: float, b: float, duration: float) -> list[Range]:
    return merge_ranges([*removed, (a, b)], duration)


def keep_only(removed: list[Range], a: float, b: float, duration: float) -> list[Range]:
    a, b = min(a, b), max(a, b)
    return merge_ranges([*removed, (0.0, a), (b, duration)], duration)


def keep_segments(duration: float, removed: list[Range], start: float = 0.0,
                  end: float | None = None) -> list[Range]:
    end = duration if end is None else min(end, duration)
    cur = max(0.0, start)
    out: list[Range] = []
    for a, b in merge_ranges(removed, duration):
        if b <= cur:
            continue
        if a >= end:
            break
        if a > cur:
            out.append((cur, min(a, end)))
        cur = max(cur, b)
    if end - cur >= MIN_LEN:
        out.append((cur, end))
    return [(a, b) for a, b in out if b - a >= MIN_LEN]


def total(segments: list[Range]) -> float:
    return sum(b - a for a, b in segments)


def pieces(duration: float, splits: list[float]) -> list[Range]:
    pts = sorted({round(s, 3) for s in splits if MIN_LEN < s < duration - MIN_LEN})
    edges = [0.0, *pts, duration]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def is_removed(removed: list[Range], t: float, tol: float = 0.0) -> Range | None:
    for a, b in removed:
        if a - tol <= t < b - tol:
            return (a, b)
    return None


def words_cut(words: list[Range], i: int, j: int, duration: float) -> Range:
    """Rentang waktu untuk membuang kata ke-i s/d ke-j.

    Ikut membuang separuh jeda sebelum & sesudahnya, supaya tidak tersisa jeda dobel.
    """
    a, b = words[i][0], words[j][1]
    prev_end = words[i - 1][1] if i > 0 else 0.0
    next_start = words[j + 1][0] if j + 1 < len(words) else duration
    lo = (prev_end + a) / 2 if a > prev_end else a
    hi = (b + next_start) / 2 if next_start > b else b
    lo = max(lo, prev_end + 0.02 if i > 0 else 0.0)
    hi = min(hi, next_start - 0.02 if j + 1 < len(words) else duration)
    return (max(0.0, min(lo, a)), min(duration, max(hi, b)))


def words_removed_mask(words: list[Range], removed: list[Range]) -> list[bool]:
    return [is_removed(removed, (a + b) / 2) is not None for a, b in words]


# ---------------------------------------------------------------- analisis

def load_peaks(path: str, per_second: int = 100):
    """Puncak suara (0..1) tiap 1/per_second detik → numpy float32 array."""
    import numpy as np

    rate = 8000
    res = subprocess.run(
        [tools.ffmpeg_path(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", path,
         "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "pipe:1"],
        capture_output=True, creationflags=tools.NO_WINDOW,
    )
    if res.returncode != 0:
        return np.zeros(0, dtype=np.float32)
    x = np.frombuffer(res.stdout, dtype=np.int16)
    step = rate // per_second
    n = len(x) // step
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    x = np.abs(x[: n * step].astype(np.int32)).reshape(n, step).max(axis=1)
    return (x / 32768.0).astype(np.float32)


_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silence(path: str, threshold_db: float = -40.0, min_len: float = 0.8,
                   duration: float | None = None) -> list[Range]:
    res = subprocess.run(
        [tools.ffmpeg_path(), "-nostdin", "-hide_banner", "-i", path, "-vn",
         "-af", f"silencedetect=n={threshold_db:.1f}dB:d={min_len:.3f}", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=tools.NO_WINDOW,
    )
    out: list[Range] = []
    start = None
    for line in res.stderr.splitlines():
        m = _SIL_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None and duration:
        out.append((start, duration))
    return out


def silence_cuts(silences: list[Range], duration: float, keep_gap: float = 0.3) -> list[Range]:
    """Hening yang dibuang: sisakan jeda keep_gap (dibagi dua di kiri/kanan)."""
    half = keep_gap / 2
    cuts = []
    for a, b in silences:
        lo = a if a <= 0.001 else a + half          # hening di awal file: mulai dari 0
        hi = b if b >= duration - 0.001 else b - half  # hening di akhir file: sampai habis
        if hi - lo >= 0.05:
            cuts.append((lo, hi))
    return merge_ranges(cuts, duration)


# ---------------------------------------------------------------- simpan

def _num(x: float) -> str:
    return f"{x:.4f}"


def cut_job(src: str, dst: str, segments: list[Range], *, info: tools.MediaInfo,
            fmt: str, bitrate: int = 192, fade_in: float = 0.0, fade_out: float = 0.0,
            label: str = "Simpan potongan") -> jobs.Job:
    """fmt: 'video' (MP4) atau salah satu format audio (mp3, m4a, wav, ogg, flac)."""
    segments = [(a, b) for a, b in segments if b - a >= MIN_LEN]
    if not segments:
        raise ValueError("Tidak ada bagian yang tersisa untuk disimpan.")
    video = fmt == "video"
    if video and not info.is_video:
        raise ValueError("File ini bukan video.")
    has_audio = info.has_audio
    if not video and not has_audio:
        raise ValueError("File ini tidak punya suara.")

    length = total(segments)
    chains: list[str] = []
    labels: list[str] = []
    for i, (a, b) in enumerate(segments):
        seg_len = b - a
        if video:
            chains.append(f"[0:v:0]trim=start={_num(a)}:end={_num(b)},setpts=PTS-STARTPTS[v{i}]")
            labels.append(f"[v{i}]")
        if has_audio:
            j = min(JOINT_FADE, seg_len / 4)
            chains.append(
                f"[0:a:0]atrim=start={_num(a)}:end={_num(b)},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={_num(j)},afade=t=out:st={_num(seg_len - j)}:d={_num(j)}[a{i}]")
            labels.append(f"[a{i}]")
    n = len(segments)
    v_out, a_out = "[vc]", "[ac]"
    outs = (v_out if video else "") + (a_out if has_audio else "")
    chains.append("".join(labels)
                  + f"concat=n={n}:v={1 if video else 0}:a={1 if has_audio else 0}{outs}")

    fi = max(0.0, min(fade_in, length / 2))
    fo = max(0.0, min(fade_out, length / 2))
    if has_audio:
        post = []
        if fi > 0:
            post.append(f"afade=t=in:st=0:d={_num(fi)}")
        if fo > 0:
            post.append(f"afade=t=out:st={_num(length - fo)}:d={_num(fo)}")
        post.append("alimiter=limit=0.98:level=false")
        chains.append(f"{a_out}{','.join(post)}[aout]")
    if video:
        post = []
        if fi > 0:
            post.append(f"fade=t=in:st=0:d={_num(fi)}")
        if fo > 0:
            post.append(f"fade=t=out:st={_num(length - fo)}:d={_num(fo)}")
        post.append("format=yuv420p")
        chains.append(f"{v_out}{','.join(post)}[vout]")

    graph = ";".join(chains)
    fg_args, script = tools.filter_graph_args(graph)
    args = [*jobs.COMMON, "-i", src, *fg_args]
    if video:
        args += ["-map", "[vout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
        if has_audio:
            args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
        args += ["-movflags", "+faststart"]
    else:
        args += ["-map", "[aout]", *jobs._audio_codec_args(fmt, bitrate)]
    args.append(dst)
    job = jobs.Job(label, args, dst, length)
    if script:
        job.cleanup.append(script)
    return job


def output_ext(fmt: str) -> str:
    return ".mp4" if fmt == "video" else jobs.AUDIO_FORMATS[fmt][1]


def save_jobs(src: str, removed: list[Range], splits: list[float], *, info: tools.MediaInfo,
              fmt: str, bitrate: int = 192, fade_in: float = 0.0, fade_out: float = 0.0,
              per_piece: bool = False, folder: str | None = None) -> list[jobs.Job]:
    dur = info.duration
    ext = output_ext(fmt)
    if not per_piece:
        segs = keep_segments(dur, removed)
        dst = jobs.output_name(src, " - potong", ext, folder)
        return [cut_job(src, dst, segs, info=info, fmt=fmt, bitrate=bitrate,
                        fade_in=fade_in, fade_out=fade_out)]
    out = []
    n = 0
    for a, b in pieces(dur, splits):
        segs = keep_segments(dur, removed, a, b)
        if total(segs) < 0.05:
            continue
        n += 1
        dst = jobs.output_name(src, f" - bagian {n}", ext, folder)
        out.append(cut_job(src, dst, segs, info=info, fmt=fmt, bitrate=bitrate,
                           fade_in=fade_in, fade_out=fade_out, label=f"Simpan bagian {n}"))
    if not out:
        raise ValueError("Tidak ada bagian yang tersisa untuk disimpan.")
    return out


def default_format(info: tools.MediaInfo) -> str:
    if info.is_video:
        return "video"
    ext = Path(info.path).suffix.lower()
    return {".mp3": "mp3", ".m4a": "m4a", ".aac": "m4a", ".wav": "wav", ".ogg": "ogg",
            ".flac": "flac"}.get(ext, "mp3")
