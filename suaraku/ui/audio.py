"""Bantuan perangkat audio Qt: pilih format, hitung volume, nama format FFmpeg."""

from __future__ import annotations

import array

from PySide6.QtMultimedia import QAudioFormat

from ..core.live import PcmFormat

SF = QAudioFormat.SampleFormat

# format Qt → (nama FFmpeg, byte per sampel, kode array, nilai penuh)
FMT_INFO = {
    SF.Int16: ("s16le", 2, "h", 32768.0),
    SF.Int32: ("s32le", 4, "i", 2147483648.0),
    SF.Float: ("f32le", 4, "f", 1.0),
}


def pick_format(device, channels=(1, 2), rates=(48000, 44100)) -> QAudioFormat:
    for rate in rates:
        for ch in channels:
            for sf in (SF.Int16, SF.Float, SF.Int32):
                f = QAudioFormat()
                f.setSampleRate(rate)
                f.setChannelCount(ch)
                f.setSampleFormat(sf)
                if device.isFormatSupported(f):
                    return f
    f = device.preferredFormat()
    if f.sampleFormat() not in FMT_INFO:
        f.setSampleFormat(SF.Int16)
    return f


def to_pcm(fmt: QAudioFormat) -> PcmFormat:
    name = FMT_INFO.get(fmt.sampleFormat(), FMT_INFO[SF.Int16])[0]
    return PcmFormat(fmt.sampleRate(), fmt.channelCount(), name)


def peak(data: bytes, sample_format) -> float:
    info = FMT_INFO.get(sample_format)
    if not info or not data:
        return 0.0
    _name, width, code, full = info
    usable = len(data) - (len(data) % width)
    if usable <= 0:
        return 0.0
    arr = array.array(code)
    arr.frombytes(data[:usable])
    if not len(arr):
        return 0.0
    return min(1.0, max(abs(max(arr)), abs(min(arr))) / full)


def is_virtual_cable(description: str) -> bool:
    d = description.lower()
    return "cable" in d and ("vb-audio" in d or "virtual" in d or d.startswith("cable"))
