"""Titik masuk SuaraKu.

    python main.py                       → buka aplikasi
    python main.py --selftest hasil.txt  → cek mandiri (dipakai saat build di GitHub)
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

# Pada exe tanpa console, stdout/stderr bernilai None. Arahkan ke file log.
_LOG = Path(tempfile.gettempdir()) / "SuaraKu-log.txt"
if sys.stdout is None or sys.stderr is None:
    _fh = open(_LOG, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _fh
    sys.stderr = sys.stderr or _fh


def _excepthook(etype, value, tb):
    text = "".join(traceback.format_exception(etype, value, tb))
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("\n[%Y-%m-%d %H:%M:%S]\n") + text)
    except OSError:
        pass
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance():
            QMessageBox.critical(None, "SuaraKu error",
                                 f"Terjadi kesalahan tak terduga:\n\n{value}\n\n"
                                 f"Detail tersimpan di:\n{_LOG}")
    except Exception:
        pass


def _app_id() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PahleviLubis.SuaraKu")
        except Exception:
            pass


def run_app() -> int:
    from PySide6.QtWidgets import QApplication

    from suaraku import APP_NAME
    from suaraku.ui import theme
    from suaraku.ui.main_window import MainWindow

    _app_id()
    sys.excepthook = _excepthook
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    theme.apply(app)
    win = MainWindow()
    win.show()
    return app.exec()


def selftest(report: str) -> int:
    lines: list[str] = []
    ok = True

    def log(msg: str) -> None:
        lines.append(msg)

    def check(name: str, fn) -> None:
        nonlocal ok
        t0 = time.time()
        try:
            res = fn()
            log(f"[OK]   {name} ({time.time() - t0:.1f}s) {res or ''}")
        except Exception:
            ok = False
            log(f"[GAGAL] {name}\n{traceback.format_exc()}")

    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtMultimedia import QMediaDevices, QMediaPlayer
        from PySide6.QtWidgets import QApplication

        from suaraku import __version__
        from suaraku.core import effects, jobs, tools
        from suaraku.ui import theme

        log(f"SuaraKu {__version__} · Python {sys.version.split()[0]} · frozen={getattr(sys, 'frozen', False)}")
        app = QApplication([sys.argv[0]])
        theme.apply(app)
        tmp = Path(tempfile.mkdtemp(prefix="suaraku-selftest-"))

        def _ff():
            return f"{tools.ffmpeg_path()} | {tools.ffprobe_path()}"

        def _filters():
            need = ["rubberband", "afftdn", "deesser", "acompressor", "loudnorm",
                    "sidechaincompress", "afftfilt", "aecho", "alimiter"]
            missing = [f for f in need if not tools.has_filter(f)]
            if [m for m in missing if m != "rubberband"]:
                raise RuntimeError(f"filter hilang: {missing}")
            return "semua ada" if not missing else f"tanpa: {missing}"

        def _encoders():
            need = ["libmp3lame", "aac", "libx264", "flac", "pcm_s16le"]
            missing = [e for e in need if not tools.has_encoder(e)]
            if missing:
                raise RuntimeError(f"encoder hilang: {missing}")

        def _ui():
            from suaraku.ui.main_window import MainWindow
            w = MainWindow()
            n = w.stack.count()
            w.close()
            return f"{n} menu"

        src_wav = str(tmp / "sine.wav")
        src_vid = str(tmp / "video.mp4")

        def _make_media():
            tools.run([tools.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                       "sine=frequency=330:duration=3", src_wav])
            tools.run([tools.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                       "testsrc=size=320x240:rate=25:duration=3", "-c:v", "libx264",
                       "-pix_fmt", "yuv420p", src_vid])
            if not (os.path.exists(src_wav) and os.path.exists(src_vid)):
                raise RuntimeError("gagal membuat media uji")

        def _effects():
            for p in effects.PRESETS:
                jobs.Runner().run(jobs.voice_effect(src_wav, str(tmp / f"{p.key}.mp3"), p.settings))
            return f"{len(effects.PRESETS)} preset"

        def _merge():
            o = jobs.MergeOptions(src_vid, str(tmp / "merge.mp4"), narration=src_wav,
                                  music=src_wav, narration_effect=effects.preset_settings("macho"))
            jobs.Runner().run(jobs.merge(o))
            i = tools.probe(o.output)
            if not (i.is_video and i.has_audio):
                raise RuntimeError("hasil gabung tidak lengkap")

        def _player():
            p = QMediaPlayer()
            loop = QEventLoop()
            state: dict = {}

            def on_status(st):
                if st in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.InvalidMedia):
                    state["status"] = st
                    loop.quit()

            p.mediaStatusChanged.connect(on_status)
            p.errorOccurred.connect(lambda _e, s: (state.update(err=s), loop.quit()))
            p.setSource(QUrl.fromLocalFile(str(tmp / "macho.mp3")))
            QTimer.singleShot(20000, loop.quit)
            loop.exec()
            if state.get("status") != QMediaPlayer.MediaStatus.LoadedMedia:
                raise RuntimeError(f"pemutar tidak bisa memuat file: {state}")
            return f"durasi {p.duration()} ms"

        def _edge():
            import edge_tts
            from suaraku.core import tts  # noqa: F401
            return getattr(edge_tts, "__version__", "")

        def _mics():
            return (f"{len(QMediaDevices.audioInputs())} mic, "
                    f"{len(QMediaDevices.audioOutputs())} output (di server build wajar 0)")

        def _live():
            from suaraku.core import live
            fin = live.PcmFormat(48000, 1, "s16le")
            fout = live.PcmFormat(48000, 2, "f32le")
            for key in ("penyiar", "macho"):
                got: list = []
                p = live.LiveProcessor(effects.preset_settings(key), fin, fout, got.append)
                p.start()
                one = (b"\x00\x10\x00\xf0" * 240) * 100   # 1 detik gelombang kotak
                for k in range(0, len(one), 1920):
                    p.feed(one[k:k + 1920])
                p.drain()
                secs = sum(len(b) for b in got) / fout.bytes_per_sec
                if not 0.8 < secs < 1.4:
                    raise RuntimeError(f"{key}: keluaran {secs:.2f} detik")
            return "ok"

        def _stt():
            from suaraku.core import stt
            path = stt.model_path("base")
            if not path:
                raise RuntimeError("model whisper-base tidak ikut terpasang")
            speech = os.environ.get("SUARAKU_TEST_SPEECH")
            if speech and not os.path.exists(speech):
                speech = None
            t = stt.transcribe(speech or src_wav, "base", "id" if speech else None)
            if speech:
                low = t.text.lower()
                if not any(w in low for w in ("selamat pagi", "aplikasi", "perkenalkan")):
                    raise RuntimeError(f"transkrip meleset: {t.text!r}")
            return f"model {path} · teks: {t.text[:80]!r}"

        check("FFmpeg ditemukan", _ff)
        check("Filter audio", _filters)
        check("Encoder", _encoders)
        check("Tampilan (semua menu)", _ui)
        check("Media uji", _make_media)
        check("Semua preset efek", _effects)
        check("Gabung video", _merge)
        check("Pemutar audio/video", _player)
        def _wait(cond, timeout: float) -> bool:
            end = time.time() + timeout
            while time.time() < end:
                app.processEvents()
                if cond():
                    return True
                time.sleep(0.02)
            return cond()

        def _cut_ui():
            from suaraku.core import edl
            from suaraku.core import stt as _stt
            from suaraku.ui.page_cut import CutPage
            page = CutPage()
            page.resize(1000, 1400)
            page.show()
            page.set_file(src_wav)
            if not _wait(lambda: page.wave.duration > 0, 30):
                raise RuntimeError("gelombang suara tidak termuat")
            page.sel = (0.5, 1.0)
            page._delete_sel()
            page.wave.playhead = 2.0
            page._split_here()
            if page.removed != [(0.5, 1.0)] or page.splits != [2.0]:
                raise RuntimeError(f"edit salah: {page.removed} {page.splits}")
            page.wave.set_zoom(3, 1.5)
            if page.wave.grab().isNull():
                raise RuntimeError("gelombang tidak tergambar")
            words = [_stt.Word(0.1, 0.4, " eh"), _stt.Word(0.6, 1.2, " halo"),
                     _stt.Word(1.4, 2.0, " semua")]
            page._words_ready(_stt.Transcript(segments=[_stt.Segment(0, 2, "x", words)]))
            page._claude_done([(0, 0, "pengisi")])
            page.tview.grab()
            page._undo()
            jl = edl.save_jobs(src_wav, page.removed, page.splits, info=page.info, fmt="mp3",
                               per_piece=True, folder=str(tmp))
            for j in jl:
                jobs.Runner().run(j)
            page.shutdown()
            page.close()
            return f"{len(jl)} bagian tersimpan"

        def _merge_subs():
            speech = os.environ.get("SUARAKU_TEST_SPEECH")
            if not speech or not os.path.exists(speech):
                return "dilewati (tanpa contoh suara)"
            import subprocess as sp
            from suaraku.ui.page_merge import MergePage
            page = MergePage()
            page.set_video(src_vid)
            page.set_narration(speech)
            page.subs_on.setChecked(True)
            page.subs_style.setCurrentIndex(page.subs_style.findData("kotak"))
            outs: list = []
            page.task.finished.connect(outs.extend)
            page._go()
            if not _wait(lambda: bool(outs) or (page._prep is None and not page.task.busy
                                                and page.task.status.objectName() == "Err"), 240):
                raise RuntimeError("gabung + subtitle tidak selesai")
            if not outs:
                raise RuntimeError(f"gagal: {page.task.status.text()}")
            out = outs[-1]
            srt = Path(out).with_suffix(".srt")
            if not srt.exists() or "-->" not in srt.read_text(encoding="utf-8"):
                raise RuntimeError("file .srt tidak dibuat")

            def frame(p, t):
                r = sp.run([tools.ffmpeg_path(), "-loglevel", "error", "-ss", str(t), "-i", p,
                            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "160x120",
                            "-"], capture_output=True, creationflags=tools.NO_WINDOW)
                return r.stdout

            diffs = []
            for t in (0.8, 1.5, 2.2):
                a, b = frame(src_vid, t), frame(out, t)
                if a and b and len(a) == len(b):
                    diffs.append(sum(abs(x - y) for x, y in zip(a, b)) / len(a))
            if not diffs or max(diffs) < 0.8:
                raise RuntimeError(f"subtitle tidak terlihat di video (selisih {diffs})")
            return f"selisih piksel {max(diffs):.1f} · {srt.name}"

        def _claude_ui():
            from suaraku.core import secret
            from suaraku.ui.page_claude import ClaudePage
            p = ClaudePage()
            p._script_done({"judul": "Uji", "naskah": "Halo semua."})
            p._cap_done({"judul": "J", "linkedin": "L", "wa_status": "W", "hashtag": ["#a"]})
            enc = secret.protect("sk-ant-uji")
            if secret.unprotect(enc) != "sk-ant-uji":
                raise RuntimeError("penyimpanan API key gagal")
            return enc.split(":")[0]

        check("Modul suara AI", _edge)
        check("Menu Potong (Qt sungguhan)", _cut_ui)
        check("Gabung + subtitle otomatis", _merge_subs)
        check("Asisten Claude + kunci terenkripsi", _claude_ui)
        check("Perangkat audio", _mics)
        check("Mode Live (pemroses real-time)", _live)
        check("Suara → teks (Whisper)", _stt)
    except Exception:
        ok = False
        log("[GAGAL] persiapan\n" + traceback.format_exc())

    log("SELFTEST OK" if ok else "SELFTEST GAGAL")
    Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        report = sys.argv[i + 1] if len(sys.argv) > i + 1 else "selftest.txt"
        return selftest(report)
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
