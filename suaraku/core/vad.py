"""Deteksi suara untuk rekam otomatis: mulai saat bicara, berhenti saat diam.

Tidak bergantung pada Qt supaya bisa diuji. Pemakaian:

    gate = VoiceGate(bytes_per_sec=96000, threshold_db=-38, stop_after=2.5)
    for chunk, peak in mic_chunks:
        data, event = gate.feed(chunk, peak)
        file.write(data)
        if event == "stop":
            file.truncate(gate.keep_bytes())
"""

from __future__ import annotations

import math
from collections import deque


def db_to_peak(db: float) -> float:
    return 10 ** (db / 20.0)


def peak_to_db(peak: float) -> float:
    return 20 * math.log10(max(peak, 1e-6))


class VoiceGate:
    WAITING = "menunggu"
    RECORDING = "merekam"
    DONE = "selesai"

    def __init__(self, bytes_per_sec: int, threshold_db: float = -38.0,
                 stop_after: float = 2.5, preroll: float = 0.4, start_hold: float = 0.06,
                 tail: float = 0.35, frame_bytes: int = 2):
        self.bps = max(1, int(bytes_per_sec))
        self.frame = max(1, int(frame_bytes))
        self.threshold = db_to_peak(threshold_db)
        self.stop_after = stop_after
        self.preroll = preroll
        self.start_hold = start_hold
        self.tail = tail
        self.state = self.WAITING
        self._pre: deque[bytes] = deque()
        self._pre_len = 0
        self._voiced_run = 0.0      # detik suara berturut-turut saat menunggu
        self._silence = 0.0         # detik hening berturut-turut saat merekam
        self.written = 0            # byte yang sudah dikeluarkan untuk ditulis
        self.last_voice_end = 0     # posisi byte akhir suara terakhir

    def _secs(self, n: int) -> float:
        return n / float(self.bps)

    def feed(self, data: bytes, peak: float) -> tuple[bytes, str | None]:
        """Kembalikan (byte yang harus ditulis ke file, event 'start'/'stop'/None)."""
        if self.state == self.DONE or not data:
            return b"", None
        dur = self._secs(len(data))
        voiced = peak >= self.threshold

        if self.state == self.WAITING:
            self._pre.append(data)
            self._pre_len += len(data)
            limit = int(self.preroll * self.bps)
            while self._pre and self._pre_len - len(self._pre[0]) >= limit:
                self._pre_len -= len(self._pre.popleft())
            self._voiced_run = self._voiced_run + dur if voiced else 0.0
            if self._voiced_run >= self.start_hold:
                out = b"".join(self._pre)
                self._pre.clear()
                self._pre_len = 0
                self.state = self.RECORDING
                self.written = len(out)
                self.last_voice_end = self.written
                return out, "start"
            return b"", None

        # RECORDING
        self.written += len(data)
        if voiced:
            self._silence = 0.0
            self.last_voice_end = self.written
        else:
            self._silence += dur
            if self._silence >= self.stop_after:
                self.state = self.DONE
                return data, "stop"
        return data, None

    def keep_bytes(self) -> int:
        """Panjang file yang disimpan: sampai suara terakhir + sedikit ekor."""
        keep = self.last_voice_end + int(self.tail * self.bps)
        keep = min(keep, self.written)
        return keep - (keep % self.frame)
