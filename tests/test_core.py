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

import os  # noqa: E402

from suaraku.core import edl, effects, jobs, live, stt, subs, tools, vad  # noqa: E402

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


def test_vad_gate():
    import array
    sr, bps = 48000, 96000          # mono s16
    chunk = 960                     # 10 ms
    def tone(amp):
        return array.array("h", [int(amp * 32767)] * (chunk // 2)).tobytes()
    g = vad.VoiceGate(bps, threshold_db=-30, stop_after=1.0, preroll=0.3, tail=0.2)
    out, events = bytearray(), []
    plan = [0.001] * 100 + [0.5] * 150 + [0.001] * 40 + [0.5] * 50 + [0.001] * 200
    for amp in plan:
        data, ev = g.feed(tone(amp), amp)
        out += data
        if ev:
            events.append((ev, len(out)))
    assert [e for e, _ in events] == ["start", "stop"], events
    keep = g.keep_bytes()
    secs = keep / bps
    # 0,3 s pre-roll + 1,5 s suara + 0,4 s jeda + 0,5 s suara + 0,2 s ekor ≈ 2,9 s
    assert 2.7 < secs < 3.1, secs
    assert keep % 2 == 0


def test_compact_silence():
    gap = _make("gap.wav", "-f", "lavfi", "-i", "sine=f=300:d=1", "-f", "lavfi", "-i",
                "anullsrc=r=48000:cl=mono:d=3", "-f", "lavfi", "-i", "sine=f=300:d=1",
                "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1")
    raw = TMP / "gap.raw"
    subprocess.run([tools.ffmpeg_path(), "-y", "-loglevel", "error", "-i", gap, "-f", "s16le",
                    "-ar", "48000", "-ac", "1", str(raw)], check=True)
    i = run(jobs.raw_to_wav(str(raw), str(TMP / "gap_compact.wav"), 48000, 1, "s16le",
                            compact_silence=True))
    assert 2.2 < i.duration < 4.0, i.duration


def test_live_processor_all_presets():
    import array
    fmt_in = live.PcmFormat(48000, 1, "s16le")
    fmt_out = live.PcmFormat(48000, 2, "s16le")      # mic virtual biasanya stereo
    one_sec = array.array("h", [int(8000 * ((i // 60) % 2 * 2 - 1)) for i in range(48000)]).tobytes()
    for key in live.LIVE_PRESETS:
        got = []
        errs = []
        p = live.LiveProcessor(effects.preset_settings(key), fmt_in, fmt_out, got.append, errs.append)
        p.start()
        for k in range(0, len(one_sec), 1920):
            p.feed(one_sec[k:k + 1920])
        p.drain()
        n = sum(len(b) for b in got)
        assert not errs, (key, errs)
        assert all(len(b) % fmt_out.bytes_per_frame == 0 for b in got), key
        secs = n / fmt_out.bytes_per_sec
        assert 0.85 < secs < 1.3, (key, secs)


def test_srt_format():
    t = stt.Transcript(segments=[stt.Segment(0, 1.5, " Halo semua."),
                                 stt.Segment(3.7, 3661.25, "Apa kabar?")])
    srt = t.to_srt()
    assert "1\n00:00:00,000 --> 00:00:01,500\nHalo semua." in srt
    assert "01:01:01,250" in srt
    assert t.paragraphs() == "Halo semua.\n\nApa kabar?"


def test_stt_real_speech():
    """Jalan di GitHub: suara contoh dibuat dengan suara AI, lalu diketik ulang oleh Whisper."""
    speech = os.environ.get("SUARAKU_TEST_SPEECH")
    if not speech or not os.path.exists(speech) or not stt.model_path("base"):
        print("     (lewati: tidak ada contoh suara / model)")
        return
    seen = []
    tw = stt.transcribe(speech, "base", "id", words=True, verbatim=True)
    ws = tw.words
    print("     kata:", len(ws), [(round(w.start, 2), w.text) for w in ws[:4]])
    assert len(ws) >= 8 and all(w.end >= w.start for w in ws)
    assert all(ws[i].start <= ws[i + 1].start for i in range(len(ws) - 1))
    caps = subs.build_captions(tw)
    assert caps and all(len(c.text) <= 60 for c in caps), [c.text for c in caps]
    t = stt.transcribe(speech, "base", "id", on_progress=seen.append)
    text = t.text.lower()
    print("     transkrip:", t.text)
    assert seen and seen[-1] == 1.0
    assert any(w in text for w in ("selamat pagi", "aplikasi", "perkenalkan")), text
    assert t.to_srt().startswith("1\n00:00:")


def test_edl_ranges():
    r = edl.merge_ranges([(5, 6), (1, 2), (1.5, 3), (9, 20)], duration=10)
    assert r == [(1, 3), (5, 6), (9, 10)], r
    assert edl.keep_segments(10, r) == [(0, 1), (3, 5), (6, 9)]
    assert edl.subtract(r, 2, 5.5) == [(1, 2), (5.5, 6), (9, 10)]
    assert edl.keep_only([], 2, 4, 10) == [(0, 2), (4, 10)]
    assert edl.pieces(10, [7, 3, 3.0004, 0, 10]) == [(0, 3), (3, 7), (7, 10)]
    assert edl.keep_segments(10, [(1, 3), (5, 6)], 2, 8) == [(3, 5), (6, 8)]
    assert edl.is_removed([(1, 3)], 2) == (1, 3) and edl.is_removed([(1, 3)], 3) is None
    cuts = edl.silence_cuts([(0, 1.0), (4, 6), (9, 10)], 10, keep_gap=0.4)
    assert cuts == [(0, 0.8), (4.2, 5.8), (9.2, 10)], cuts   # sisakan 0,2 dtk sebelum suara


def test_cut_audio_and_video():
    segs = [(0.5, 1.5), (2.0, 2.5), (4.0, 5.5)]       # total 3,0 detik
    a_info = tools.probe(FX["video"])
    for fmt in ("mp3", "wav", "m4a"):
        out = str(TMP / f"cut.{fmt}")
        i = run(edl.cut_job(FX["video"], out, segs, info=a_info, fmt=fmt, fade_in=0.3, fade_out=0.3))
        assert not i.is_video and 2.85 < i.duration < 3.2, (fmt, i.duration)
    out = str(TMP / "cut_video.mp4")
    i = run(edl.cut_job(FX["video"], out, segs, info=a_info, fmt="video", fade_in=0.5))
    assert i.is_video and i.has_audio and 2.85 < i.duration < 3.2, i.duration
    # video tanpa suara
    v_info = tools.probe(FX["video_vp9"])
    i = run(edl.cut_job(FX["video_vp9"], str(TMP / "cut_vp9.mp4"), [(0, 1), (2, 3)],
                        info=v_info, fmt="video"))
    assert i.is_video and not i.has_audio and 1.85 < i.duration < 2.2, i.duration


def test_cut_many_segments_uses_script():
    long = _make("long.mp3", "-f", "lavfi", "-i", "sine=f=330:d=120", "-c:a", "libmp3lame")
    info = tools.probe(long)
    removed = [(k + 0.5, k + 0.9) for k in range(0, 119)]      # 119 potongan → graph panjang
    job = edl.save_jobs(long, removed, [], info=info, fmt="mp3")[0]
    assert any(a in ("-/filter_complex", "-filter_complex_script") for a in job.args), "harus pakai file"
    i = run(job)
    expect = 120 - 0.4 * 119
    assert abs(i.duration - expect) < 0.4, (i.duration, expect)


def test_split_pieces():
    info = tools.probe(FX["video"])
    js = edl.save_jobs(FX["video"], [(1.0, 1.5)], [2.0, 4.0], info=info, fmt="wav",
                       per_piece=True, folder=str(TMP))
    durs = [run(j).duration for j in js]
    assert len(durs) == 3 and abs(durs[0] - 1.5) < 0.1 and abs(durs[1] - 2) < 0.1, durs
    assert all(" - bagian " in j.output for j in js)


def test_detect_silence_and_peaks():
    gap = _make("gap2.wav", "-f", "lavfi", "-i", "sine=f=300:d=1", "-f", "lavfi", "-i",
                "anullsrc=r=48000:cl=mono:d=2", "-f", "lavfi", "-i", "sine=f=300:d=1",
                "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1")
    sil = edl.detect_silence(gap, -40, 0.5, duration=4)
    assert len(sil) == 1 and abs(sil[0][0] - 1) < 0.1 and abs(sil[0][1] - 3) < 0.1, sil
    peaks = edl.load_peaks(gap, 100)
    assert 395 <= len(peaks) <= 405, len(peaks)
    assert peaks[50] > 0.1 and peaks[200] < 0.01   # sine lavfi = amplitudo 0,125


def _frame_mean_diff(a: str, b: str, t: float) -> float:
    import numpy as np
    def grab(p):
        r = subprocess.run([tools.ffmpeg_path(), "-loglevel", "error", "-ss", str(t), "-i", p,
                            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "320x180", "-"],
                           capture_output=True, check=True)
        return np.frombuffer(r.stdout, dtype=np.uint8).astype(np.int16)
    return float(np.abs(grab(a) - grab(b)).mean())


def test_subtitles_burned_in():
    words = [stt.Word(0.2 + i * 0.4, 0.5 + i * 0.4, w) for i, w in
             enumerate("Halo semua. Ini SuaraKu buatan saya sendiri lho.".split())]
    t = stt.Transcript(segments=[stt.Segment(0.2, 3.5, "x", words)])
    caps = subs.build_captions(t, offset=0.5)
    assert caps[0].text == "Halo semua." and abs(caps[0].start - 0.7) < 1e-6, caps
    assert all(c.end > c.start for c in caps)
    assert all(caps[i].end <= caps[i + 1].start for i in range(len(caps) - 1))
    doc = subs.ass_document(caps, 640, 360, "kotak", "besar")
    assert "PlayResX: 640" in doc and "Dialogue:" in doc
    srt = subs.to_srt(caps)
    assert srt.startswith("1\n00:00:00,700 --> ")
    for style in subs.STYLES:
        ass = subs.prepare_dir(caps, 640, 360, style, "sedang")
        assert Path(ass).exists() and (Path(ass).parent / "fonts" / subs.FONT_FILE).exists()
        o = jobs.MergeOptions(FX["video"], str(TMP / f"subs_{style}.mp4"),
                              narration=FX["narration"], subtitles_ass=ass)
        i = run(jobs.merge(o))
        assert i.is_video and i.has_audio
        diff = _frame_mean_diff(FX["video"], o.output, 1.0)
        assert diff > 0.8, (style, "subtitle tidak terlihat", diff)
    # tanpa suara sama sekali tapi pakai subtitle
    o = jobs.MergeOptions(FX["video"], str(TMP / "subs_mute.mp4"), subtitles_ass=ass, scale_720=True)
    i = run(jobs.merge(o))
    assert i.is_video and not i.has_audio


def test_words_cut():
    w = [(0.0, 0.5), (1.0, 1.4), (1.6, 2.0), (3.0, 3.5)]
    a, b = edl.words_cut(w, 1, 2, 4.0)
    assert 0.5 < a <= 1.0 and 2.0 <= b < 3.0, (a, b)
    assert abs(a - 0.75) < 1e-6 and abs(b - 2.5) < 1e-6
    a, b = edl.words_cut(w, 0, 0, 4.0)
    assert a == 0.0 and 0.5 <= b <= 1.0
    assert edl.words_removed_mask(w, [(0.9, 2.1)]) == [False, True, True, False]


def test_secret_roundtrip():
    from suaraku.core import secret
    for k in ("sk-ant-api03-abc_DEF-123", "kunci dengan spasi ✓"):
        enc = secret.protect(k)
        assert enc and k not in enc
        assert secret.unprotect(enc) == k
        if sys.platform == "win32":
            assert enc.startswith("dpapi:"), enc[:10]
    assert secret.unprotect("") == "" and secret.unprotect("rusak:xx") == ""


def _mock_claude(mode: dict):
    import json as _json
    import threading as _th
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            raw = _json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _err(self, code, etype, msg):
            self._send(code, {"type": "error", "error": {"type": etype, "message": msg},
                              "request_id": "req_test"})

        def do_GET(self):
            seen.append(("GET", self.path, dict(self.headers), None))
            if self.headers.get("x-api-key") != "good":
                return self._err(401, "authentication_error", "invalid x-api-key")
            self._send(200, {"data": [{"id": "claude-haiku-5-5"}, {"id": "claude-sonnet-5-5"}]})

        def do_POST(self):
            body = _json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append(("POST", self.path, dict(self.headers), body))
            if self.headers.get("x-api-key") != "good":
                return self._err(401, "authentication_error", "invalid x-api-key")
            if mode.get("overload_once"):
                mode["overload_once"] = False
                return self._err(529, "overloaded_error", "Overloaded")
            if mode.get("no_credit"):
                return self._err(400, "invalid_request_error",
                                 "Your credit balance is too low to access the Anthropic API.")
            if mode.get("reject_schema") and "output_config" in body:
                return self._err(400, "invalid_request_error",
                                 "output_config: Extra inputs are not permitted")
            sysmsg = body.get("system", "")
            if "penulis naskah" in sysmsg:
                out = {"judul": "SuaraKu", "naskah": "Halo semua.\n\nIni app buatan saya."}
            elif "editor audio" in sysmsg:
                out = {"hapus": [{"dari": 3, "sampai": 1, "alasan": "diulang"},
                                 {"dari": 0, "sampai": 0, "alasan": "pengisi"},
                                 {"dari": 99, "sampai": 120, "alasan": "x"}]}
            elif "sound engineer" in sysmsg:
                out = {"pitch": -30, "formant_shift": False, "bass": 6.5, "mud_cut": 3,
                       "presence": 2, "treble": -2, "highpass": 80, "lowpass": 500,
                       "denoise": 140, "deess": True, "compress": True, "reverb": 12,
                       "robot": False, "normalize": True, "volume": 1.0,
                       "penjelasan": "Lebih berat dan hangat."}
            else:
                out = {"linkedin": "Hook!\n\nIsi.", "wa_status": "Cek ini!",
                       "hashtag": ["SuaraKu", "#AI", "buatan sendiri"], "judul": "SuaraKu"}
            text = _json.dumps(out)
            if mode.get("fenced"):
                text = "Berikut JSON-nya:\n```json\n" + text + "\n```"
            self._send(200, {"id": "msg_test", "type": "message", "role": "assistant",
                             "content": [{"type": "thinking", "thinking": "hmm", "signature": "x"},
                                         {"type": "text", "text": text}],
                             "stop_reason": "end_turn"})

    srv = HTTPServer(("127.0.0.1", 0), H)
    _th.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def test_claude_client():
    from suaraku.core import claude
    mode: dict = {}
    srv, seen = _mock_claude(mode)
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    old = claude.API_URL, claude.MODELS_URL
    claude.API_URL, claude.MODELS_URL = base + "/messages", base + "/models"
    try:
        assert claude.test_key("good") == 2
        try:
            claude.test_key("bad")
            raise AssertionError("harus gagal")
        except claude.ClaudeError as e:
            assert e.status == 401 and "API key" in str(e)
        r = claude.write_script("good", "claude-haiku-5-5", "- app SuaraKu", 30, "Santai", "Bahasa Indonesia")
        assert r["judul"] == "SuaraKu" and "Halo" in r["naskah"]
        m, path, headers, body = seen[-1]
        h = {k.lower(): v for k, v in headers.items()}
        assert path == "/v1/messages" and h["anthropic-version"] == claude.API_VERSION
        assert h["x-api-key"] == "good" and h["content-type"] == "application/json"
        assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] > 0
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
        assert "temperature" not in body and body["messages"][0]["role"] == "user"
        cuts = claude.clean_take("good", "", ["eh", "saya", "saya", "mau", "cerita"])
        assert cuts == [(0, 0, "pengisi"), (1, 3, "diulang")], cuts
        vs, why = claude.voice_settings("good", "", "berat dan hangat")
        assert vs.pitch == -12 and vs.mud_cut == 0 and vs.denoise == 100 and vs.lowpass == 3000
        assert isinstance(vs.highpass, int) and vs.deess is True and why
        c = claude.captions("good", "", "Halo semua", "pamer app", "Bahasa Indonesia")
        assert c["hashtag"] == ["#SuaraKu", "#AI", "#buatansendiri"], c["hashtag"]
        # model/versi yang menolak output_config → otomatis minta JSON lewat teks
        mode.update(reject_schema=True, fenced=True)
        n0 = len(seen)
        r = claude.write_script("good", "", "- x", 15, "Santai", "English")
        assert r["judul"] == "SuaraKu" and "output_config" not in seen[-1][3]
        assert len(seen) - n0 == 2 and "JSON" in seen[-1][3]["messages"][0]["content"]
        mode.update(reject_schema=False, fenced=False, overload_once=True)
        assert claude.captions("good", "", "x", "", "English")["judul"] == "SuaraKu"
        mode["no_credit"] = True
        try:
            claude.captions("good", "", "x", "", "English")
            raise AssertionError("harus gagal")
        except claude.ClaudeError as e:
            assert "Kredit" in str(e), str(e)
    finally:
        claude.API_URL, claude.MODELS_URL = old
        srv.shutdown()


if __name__ == "__main__":
    setup_module()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"Semua {len(tests)} tes lulus. File hasil di {TMP}")
