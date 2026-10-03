from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from ctypes import wintypes

# Unlocking the keyring may show a prompt, so allow time for the user to answer it.
_SECRET_TOOL_TIMEOUT = 60


class CredentialStore:
    """Per-server session tokens in the OS keyring.

    Windows uses Credential Manager; elsewhere `secret-tool` (libsecret) talks to whichever
    Secret Service is running (GNOME Keyring, KWallet, KeePassXC).
    """

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self, namespace: str = "SprocketModManager") -> None:
        self.namespace = namespace
        self._secret_tool = None if os.name == "nt" else shutil.which("secret-tool")
        self._available = os.name == "nt" or self._secret_tool is not None

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
            raise OSError("no credential store is available")
        if self._secret_tool:
            result = self._run_secret_tool("store", f"--label={target}", *self._attributes(server_id), secret=value)
            if result.returncode != 0:
                raise OSError(f"secret-tool store failed: {result.stderr.decode(errors='replace').strip()}")
            return target

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
        if self._secret_tool:
            result = self._run_secret_tool("lookup", *self._attributes(server_id))
            return result.stdout.decode("utf-8").strip() if result.returncode == 0 else ""

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
        if self._secret_tool:
            self._run_secret_tool("clear", *self._attributes(server_id))
        elif self._available:
            ctypes.windll.Advapi32.CredDeleteW(self._target(server_id), self._CRED_TYPE_GENERIC, 0)

    def _attributes(self, server_id: str) -> tuple[str, ...]:
        self._target(server_id)  # same validation as the Windows target name
        return ("service", self.namespace, "account", str(server_id).strip())

    def _run_secret_tool(self, *args: str, secret: bytes = b"") -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                [self._secret_tool, *args], input=secret, capture_output=True, timeout=_SECRET_TOOL_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess(args, 1, b"", str(exc).encode())
