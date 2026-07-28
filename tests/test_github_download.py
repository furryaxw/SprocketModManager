import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.errors import DownloadError
from sprocket_mod_manager.github import GitHubClient, HttpClient
from sprocket_mod_manager.models import RegistryPackage, ReleaseAsset


class FakeResponse:
    def __init__(self, body: bytes, final_url: str):
        self.body = body
        self.final_url = final_url
        self.headers = {"Content-Length": str(len(body)), "ETag": "test"}
        self.read_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.final_url

    def read(self, _size):
        if self.read_count:
            return b""
        self.read_count += 1
        return self.body


class GitHubDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.asset = ReleaseAsset(
            id=1,
            name="TestMod.dll",
            size=4,
            download_url="https://github.com/example/mod/releases/download/v1.0.0/TestMod.dll",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_release_asset_redirect_host_is_allowed(self):
        response = FakeResponse(
            b"test",
            "https://release-assets.githubusercontent.com/github-production-release-asset/test",
        )
        destination = self.root / "TestMod.dll"
        with patch("sprocket_mod_manager.github.urlopen", return_value=response):
            HttpClient(self.root / "cache").download(self.asset, destination)
        self.assertEqual(destination.read_bytes(), b"test")

    def test_unexpected_release_redirect_host_is_rejected(self):
        response = FakeResponse(b"test", "https://downloads.example.com/TestMod.dll")
        with patch("sprocket_mod_manager.github.urlopen", return_value=response):
            with self.assertRaisesRegex(DownloadError, "download host is not allowed"):
                HttpClient(self.root / "cache").download(self.asset, self.root / "TestMod.dll")

    def test_github_token_is_only_sent_to_api_host(self):
        requests = []

        def respond(request, **_kwargs):
            requests.append(request)
            return FakeResponse(b"test", self.asset.download_url)

        with patch("sprocket_mod_manager.github.urlopen", side_effect=respond):
            HttpClient(self.root / "cache", token="secret").download(
                self.asset,
                self.root / "TestMod.dll",
            )
        self.assertNotIn("Authorization", requests[0].headers)

    def test_latest_repository_release_parses_semver_and_page_url(self):
        class JsonHttp:
            def __init__(self):
                self.calls = []

            def get_json(self, url, *, cache_seconds=600):
                self.calls.append((url, cache_seconds))
                return {
                    "tag_name": "v0.2.0",
                    "html_url": "https://github.com/furryaxw/sprocket-mods/releases/tag/v0.2.0",
                    "draft": False,
                    "prerelease": False,
                }

        http = JsonHttp()
        release = GitHubClient(http).latest_repository_release("furryaxw/sprocket-mods")

        self.assertEqual(str(release.version), "0.2.0")
        self.assertEqual(release.tag, "v0.2.0")
        self.assertEqual(
            release.page_url,
            "https://github.com/furryaxw/sprocket-mods/releases/tag/v0.2.0",
        )
        self.assertEqual(
            http.calls,
            [("https://api.github.com/repos/furryaxw/sprocket-mods/releases/latest", 3600)],
        )

    def test_latest_repository_release_rejects_unexpected_page_url(self):
        class JsonHttp:
            @staticmethod
            def get_json(_url, *, cache_seconds=600):
                return {
                    "tag_name": "v0.2.0",
                    "html_url": "https://example.com/update.exe",
                    "draft": False,
                    "prerelease": False,
                }

        with self.assertRaisesRegex(DownloadError, "release page URL"):
            GitHubClient(JsonHttp()).latest_repository_release("furryaxw/sprocket-mods")

    def test_package_release_falls_back_to_latest_when_list_is_empty(self):
        class JsonHttp:
            def __init__(self):
                self.calls = []

            def get_json(self, url, *, cache_seconds=600):
                self.calls.append((url, cache_seconds))
                if url.endswith("/releases?per_page=100&page=1"):
                    return []
                if url.endswith("/releases/latest"):
                    return {
                        "id": 1,
                        "tag_name": "v1.3.1",
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-07-25T14:57:10Z",
                        "assets": [
                            {
                                "id": 2,
                                "name": "TestMod.dll",
                                "size": 4,
                                "browser_download_url": (
                                    "https://github.com/example/TestMod/releases/"
                                    "download/v1.3.1/TestMod.dll"
                                ),
                                "digest": "sha256:test",
                                "updated_at": "2026-07-25T14:57:10Z",
                            }
                        ],
                    }
                raise AssertionError(f"unexpected URL: {url}")

        package = RegistryPackage.from_dict(
            {
                "id": "example.test-mod",
                "name": "TestMod",
                "authors": ["example"],
                "repository": "example/TestMod",
                "license": "GPL-3.0-only",
                "display_name": {"en": "Test Mod"},
                "release": {
                    "include_prerelease": False,
                    "version_pattern": r"^v?([0-9]+\.[0-9]+\.[0-9]+)$",
                    "assets": {"include": ["TestMod.dll"], "exclude": []},
                },
                "dependencies": [],
                "install": {"scan_dlls": True, "exclude": [], "overrides": []},
                "category": "utility",
                "tags": [],
            }
        )
        http = JsonHttp()

        releases = GitHubClient(http).releases(package)

        self.assertEqual([str(release.version) for release in releases], ["1.3.1"])
        self.assertEqual([asset.name for asset in releases[0].assets], ["TestMod.dll"])
        self.assertEqual(len(http.calls), 2)


if __name__ == "__main__":
    unittest.main()
