"""Tes mesin SuaraKu memakai FFmpeg sungguhan.

Jalankan: python -m pytest tests -q   (atau: python tests/test_core.py)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from suaraku.core import effects, jobs, tools  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="suaraku-test-"))


def _make(name: str, *args: str) -> str:
    out = str(TMP / name)
    subprocess.run([tools.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", *args, out],
                   check=True)
    return out


def fixtures() -> dict[str, str]:
    return {
        # video 6 detik + suara asli
        "video": _make("video.mp4", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=6",
                       "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"),
        # video tanpa suara, codec non-h264 (harus di-encode ulang)
        "video_vp9": _make("video_vp9.webm", "-f", "lavfi", "-i",
                           "testsrc=size=320x240:rate=25:duration=4", "-c:v", "libvpx-vp9", "-b:v", "200k"),
        # "narasi": suara mirip ucapan (noise yang dimodulasi) 3 detik
        "narration": _make("narasi.wav", "-f", "lavfi", "-i",
                           "anoisesrc=d=3:c=pink:a=0.3,volume='0.5+0.5*sin(2*PI*3*t)':eval=frame"),
        # musik 2.5 detik (harus di-loop sampai video habis)
        "music": _make("musik.mp3", "-f", "lavfi", "-i", "sine=frequency=220:duration=2.5",
                       "-c:a", "libmp3lame"),
    }


FX: dict[str, str] = {}


def setup_module(_=None):
    FX.update(fixtures())


def run(job: jobs.Job) -> tools.MediaInfo:
    seen = []
    jobs.Runner().run(job, seen.append)
    assert Path(job.output).exists() and Path(job.output).stat().st_size > 0, job.output
    assert seen and seen[-1] == 1.0
    return tools.probe(job.output)


def test_parse_time():
    assert tools.parse_time("90") == 90
    assert tools.parse_time("1:30") == 90
    assert tools.parse_time("0:01:30,5") == 90.5
    assert tools.parse_time("") is None


def test_probe():
    i = tools.probe(FX["video"])
    assert i.is_video and i.has_audio and 5.5 < i.duration < 6.5
    m = tools.probe(FX["music"])
    assert not m.is_video and m.has_audio


def test_convert_audio_formats():
    for fmt, (_, ext) in jobs.AUDIO_FORMATS.items():
        out = str(TMP / f"conv_{fmt}{ext}")
        i = run(jobs.convert(FX["video"], out, fmt))
        assert i.has_audio and not i.is_video, fmt
        assert 5.5 < i.duration < 6.6, (fmt, i.duration)


def test_convert_trim():
    out = str(TMP / "trim.mp3")
    i = run(jobs.convert(FX["video"], out, "mp3", start=1, end=4))
    assert 2.8 < i.duration < 3.2, i.duration


def test_convert_video_formats():
    for fmt in jobs.VIDEO_FORMATS:
        out = str(TMP / f"conv_{fmt}.mp4")
        i = run(jobs.convert(FX["video"], out, fmt))
        assert i.is_video and i.video_codec == "h264"
        assert i.has_audio == (fmt != "mp4_mute"), fmt


def test_mute_video():
    i = run(jobs.mute_video(FX["video"], str(TMP / "mute.mp4")))
    assert i.is_video and not i.has_audio


def test_all_presets_both_pitch_engines():
    for rb in (True, False):
        for p in effects.PRESETS:
            out = str(TMP / f"fx_{p.key}_{int(rb)}.wav")
            i = run(jobs.voice_effect(FX["narration"], out, p.settings, use_rubberband=rb))
            assert i.has_audio and 2.7 < i.duration < 3.4, (p.key, rb, i.duration)


def test_effect_keeps_video_and_preview():
    s = effects.preset_settings("macho")
    i = run(jobs.voice_effect(FX["video"], str(TMP / "fx_video.mp4"), s, keep_video=True))
    assert i.is_video and i.has_audio
    i = run(jobs.voice_effect(FX["video"], str(TMP / "fx_prev.wav"), s,
                              preview_seconds=2, preview_start=1))
    assert 1.8 < i.duration < 2.3, i.duration


def test_extreme_manual_settings():
    s = effects.VoiceSettings(pitch=-12, bass=12, treble=12, presence=-12, reverb=100,
                              denoise=100, highpass=200, lowpass=5000, deess=True,
                              compress=True, robot=True, volume=2.0)
    for rb in (True, False):
        run(jobs.voice_effect(FX["narration"], str(TMP / f"fx_extreme_{int(rb)}.mp3"), s,
                              use_rubberband=rb))


def test_merge_variants():
    v = FX["video"]
    cases = {
        "narr_only": jobs.MergeOptions(v, "", narration=FX["narration"], narration_offset=1.5),
        "narr_fx": jobs.MergeOptions(v, "", narration=FX["narration"],
                                     narration_effect=effects.preset_settings("penyiar")),
        "narr_music_duck": jobs.MergeOptions(v, "", narration=FX["narration"], music=FX["music"]),
        "narr_music_noduck": jobs.MergeOptions(v, "", narration=FX["narration"], music=FX["music"],
                                               duck_music=False, music_loop=False),
        "music_keep_orig": jobs.MergeOptions(v, "", music=FX["music"], keep_original=True),
        "all_three_720": jobs.MergeOptions(v, "", narration=FX["narration"], music=FX["music"],
                                           keep_original=True, scale_720=True),
        "nothing": jobs.MergeOptions(v, ""),
    }
    for name, o in cases.items():
        o.output = str(TMP / f"merge_{name}.mp4")
        i = run(jobs.merge(o))
        assert i.is_video, name
        assert i.has_audio == (name != "nothing"), name
        assert 5.5 < i.duration < 6.6, (name, i.duration)
    # video VP9 tanpa suara → harus di-encode ulang ke H.264
    o = jobs.MergeOptions(FX["video_vp9"], str(TMP / "merge_vp9.mp4"), narration=FX["narration"])
    i = run(jobs.merge(o))
    assert i.video_codec == "h264" and i.has_audio


def test_raw_to_wav():
    raw = TMP / "rec.raw"
    raw.write_bytes(b"\x00\x00" * 48000 * 2)  # 2 detik mono s16
    i = run(jobs.raw_to_wav(str(raw), str(TMP / "rec.wav"), 48000, 1, "s16le"))
    assert 1.9 < i.duration < 2.1


def test_cancel():
    import threading
    o = jobs.MergeOptions(FX["video"], str(TMP / "cancel.mp4"), narration=FX["narration"],
                          scale_720=True)
    r = jobs.Runner()
    threading.Timer(0.05, r.cancel).start()
    try:
        r.run(jobs.merge(o))
    except jobs.JobCancelled:
        assert not Path(o.output).exists()


if __name__ == "__main__":
    setup_module()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"Semua {len(tests)} tes lulus. File hasil di {TMP}")
