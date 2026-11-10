"""Efek suara: preset (Penyiar, Macho, Husky, dll) dan penyusun filter FFmpeg."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

SAMPLE_RATE = 48000


@dataclass
class VoiceSettings:
    pitch: float = 0.0       # semitone, -12..+12 (negatif = lebih berat)
    formant_shift: bool = False  # True = karakter kartun (tupai/raksasa)
    bass: float = 0.0        # dB, -12..+12
    mud_cut: float = 0.0     # dB pengurangan di ~300 Hz (suara "bindeng")
    presence: float = 0.0    # dB di ~3 kHz (kejelasan suara)
    treble: float = 0.0      # dB, -12..+12
    highpass: int = 0        # Hz, 0 = mati (buang dengung rendah)
    lowpass: int = 0         # Hz, 0 = mati
    denoise: int = 0         # 0..100 pembersih noise
    deess: bool = False      # redam desis huruf "s"
    compress: bool = False   # padatkan volume (suara lebih "tebal")
    reverb: int = 0          # 0..100 gema
    robot: bool = False
    normalize: bool = True   # samakan volume (standar media sosial)
    volume: float = 1.0      # pengali volume akhir

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VoiceSettings":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    hint: str
    settings: VoiceSettings


_CLEAN = dict(highpass=80, denoise=45, deess=True, compress=True, normalize=True)

PRESETS: list[Preset] = [
    Preset("asli", "Asli", "Tanpa efek, apa adanya.",
           VoiceSettings(normalize=False)),
    Preset("bersih", "Bersih", "Buang noise kipas/AC dan samakan volume. Suara tetap natural.",
           VoiceSettings(highpass=80, denoise=50, normalize=True)),
    Preset("penyiar", "Penyiar Radio", "Hangat, jelas, padat — kayak rekaman studio.",
           VoiceSettings(**_CLEAN, bass=4, mud_cut=-3, presence=3, treble=1)),
    Preset("macho", "Macho", "Lebih berat dan nge-bass, tetap natural.",
           VoiceSettings(**_CLEAN, pitch=-3, bass=7, mud_cut=-2, presence=2, treble=-1, reverb=8)),
    Preset("husky", "Husky / Sexy", "Sedikit lebih rendah, hangat, lembut, ada sedikit gema.",
           VoiceSettings(**_CLEAN, pitch=-1.5, bass=5, mud_cut=-1, presence=1, treble=-3, reverb=18)),
    Preset("podcast", "Podcast Dekat", "Intim dan dekat, kayak ngomong di depan mic bagus.",
           VoiceSettings(**_CLEAN, bass=6, mud_cut=-4, presence=2, treble=2)),
    Preset("aula", "Gema Aula", "Seperti bicara di ruangan besar.",
           VoiceSettings(highpass=80, denoise=40, bass=2, presence=2, compress=True, reverb=60)),
    Preset("telepon", "Telepon / HT", "Suara sempit kayak lewat telepon atau radio HT.",
           VoiceSettings(highpass=350, lowpass=3400, presence=4, compress=True)),
    Preset("robot", "Robot", "Suara robot datar.",
           VoiceSettings(highpass=80, denoise=40, robot=True, compress=True)),
    Preset("raksasa", "Raksasa", "Berat sekali, kayak monster film.",
           VoiceSettings(pitch=-7, formant_shift=True, bass=5, denoise=40, reverb=30, compress=True)),
    Preset("tupai", "Tupai (lucu)", "Cempreng dan lucu.",
           VoiceSettings(pitch=7, formant_shift=True, denoise=40, compress=True)),
]

PRESETS_BY_KEY = {p.key: p for p in PRESETS}


def preset_settings(key: str) -> VoiceSettings:
    return replace(PRESETS_BY_KEY[key].settings)


def _num(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".")


def pitch_filters(semitones: float, formant_shift: bool, use_rubberband: bool) -> list[str]:
    if abs(semitones) < 0.05:
        return []
    ratio = 2 ** (semitones / 12.0)
    if use_rubberband:
        formant = "shifted" if formant_shift else "preserved"
        return [f"rubberband=pitch={_num(ratio)}:formant={formant}:pitchq=quality"]
    # Cadangan tanpa rubberband: ubah sample rate lalu kembalikan tempo.
    return [
        f"asetrate={int(round(SAMPLE_RATE * ratio))}",
        f"aresample={SAMPLE_RATE}",
        f"atempo={_num(1 / ratio)}",
    ]


def reverb_filter(amount: int) -> str | None:
    if amount <= 0:
        return None
    k = min(amount, 100) / 100.0
    delays = [35, 67, 113, 171]
    decays = [0.45 * k, 0.32 * k, 0.22 * k, 0.15 * k]
    d = "|".join(str(x) for x in delays)
    g = "|".join(_num(x) for x in decays)
    out_gain = _num(1.0 / (1.0 + 0.6 * k))
    return f"aecho=1:{out_gain}:{d}:{g}"


ROBOT = ("afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)'"
         ":win_size=512:overlap=0.75")


def build_voice_filters(s: VoiceSettings, use_rubberband: bool, realtime: bool = False) -> list[str]:
    """Daftar filter audio FFmpeg (urutan penting) untuk pengaturan suara.

    realtime=True dipakai Mode Live: tanpa filter yang butuh "melihat ke depan"
    (loudnorm menahan suara ±3 detik), diganti kompresor ringan.
    """
    f: list[str] = [f"aresample={SAMPLE_RATE}"]
    if s.highpass > 0:
        f.append(f"highpass=f={int(s.highpass)}")
    if s.denoise > 0:
        nr = 6 + 0.24 * min(s.denoise, 100)
        f.append(f"afftdn=nr={_num(nr)}:nf=-45:tn=1")
    f += pitch_filters(s.pitch, s.formant_shift, use_rubberband)
    if s.robot:
        f.append(ROBOT)
    if abs(s.bass) >= 0.1:
        f.append(f"bass=g={_num(s.bass)}:f=110")
    if abs(s.mud_cut) >= 0.1:
        f.append(f"equalizer=f=300:t=q:w=1.2:g={_num(s.mud_cut)}")
    if abs(s.presence) >= 0.1:
        f.append(f"equalizer=f=3200:t=q:w=1:g={_num(s.presence)}")
    if abs(s.treble) >= 0.1:
        f.append(f"treble=g={_num(s.treble)}:f=7000")
    if s.lowpass > 0:
        f.append(f"lowpass=f={int(s.lowpass)}")
    if s.deess:
        f.append("deesser=i=0.6:m=0.5:f=0.5")
    if s.compress:
        f.append("acompressor=threshold=-22dB:ratio=4:attack=8:release=150:makeup=2")
    rv = reverb_filter(s.reverb)
    if rv:
        f.append(rv)
    if s.normalize and realtime:
        f.append("acompressor=threshold=-30dB:ratio=3:attack=5:release=200:makeup=4")
    elif s.normalize:
        f.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        f.append(f"aresample={SAMPLE_RATE}")
    if abs(s.volume - 1.0) > 0.01:
        f.append(f"volume={_num(s.volume)}")
    # Pengaman supaya tidak pecah (clipping) setelah bass/volume dinaikkan.
    f.append("alimiter=limit=0.97:level=false")
    return f


def build_voice_filter(s: VoiceSettings, use_rubberband: bool, realtime: bool = False) -> str:
    return ",".join(build_voice_filters(s, use_rubberband, realtime))
