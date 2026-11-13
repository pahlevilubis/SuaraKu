"""Simpan rahasia (API key) terenkripsi.

Di Windows memakai DPAPI (CryptProtectData): hanya akun Windows yang sama di laptop
yang sama yang bisa membukanya. Di sistem lain hanya disamarkan (base64).
"""

from __future__ import annotations

import base64
import sys

_ENTROPY = b"SuaraKu-claude-key"


def _blob_cls():
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):  # noqa: N801
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    return DATA_BLOB


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes

    blob_cls = _blob_cls()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    def mk(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return blob_cls(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    inp, _keep1 = mk(data)
    ent, _keep2 = mk(_ENTROPY)
    out = blob_cls()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if protect:
        ok = fn(ctypes.byref(inp), "SuaraKu", ctypes.byref(ent), None, None, 0x1, ctypes.byref(out))
    else:
        ok = fn(ctypes.byref(inp), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI gagal")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(text: str) -> str:
    if not text:
        return ""
    raw = text.encode("utf-8")
    if sys.platform == "win32":
        try:
            return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode("ascii")
        except Exception:
            pass
    return "plain:" + base64.b64encode(raw).decode("ascii")


def unprotect(value: str) -> str:
    if not value:
        return ""
    try:
        kind, _, b64 = value.partition(":")
        data = base64.b64decode(b64)
        if kind == "dpapi":
            return _dpapi(data, False).decode("utf-8")
        if kind == "plain":
            return data.decode("utf-8")
    except Exception:
        return ""
    return ""
