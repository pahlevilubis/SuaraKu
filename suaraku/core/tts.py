"""Suara AI pembaca naskah (text-to-speech) memakai layanan suara Microsoft Edge.

Butuh internet. Aksesnya lewat pustaka edge-tts (tidak resmi), jadi kalau suatu
saat berhenti jalan, biasanya cukup build ulang SuaraKu supaya edge-tts-nya
ikut versi terbaru.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class Voice:
    short_name: str
    label: str


# Pilihan siap pakai. Daftar lengkap bisa dimuat dari internet lewat list_voices().
CURATED: list[Voice] = [
    Voice("id-ID-ArdiNeural", "Ardi — Indonesia, pria"),
    Voice("id-ID-GadisNeural", "Gadis — Indonesia, wanita"),
    Voice("en-US-AndrewMultilingualNeural", "Andrew — multibahasa, pria hangat (bisa baca B. Indonesia)"),
    Voice("en-US-BrianMultilingualNeural", "Brian — multibahasa, pria santai (bisa baca B. Indonesia)"),
    Voice("en-US-AvaMultilingualNeural", "Ava — multibahasa, wanita (bisa baca B. Indonesia)"),
    Voice("en-US-EmmaMultilingualNeural", "Emma — multibahasa, wanita ceria (bisa baca B. Indonesia)"),
    Voice("en-US-GuyNeural", "Guy — Inggris AS, pria gaya penyiar"),
    Voice("en-US-ChristopherNeural", "Christopher — Inggris AS, pria tegas"),
    Voice("en-US-AndrewNeural", "Andrew — Inggris AS, pria hangat"),
    Voice("en-US-JennyNeural", "Jenny — Inggris AS, wanita"),
    Voice("en-US-AriaNeural", "Aria — Inggris AS, wanita percaya diri"),
    Voice("en-GB-RyanNeural", "Ryan — Inggris British, pria"),
    Voice("en-GB-SoniaNeural", "Sonia — Inggris British, wanita"),
]


class TtsError(RuntimeError):
    pass


def _import():
    try:
        import edge_tts  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise TtsError(f"Modul suara AI tidak tersedia: {exc}") from exc
    return edge_tts


def _friendly(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if any(k in low for k in ("connect", "dns", "timeout", "getaddrinfo", "network", "ssl")):
        return ("Tidak bisa terhubung ke layanan suara AI. Pastikan laptop tersambung ke "
                "internet, lalu coba lagi.\n\nDetail: " + text)
    if "403" in low or "401" in low:
        return ("Layanan suara AI menolak permintaan (kemungkinan aturannya berubah). "
                "Build ulang SuaraKu supaya pakai versi terbaru.\n\nDetail: " + text)
    if "noaudioreceived" in low or "no audio" in low:
        return ("Tidak ada suara yang diterima. Cek nama suara yang dipilih, atau "
                "coba suara lain.\n\nDetail: " + text)
    return text


def synthesize(text: str, voice: str, output_mp3: str, *,
               rate_pct: int = 0, pitch_hz: int = 0, volume_pct: int = 0) -> str:
    text = (text or "").strip()
    if not text:
        raise TtsError("Naskahnya masih kosong.")
    edge_tts = _import()

    async def _go():
        com = edge_tts.Communicate(text, voice, rate=f"{rate_pct:+d}%",
                                   volume=f"{volume_pct:+d}%", pitch=f"{pitch_hz:+d}Hz")
        await com.save(output_mp3)

    try:
        asyncio.run(_go())
    except Exception as exc:
        raise TtsError(_friendly(exc)) from exc
    return output_mp3


def list_voices(locales: tuple[str, ...] = ("id-", "en-", "ms-")) -> list[Voice]:
    edge_tts = _import()

    async def _go():
        return await edge_tts.list_voices()

    try:
        raw = asyncio.run(_go())
    except Exception as exc:
        raise TtsError(_friendly(exc)) from exc
    out = []
    for v in raw:
        name = v.get("ShortName", "")
        if not name.startswith(locales):
            continue
        gender = {"Male": "pria", "Female": "wanita"}.get(v.get("Gender", ""), v.get("Gender", ""))
        person = name.split("-")[-1].replace("Neural", "")
        out.append(Voice(name, f"{person} — {v.get('Locale', '')}, {gender}"))
    out.sort(key=lambda x: (not x.short_name.startswith("id-"), x.short_name))
    return out
