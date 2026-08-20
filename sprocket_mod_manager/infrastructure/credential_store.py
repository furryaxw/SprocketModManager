from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class CredentialStore:
    """Small Windows Credential Manager wrapper for per-server session tokens."""

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self, namespace: str = "SprocketModManager") -> None:
        self.namespace = namespace
        self._available = os.name == "nt"

    def _target(self, server_id: str) -> str:
        value = str(server_id).strip()
        if not value or any(char in value for char in "\\/\x00"):
            raise ValueError("invalid credential target")
        return f"{self.namespace}/{value}"

    def save(self, server_id: str, token: str) -> str:
        target = self._target(server_id)
        value = str(token).strip().encode("utf-8")
        if not value or len(value) > 4096:
            raise ValueError("invalid session token")
        if not self._available:
            raise OSError("Windows Credential Manager is unavailable")

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        blob = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        credential = CREDENTIAL(
            0, self._CRED_TYPE_GENERIC, target, None, wintypes.FILETIME(),
            len(value), ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
            self._CRED_PERSIST_LOCAL_MACHINE, 0, None, None, target,
        )
        advapi = ctypes.windll.Advapi32
        if not advapi.CredWriteW(ctypes.byref(credential), 0):
            raise ctypes.WinError()
        return target

    def load(self, server_id: str) -> str:
        target = self._target(server_id)
        if not self._available:
            return ""

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        pointer = ctypes.POINTER(CREDENTIAL)()
        if not ctypes.windll.Advapi32.CredReadW(target, self._CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            return ""
        try:
            raw = ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize)
            return raw.decode("utf-8")
        finally:
            ctypes.windll.Advapi32.CredFree(pointer)

    def delete(self, server_id: str) -> None:
        if self._available:
            ctypes.windll.Advapi32.CredDeleteW(self._target(server_id), self._CRED_TYPE_GENERIC, 0)
