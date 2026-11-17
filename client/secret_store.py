"""secret_store.py — the auth token and client secret are kept encrypted on Windows (V1.0.1).

Until 1.0.0, ~/.lmu_garage/client_config.json held both in plain text; on Windows the "owner only"
chmod in client/config.py does nothing. Now they are encrypted with Windows' own Data Protection
API (DPAPI, CryptProtectData — the same mechanism browsers use for saved passwords): only the same
Windows user on the same PC can decrypt them. No extra package — ctypes and the system's crypt32.dll.

Stored form: "dpapi:" + base64. A value without that prefix is plain text (files from 1.0.0 and
older, and every other operating system); client/config.py rewrites such a file encrypted the next
time it is read on Windows.

A value that cannot be decrypted (the file was copied from another PC or user) counts as missing —
the client then simply asks to connect again.
"""

from __future__ import annotations

import base64
import sys

PREFIX = "dpapi:"
_ENTROPY = b"Garage16 client credentials v1"


class SecretUnavailable(Exception):
    """The stored value cannot be decrypted here."""


def available() -> bool:
    return sys.platform == "win32"


def _blob_type():
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    return DATA_BLOB


def _to_blob(data: bytes):
    import ctypes

    blob_type = _blob_type()
    buffer = ctypes.create_string_buffer(data, len(data))
    return blob_type(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _from_blob(blob) -> bytes:
    import ctypes

    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def _dpapi_encrypt(data: bytes) -> bytes:   # pragma: no cover — Windows only (tests patch it)
    import ctypes

    blob_in, _keep = _to_blob(data)
    entropy, _keep2 = _to_blob(_ENTROPY)
    blob_out = _blob_type()()
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(blob_in), "Garage16", ctypes.byref(entropy),
                                                   None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise OSError("CryptProtectData failed")
    return _from_blob(blob_out)


def _dpapi_decrypt(data: bytes) -> bytes:   # pragma: no cover — Windows only (tests patch it)
    import ctypes

    blob_in, _keep = _to_blob(data)
    entropy, _keep2 = _to_blob(_ENTROPY)
    blob_out = _blob_type()()
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, ctypes.byref(entropy),
                                                     None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise OSError("CryptUnprotectData failed")
    return _from_blob(blob_out)


def is_protected(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def protect(value: str) -> str:
    """The value as it is written to the file: encrypted on Windows, unchanged elsewhere (or if
    Windows refuses — the client must keep working, as 1.0.0 did)."""
    if not available() or not value or is_protected(value):
        return value
    try:
        return PREFIX + base64.b64encode(_dpapi_encrypt(value.encode("utf-8"))).decode("ascii")
    except (OSError, AttributeError, ValueError):
        return value


def unprotect(value) -> str:
    """The plain value. Raises SecretUnavailable for an encrypted value this user/PC cannot read."""
    if not is_protected(value):
        return value
    if not available():
        raise SecretUnavailable("encrypted on Windows")
    try:
        return _dpapi_decrypt(base64.b64decode(value[len(PREFIX):])).decode("utf-8")
    except (OSError, AttributeError, ValueError) as exc:
        raise SecretUnavailable(str(exc)) from exc
