from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from unittest.mock import patch

from sprocket_mod_manager.infrastructure.credential_store import CredentialStore

MODULE = "sprocket_mod_manager.infrastructure.credential_store"


def _completed(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _secret_tool_store(which: str | None = "/usr/bin/secret-tool") -> CredentialStore:
    with patch(f"{MODULE}.os.name", "posix"), patch(f"{MODULE}.shutil.which", return_value=which):
        return CredentialStore("TestNamespace")


class SecretToolBackendTests(unittest.TestCase):
    def test_save_passes_token_on_stdin_with_service_and_account(self) -> None:
        store = _secret_tool_store()
        with patch(f"{MODULE}.subprocess.run", return_value=_completed()) as run:
            target = store.save("test-server", "  token-value  ")

        self.assertEqual(target, "TestNamespace/test-server")
        command = run.call_args.args[0]
        self.assertEqual(
            command,
            ["/usr/bin/secret-tool", "store", "--label=TestNamespace/test-server",
             "service", "TestNamespace", "account", "test-server"],
        )
        self.assertEqual(run.call_args.kwargs["input"], b"token-value", "token goes over stdin, never argv")

    def test_save_failure_raises_os_error(self) -> None:
        store = _secret_tool_store()
        with (
            patch(f"{MODULE}.subprocess.run", return_value=_completed(1, stderr=b"no secret service")),
            self.assertRaisesRegex(OSError, "no secret service"),
        ):
            store.save("test-server", "token")

    def test_load_returns_lookup_output(self) -> None:
        store = _secret_tool_store()
        with patch(f"{MODULE}.subprocess.run", return_value=_completed(stdout=b"token-value\n")) as run:
            self.assertEqual(store.load("test-server"), "token-value")
        self.assertEqual(run.call_args.args[0][1:], ["lookup", "service", "TestNamespace", "account", "test-server"])

    def test_load_missing_entry_returns_empty(self) -> None:
        store = _secret_tool_store()
        with patch(f"{MODULE}.subprocess.run", return_value=_completed(1)):
            self.assertEqual(store.load("test-server"), "")

    def test_load_timeout_returns_empty(self) -> None:
        store = _secret_tool_store()
        with patch(f"{MODULE}.subprocess.run", side_effect=subprocess.TimeoutExpired("secret-tool", 60)):
            self.assertEqual(store.load("test-server"), "")

    def test_delete_clears_matching_attributes(self) -> None:
        store = _secret_tool_store()
        with patch(f"{MODULE}.subprocess.run", return_value=_completed()) as run:
            store.delete("test-server")
        self.assertEqual(run.call_args.args[0][1:], ["clear", "service", "TestNamespace", "account", "test-server"])

    def test_invalid_server_id_is_rejected_before_running_secret_tool(self) -> None:
        store = _secret_tool_store()
        with patch(f"{MODULE}.subprocess.run") as run:
            for server_id in ("", "a/b", "a\\b"):
                with self.assertRaises(ValueError):
                    store.delete(server_id)
        run.assert_not_called()

    def test_without_secret_tool_store_is_unavailable(self) -> None:
        store = _secret_tool_store(which=None)
        with patch(f"{MODULE}.subprocess.run") as run:
            with self.assertRaises(OSError):
                store.save("test-server", "token")
            self.assertEqual(store.load("test-server"), "")
            store.delete("test-server")
        run.assert_not_called()


@unittest.skipIf(os.name == "nt" or not shutil.which("secret-tool"), "needs secret-tool")
class SecretToolRoundTripTests(unittest.TestCase):
    def test_save_load_delete_round_trip(self) -> None:
        store = CredentialStore("SprocketModManagerTest")
        try:
            store.save("round-trip", "token-value")
        except OSError as exc:
            self.skipTest(f"no Secret Service running: {exc}")
        try:
            self.assertEqual(store.load("round-trip"), "token-value")
        finally:
            store.delete("round-trip")
        self.assertEqual(store.load("round-trip"), "")


if __name__ == "__main__":
    unittest.main()
