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
            return f"{len(QMediaDevices.audioInputs())} mic terdeteksi (di server build wajar 0)"

        check("FFmpeg ditemukan", _ff)
        check("Filter audio", _filters)
        check("Encoder", _encoders)
        check("Tampilan (semua menu)", _ui)
        check("Media uji", _make_media)
        check("Semua preset efek", _effects)
        check("Gabung video", _merge)
        check("Pemutar audio/video", _player)
        check("Modul suara AI", _edge)
        check("Mikrofon", _mics)
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
