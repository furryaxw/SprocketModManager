import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.errors import DownloadError, InstallError
from sprocket_mod_manager.melonloader import (
    MELONLOADER_ASSET_NAME,
    MelonLoaderManager,
)
from sprocket_mod_manager.semver import Version


def archive_bytes(*, unsafe_name: str | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("version.dll", b"new proxy")
        archive.writestr("MelonLoader/net6/MelonLoader.dll", b"new loader")
        archive.writestr("MelonLoader/Documentation/README.md", b"docs")
        if unsafe_name:
            archive.writestr(unsafe_name, b"escape")
    return output.getvalue()


def release_record(payload: bytes, *, digest: str | None = None) -> dict:
    return {
        "tag_name": "v0.7.3",
        "html_url": "https://github.com/LavaGang/MelonLoader/releases/tag/v0.7.3",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "id": 42,
                "name": MELONLOADER_ASSET_NAME,
                "size": len(payload),
                "browser_download_url": (
                    "https://github.com/LavaGang/MelonLoader/releases/download/"
                    "v0.7.3/MelonLoader.x64.zip"
                ),
                "digest": digest,
                "updated_at": "2026-05-14T20:20:00Z",
            }
        ],
    }


class FakeHttp:
    def __init__(self, payload: bytes, *, digest: str | None = None):
        self.payload = payload
        self.record = release_record(payload, digest=digest)
        self.cache_seconds = None

    def get_json(self, _url, *, cache_seconds):
        self.cache_seconds = cache_seconds
        return self.record

    def download(self, asset, destination, progress=None):
        if progress:
            progress(f"downloaded {len(self.payload):,} bytes")
        destination.write_bytes(self.payload)
        return destination


def game_directory(root: Path) -> Path:
    game = root / "game"
    game.mkdir()
    (game / "Sprocket.exe").write_bytes(b"")
    return game


class MelonLoaderManagerTests(unittest.TestCase):
    def test_detect_requires_proxy_and_loader_and_reads_loader_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            game = game_directory(Path(temporary))
            (game / "version.dll").write_bytes(b"proxy")
            core = game / "MelonLoader" / "net6" / "MelonLoader.dll"
            core.parent.mkdir(parents=True)
            core.write_bytes(b"loader")

            with patch(
                "sprocket_mod_manager.melonloader._file_version",
                return_value=Version.parse("0.7.2"),
            ):
                installation = MelonLoaderManager.detect(game)

        self.assertTrue(installation.installed)
        self.assertEqual(str(installation.version), "0.7.2")

    def test_latest_release_selects_official_x64_zip(self):
        payload = archive_bytes()
        http = FakeHttp(payload)
        manager = MelonLoaderManager(Path("app"), http)

        release = manager.latest_release(refresh=True)

        self.assertEqual(str(release.version), "0.7.3")
        self.assertEqual(release.asset.name, MELONLOADER_ASSET_NAME)
        self.assertEqual(http.cache_seconds, 0)

    @patch("sprocket_mod_manager.melonloader.sprocket_is_running", return_value=False)
    def test_install_verifies_digest_overwrites_payload_and_preserves_generated_files(self, _running):
        payload = archive_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        http = FakeHttp(payload, digest=f"sha256:{digest}")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_directory(root)
            (game / "version.dll").write_bytes(b"old proxy")
            core = game / "MelonLoader" / "net6" / "MelonLoader.dll"
            core.parent.mkdir(parents=True)
            core.write_bytes(b"old loader")
            generated = game / "MelonLoader" / "Il2CppAssemblies" / "Assembly-CSharp.dll"
            generated.parent.mkdir(parents=True)
            generated.write_bytes(b"generated")

            result = MelonLoaderManager(root / "app", http).install(game)

            self.assertEqual((game / "version.dll").read_bytes(), b"new proxy")
            self.assertEqual(core.read_bytes(), b"new loader")
            self.assertEqual(generated.read_bytes(), b"generated")
            self.assertEqual(result.files_installed, 3)
            self.assertEqual(result.sha256, digest)
            self.assertTrue(result.publisher_verified)

    @patch("sprocket_mod_manager.melonloader.sprocket_is_running", return_value=False)
    def test_install_rejects_zip_path_traversal(self, _running):
        payload = archive_bytes(unsafe_name="../outside.dll")
        http = FakeHttp(payload)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_directory(root)

            with self.assertRaisesRegex(InstallError, "unsafe path"):
                MelonLoaderManager(root / "app", http).install(game)

            self.assertFalse((root / "outside.dll").exists())
            self.assertFalse((game / "version.dll").exists())

    @patch("sprocket_mod_manager.melonloader.sprocket_is_running", return_value=False)
    def test_install_rejects_digest_mismatch_before_writing_game(self, _running):
        payload = archive_bytes()
        http = FakeHttp(payload, digest="sha256:" + "0" * 64)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_directory(root)

            with self.assertRaisesRegex(DownloadError, "SHA-256 mismatch"):
                MelonLoaderManager(root / "app", http).install(game)

            self.assertFalse((game / "version.dll").exists())

    @patch("sprocket_mod_manager.melonloader.sprocket_is_running", return_value=False)
    def test_install_rolls_back_files_when_replacement_fails(self, _running):
        payload = archive_bytes()
        http = FakeHttp(payload)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_directory(root)
            proxy = game / "version.dll"
            proxy.write_bytes(b"old proxy")
            real_replace = __import__("os").replace

            def replace(source, target):
                if Path(target).name == "MelonLoader.dll":
                    raise OSError("simulated locked file")
                return real_replace(source, target)

            with (
                patch("sprocket_mod_manager.melonloader.os.replace", side_effect=replace),
                self.assertRaisesRegex(OSError, "simulated locked file"),
            ):
                MelonLoaderManager(root / "app", http).install(game)

            self.assertEqual(proxy.read_bytes(), b"old proxy")
            self.assertFalse((game / "MelonLoader" / "net6" / "MelonLoader.dll").exists())


if __name__ == "__main__":
    unittest.main()
