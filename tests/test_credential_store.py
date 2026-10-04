from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.infrastructure.credential_store import CredentialStore, _unprotect

TOKEN = "token-value"


class FileStoreTests(unittest.TestCase):
    def _store(self, directory: str) -> CredentialStore:
        return CredentialStore("TestNamespace", Path(directory))

    def test_save_load_delete_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            path = Path(store.save("test-server", f"  {TOKEN}  "))

            self.assertEqual(path, Path(directory) / "test-server.bin")
            self.assertEqual(store.load("test-server"), TOKEN)

            store.delete("test-server")
            self.assertFalse(path.exists())
            self.assertEqual(store.load("test-server"), "")

    def test_save_leaves_no_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.save("test-server", TOKEN)

            self.assertEqual([item.name for item in Path(directory).iterdir()], ["test-server.bin"])

    def test_delete_of_a_missing_entry_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.delete("test-server")
            self.assertEqual(store.load("test-server"), "")

    def test_invalid_name_is_rejected_before_anything_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            for name in ("", "   ", "a/b", "a\\b", "a\x00b"):
                with self.assertRaises(ValueError):
                    store.save(name, TOKEN)
                with self.assertRaises(ValueError):
                    store.delete(name)

            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_invalid_token_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            for token in ("", "   ", "x" * 4097):
                with self.assertRaises(ValueError):
                    store.save("test-server", token)

    def test_unreadable_file_falls_back_to_the_vault(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            (Path(directory) / "test-server.bin").write_bytes(b"\xff\xfe\x00 broken")

            with patch.object(CredentialStore, "_read_vault", return_value="vault-token"):
                self.assertEqual(store.load("test-server"), "vault-token")

    @unittest.skipIf(os.name == "nt", "Windows protects the file with DPAPI")
    def test_file_is_0600_and_holds_the_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            path = Path(store.save("test-server", TOKEN))

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_bytes(), TOKEN.encode("utf-8"))

    @unittest.skipUnless(os.name == "nt", "DPAPI is the Windows protection")
    def test_file_is_protected_with_the_account_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            path = Path(store.save("test-server", TOKEN))

            self.assertEqual(_unprotect(path.read_bytes()).decode("utf-8"), TOKEN)


if __name__ == "__main__":
    unittest.main()
