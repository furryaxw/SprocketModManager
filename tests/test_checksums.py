from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.utilities.checksums import parse_checksum_text, sha256_file


class ChecksumUtilityTests(unittest.TestCase):
    def test_sha256_file_hashes_stream_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(b"Sprocket")
            self.assertEqual(
                sha256_file(path),
                "428cf4329177a0afc5384843966ae6790a5321a1dadd24be4e5f16cdf0e15ad9",
            )

    def test_checksum_parser_supports_common_sidecar_formats(self) -> None:
        digest = "a" * 64
        self.assertEqual(parse_checksum_text(digest, "mod.zip", allow_bare=True), digest)
        self.assertEqual(parse_checksum_text(f"{digest} *mod.zip", "mod.zip"), digest)
        self.assertEqual(parse_checksum_text(f"SHA256 (mod.zip) = {digest}", "mod.zip"), digest)

    def test_checksum_parser_ignores_other_files(self) -> None:
        self.assertIsNone(parse_checksum_text(f"{'b' * 64} other.zip", "mod.zip"))


if __name__ == "__main__":
    unittest.main()
