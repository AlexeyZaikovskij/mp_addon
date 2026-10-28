"""Passwords the Toolkit keeps (remembered MultiProbe accounts, the account for scheduled jobs), encrypted
with Windows DPAPI.

Machine scope: any program on this computer can decrypt them, but no other computer can, so a copy of
data/addon.db is useless elsewhere. Machine scope (not the scope of the Windows user) keeps them readable
when the Toolkit later runs as a service under another account. A database moved to another computer
needs the passwords entered again.
"""
import ctypes
import sys

_ENTROPY = b"MultiProbe Toolkit / mp_credential"
_FLAGS = 0x1 | 0x4  # CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE


class VaultError(Exception):
    pass


if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    def _call(fn, data: bytes) -> bytes:
        buf = ctypes.create_string_buffer(data, len(data))
        ent = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
        blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_ent = _Blob(len(_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        if not fn(ctypes.byref(blob_in), None, ctypes.byref(blob_ent), None, None, _FLAGS, ctypes.byref(blob_out)):
            raise VaultError(f"DPAPI error {ctypes.GetLastError()}")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _kernel32.LocalFree(blob_out.pbData)

    def encrypt(text: str) -> bytes:
        return _call(_crypt32.CryptProtectData, text.encode("utf-8"))

    def decrypt(data: bytes) -> str:
        return _call(_crypt32.CryptUnprotectData, data).decode("utf-8")

else:
    def encrypt(text: str) -> bytes:
        raise VaultError("Keeping passwords needs Windows (DPAPI)")

    def decrypt(data: bytes) -> str:
        raise VaultError("Keeping passwords needs Windows (DPAPI)")
