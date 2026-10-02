from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

_ENTROPY = b"sprocket-mod-manager-credential"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _protect(data: bytes) -> bytes:
    """Encrypt with the user's DPAPI master key; only this Windows account can decrypt."""
    source = _blob(data)
    output = _DataBlob()
    if not ctypes.windll.Crypt32.CryptProtectData(
            ctypes.byref(source), "sprocket-mod-manager", _blob(_ENTROPY), None, None, 0,
            ctypes.byref(output)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.Kernel32.LocalFree(output.pbData)


def _unprotect(blob: bytes) -> bytes:
    source = _blob(blob)
    output = _DataBlob()
    if not ctypes.windll.Crypt32.CryptUnprotectData(
            ctypes.byref(source), None, _blob(_ENTROPY), None, None, 0, ctypes.byref(output)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.Kernel32.LocalFree(output.pbData)


class CredentialStore:
    """Per-name secrets for one manager installation.

    Credentials live in DPAPI-encrypted files inside the application directory, so they follow
    that directory and are removed with it. The user-level Windows vault is read as well,
    because a token may already sit there under the same name; nothing is written back to it.

    `directory` is what selects file storage. Without one the store keeps using the vault,
    which is also where every name it does not find on disk is looked up.
    """

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self, namespace: str = "SprocketModManager", directory: Path | None = None):
        self.namespace = namespace
        self.directory = Path(directory) if directory is not None else None
        self._available = os.name == "nt"

    def _target(self, name: str) -> str:
        value = str(name).strip()
        if not value or any(char in value for char in "\\/\x00"):
            raise ValueError("invalid credential target")
        return f"{self.namespace}/{value}"

    def _file(self, name: str) -> Path:
        return Path(self.directory) / f"{name}.bin"

    def save(self, name: str, token: str) -> str:
        target = self._target(name)
        value = str(token).strip().encode("utf-8")
        if not value or len(value) > 4096:
            raise ValueError("invalid session token")
        if self.directory is None:
            self._write_vault(target, value)
            return target
        path = self._file(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        try:
            temporary.write_bytes(_protect(value))
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return str(path)

    def load(self, name: str) -> str:
        if self.directory is not None:
            path = self._file(name)
            if path.is_file():
                try:
                    return _unprotect(path.read_bytes()).decode("utf-8")
                except (OSError, ValueError, UnicodeDecodeError):
                    pass
        return self._read_vault(self._target(name))

    def delete(self, name: str) -> None:
        if self.directory is not None:
            try:
                self._file(name).unlink(missing_ok=True)
            except OSError:
                pass
        self._delete_vault(self._target(name))

    # ---- Windows vault ------------------------------------------------------

    def _write_vault(self, target: str, value: bytes) -> None:
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
        if not ctypes.windll.Advapi32.CredWriteW(ctypes.byref(credential), 0):
            raise ctypes.WinError()

    def _read_vault(self, target: str) -> str:
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
        except UnicodeDecodeError:
            return ""
        finally:
            ctypes.windll.Advapi32.CredFree(pointer)

    def _delete_vault(self, target: str) -> None:
        if self._available:
            ctypes.windll.Advapi32.CredDeleteW(target, self._CRED_TYPE_GENERIC, 0)
