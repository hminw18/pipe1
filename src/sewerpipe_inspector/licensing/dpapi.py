from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


APP_ENTROPY = b"pipe1-license-state-v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class DpapiError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


class DpapiProtector:
    def __init__(self, entropy: bytes = APP_ENTROPY) -> None:
        self.entropy = entropy

    @staticmethod
    def is_available() -> bool:
        return os.name == "nt"

    def protect(self, data: bytes) -> bytes:
        if not self.is_available():
            raise DpapiError("DPAPI is only available on Windows")
        return _crypt_protect_data(data, self.entropy)

    def unprotect(self, protected_data: bytes) -> bytes:
        if not self.is_available():
            raise DpapiError("DPAPI is only available on Windows")
        return _crypt_unprotect_data(protected_data, self.entropy)


def _make_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)),
    )
    return blob, buffer


def _crypt_protect_data(data: bytes, entropy: bytes) -> bytes:
    data_blob, data_buffer = _make_blob(data)
    entropy_blob, entropy_buffer = _make_blob(entropy)
    output_blob = _DataBlob()

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptProtectData(
        ctypes.byref(data_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    # Keep input buffers alive until after the call.
    _ = (data_buffer, entropy_buffer)
    if not ok:
        raise DpapiError(ctypes.WinError())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _crypt_unprotect_data(protected_data: bytes, entropy: bytes) -> bytes:
    data_blob, data_buffer = _make_blob(protected_data)
    entropy_blob, entropy_buffer = _make_blob(entropy)
    output_blob = _DataBlob()

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(data_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    _ = (data_buffer, entropy_buffer)
    if not ok:
        raise DpapiError(ctypes.WinError())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
