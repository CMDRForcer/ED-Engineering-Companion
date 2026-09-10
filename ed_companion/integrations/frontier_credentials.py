"""Windows-user-bound storage for Frontier OAuth tokens."""

from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from ed_companion.integrations.frontier_capi import FrontierTokens
from ed_companion.persistence import atomic_write


class FrontierCredentialError(RuntimeError):
    """Credential storage failed without exposing credential contents."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(value):
    buffer = ctypes.create_string_buffer(value)
    return (
        _DataBlob(
            len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        ),
        buffer,
    )


def _dpapi_transform(value, function_name):
    if os.name != "nt":
        raise FrontierCredentialError(
            "Secure Frontier credential storage is available only on Windows."
        )
    source, source_buffer = _blob(value)
    destination = _DataBlob()
    function = getattr(ctypes.windll.crypt32, function_name)
    if function_name == "CryptProtectData":
        success = function(
            ctypes.byref(source), "EDEC Frontier CAPI", None, None, None,
            0x01, ctypes.byref(destination),
        )
    else:
        success = function(
            ctypes.byref(source), None, None, None, None,
            0x01, ctypes.byref(destination),
        )
    del source_buffer
    if not success:
        raise FrontierCredentialError(
            "Windows could not protect Frontier credentials."
        )
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(destination.pbData)


def protect_for_current_user(value):
    return _dpapi_transform(bytes(value), "CryptProtectData")


def unprotect_for_current_user(value):
    return _dpapi_transform(bytes(value), "CryptUnprotectData")


class FrontierCredentialStore:
    """Persist one token set encrypted for the current Windows account."""

    def __init__(self, path, *, protect=None, unprotect=None):
        self.path = Path(path)
        self._protect = protect or protect_for_current_user
        self._unprotect = unprotect or unprotect_for_current_user

    def save(self, tokens):
        if not isinstance(tokens, FrontierTokens):
            raise TypeError("tokens must be FrontierTokens")
        payload = json.dumps({
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "token_type": tokens.token_type,
            "expires_at": tokens.expires_at,
        }, separators=(",", ":")).encode("utf-8")
        try:
            protected = self._protect(payload)
            encoded = base64.b64encode(protected).decode("ascii")
            if not atomic_write(self.path, encoded):
                raise OSError("atomic write rejected")
        except (OSError, ValueError, FrontierCredentialError) as exc:
            raise FrontierCredentialError(
                "Frontier credentials could not be stored securely."
            ) from exc

    def load(self):
        try:
            encoded = self.path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise FrontierCredentialError(
                "Frontier credentials could not be read securely."
            ) from exc
        try:
            payload = json.loads(
                self._unprotect(
                    base64.b64decode(encoded, validate=True)
                ).decode("utf-8")
            )
            tokens = FrontierTokens(
                access_token=str(payload.get("access_token") or ""),
                refresh_token=str(payload.get("refresh_token") or ""),
                token_type=str(payload.get("token_type") or "Bearer"),
                expires_at=float(payload.get("expires_at") or 0),
            )
            if not tokens.access_token or not tokens.refresh_token:
                raise ValueError("incomplete token document")
            return tokens
        except (ValueError, TypeError, UnicodeError,
                FrontierCredentialError) as exc:
            raise FrontierCredentialError(
                "Stored Frontier credentials could not be decrypted."
            ) from exc

    def clear(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise FrontierCredentialError(
                "Frontier credentials could not be removed."
            ) from exc
