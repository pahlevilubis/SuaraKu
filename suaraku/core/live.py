"""Mode Live: olah suara mic secara real-time lewat FFmpeg.

Alur: mic (PCM) → feed() → FFmpeg (filter efek) → on_output(PCM) → mic virtual
(VB-CABLE) yang dipilih sebagai mikrofon di WhatsApp Desktop / Teams.

Tidak bergantung pada Qt, jadi bisa diuji dengan data buatan.
"""

from __future__ import annotations

import queue
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

from . import effects, tools
from .effects import VoiceSettings

# Preset yang cocok dipakai saat telepon (tanpa efek berat).
LIVE_PRESETS = ["asli", "bersih", "penyiar", "macho", "husky", "podcast", "telepon",
                "robot", "raksasa", "tupai", "aula"]


@dataclass
class PcmFormat:
    sample_rate: int = 48000
    channels: int = 1
    sample_fmt: str = "s16le"   # s16le / s32le / f32le

    @property
    def bytes_per_frame(self) -> int:
        return {"s16le": 2, "s32le": 4, "f32le": 4}.get(self.sample_fmt, 2) * self.channels

    @property
    def bytes_per_sec(self) -> int:
        return self.bytes_per_frame * self.sample_rate


def live_args(settings: VoiceSettings, inp: PcmFormat, out: PcmFormat,
              use_rubberband: bool | None = None) -> list[str]:
    if use_rubberband is None:
        use_rubberband = tools.has_filter("rubberband")
    chain = effects.build_voice_filters(settings, use_rubberband, realtime=True)
    chain.append(f"aresample={out.sample_rate}")
    return [
        "-hide_banner", "-loglevel", "error", "-nostats",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-probesize", "32", "-analyzeduration", "0",
        "-f", inp.sample_fmt, "-ar", str(inp.sample_rate), "-ac", str(inp.channels),
        "-i", "pipe:0",
        "-af", ",".join(chain),
        "-f", out.sample_fmt, "-ar", str(out.sample_rate), "-ac", str(out.channels),
        "-flush_packets", "1", "-avioflags", "direct",
        "pipe:1",
    ]


class LiveProcessor:
    """Satu proses FFmpeg yang terus jalan selama Mode Live aktif."""

    def __init__(self, settings: VoiceSettings, inp: PcmFormat, out: PcmFormat,
                 on_output: Callable[[bytes], None],
                 on_error: Callable[[str], None] | None = None):
        self.settings = settings
        self.inp = inp
        self.out = out
        self.on_output = on_output
        self.on_error = on_error
        self.proc: subprocess.Popen | None = None
        self._q: queue.Queue[bytes | None] = queue.Queue(maxsize=200)
        self._threads: list[threading.Thread] = []
        self._stopping = False
        self._closing = False
        self._err: list[str] = []

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        cmd = [tools.ffmpeg_path(), *live_args(self.settings, self.inp, self.out)]
        self._stopping = False
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, creationflags=tools.NO_WINDOW,
        )
        for fn in (self._writer, self._reader, self._stderr):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self._threads.append(t)

    def feed(self, data: bytes) -> None:
        if not data or self._stopping:
            return
        try:
            self._q.put_nowait(data)
        except queue.Full:
            # FFmpeg tertinggal: buang data lama supaya jeda tidak makin panjang.
            try:
                self._q.get_nowait()
                self._q.put_nowait(data)
            except (queue.Empty, queue.Full):
                pass

    def _writer(self) -> None:
        proc = self.proc
        while proc and not self._stopping:
            data = self._q.get()
            if data is None:
                break
            try:
                proc.stdin.write(data)
            except (BrokenPipeError, OSError, ValueError):
                break
        try:
            if proc and proc.stdin:
                proc.stdin.close()
        except OSError:
            pass

    def _reader(self) -> None:
        proc = self.proc
        if not proc or not proc.stdout:
            return
        frame = self.out.bytes_per_frame
        carry = b""
        while True:
            try:
                chunk = proc.stdout.read(4096)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            chunk = carry + chunk
            usable = len(chunk) - (len(chunk) % frame)
            carry = chunk[usable:]
            if usable:
                self.on_output(chunk[:usable])
        if not (self._stopping or self._closing) and self.on_error:
            detail = "\n".join(self._err[-5:]) or "FFmpeg berhenti."
            self.on_error(f"Pemrosesan suara berhenti:\n{detail}")

    def _stderr(self) -> None:
        proc = self.proc
        if not proc or not proc.stderr:
            return
        for line in iter(proc.stderr.readline, b""):
            self._err.append(line.decode("utf-8", "replace").rstrip())
            del self._err[:-20]

    def stop(self, timeout: float = 3.0) -> None:
        self._stopping = True
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass
        proc = self.proc
        if proc:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout)
        for t in self._threads:
            t.join(timeout=1)
        self._threads.clear()
        self.proc = None

    def drain(self, timeout: float = 5.0) -> None:
        """Untuk tes: tutup input lalu tunggu semua output keluar."""
        self._closing = True
        self._q.put(None)
        if self.proc:
            self.proc.wait(timeout=timeout)
        for t in self._threads:
            t.join(timeout=timeout)
        self._stopping = True
        self.proc = None
