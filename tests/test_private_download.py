"""私有包下载的契约：流式落盘、大小校验、以及跳转只能落在信任的源里。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from sprocket_mod_manager.infrastructure.private_servers import (
    DeveloperServerClient,
    DeveloperServerError,
)

BASE = "http://127.0.0.1:8787"
PACKAGE = "team1.example-mod"
VERSION = "1.0.0"
DOWNLOAD_URL = f"{BASE}/v1/packages/{PACKAGE}/download?version={VERSION}"
# 条目里的 `download_url`：协议端点，不带版本（两侧的跨实现 fixture 都是这个形状）。
ENTRY_URL = f"{BASE}/v1/packages/{PACKAGE}/download"


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, limit: int | None = None) -> bytes:
        chunk = self._body if limit is None else self._body[:limit]
        self._body = self._body[len(chunk):]
        return chunk

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class _FakeOpener:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.requested: list[str] = []
        self.requests: list[object] = []

    def open(self, request, timeout: int = 0):  # noqa: ARG002 - 契约如此
        url = getattr(request, "full_url", str(request))
        self.requested.append(url)
        self.requests.append(request)
        result = self.responses.get(url)
        if result is None:
            raise AssertionError(f"unexpected request: {url}")
        if isinstance(result, Exception):
            raise result
        return result


def authorization(request) -> str:
    """这一跳带的 `Authorization`；没有就是空串（`Request` 会把头名首字母大写）。"""
    for name, value in getattr(request, "headers", {}).items():
        if name.lower() == "authorization":
            return str(value)
    return ""


def redirect(location: str) -> HTTPError:
    return HTTPError(DOWNLOAD_URL, 302, "Found", {"Location": location}, None)


def client(*, origins: tuple[str, ...] = (), session: str = "session-token") -> DeveloperServerClient:
    client = DeveloperServerClient(BASE, session_token=session)
    client._download_origins = origins
    client._info_loaded = True
    return client


def download(target: Path, opener: _FakeOpener, instance: DeveloperServerClient) -> int:
    with patch(
        "sprocket_mod_manager.infrastructure.private_servers.developer_server_client.build_opener",
        return_value=opener,
    ):
        return instance.download(PACKAGE, VERSION, target)


class PrivateDownloadTests(unittest.TestCase):
    def test_a_same_origin_download_lands_on_disk(self) -> None:
        payload = b"PK\x03\x04archive-bytes"
        opener = _FakeOpener({DOWNLOAD_URL: _Response(payload)})

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "assets" / "mod.zip"
            written = download(target, opener, client())

            self.assertEqual(written, len(payload))
            self.assertEqual(target.read_bytes(), payload)
            self.assertTrue(target.parent.is_dir(), "父目录要自己建出来")

    def test_a_declared_origin_may_redirect(self) -> None:
        payload = b"from-object-storage"
        signed = "https://cdn.example.invalid/signed/mod.zip"
        opener = _FakeOpener({DOWNLOAD_URL: redirect(signed), signed: _Response(payload)})

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mod.zip"
            written = download(target, opener, client(origins=("cdn.example.invalid",)))

            self.assertEqual(written, len(payload))
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(opener.requested, [DOWNLOAD_URL, signed])

    def test_a_redirect_to_another_origin_carries_no_session(self) -> None:
        """签名直链自带鉴权：再捎上 Bearer，存储端会按两种鉴权同时出现拒掉（400），
        那也等于把会话令牌交给第三方。"""
        signed = "https://cdn.example.invalid/signed/mod.zip?X-Amz-Signature=abc"
        opener = _FakeOpener({DOWNLOAD_URL: redirect(signed), signed: _Response(b"bytes")})

        with tempfile.TemporaryDirectory() as directory:
            download(Path(directory) / "mod.zip", opener, client(origins=("cdn.example.invalid",)))

        server_hop, storage_hop = opener.requests
        self.assertIn("Bearer", authorization(server_hop), "服务器自己的端点要会话")
        self.assertEqual(authorization(storage_hop), "", "换个源就不带会话")

    def test_a_failure_names_the_hop_that_failed(self) -> None:
        """失败的那一跳可能是对象存储：它的错误体不是我们的 JSON 契约，得点名是谁。"""
        signed = "https://cdn.example.invalid/signed/mod.zip?X-Amz-Signature=secret"
        failure = HTTPError(signed, 400, "Bad Request", {}, None)
        opener = _FakeOpener({DOWNLOAD_URL: redirect(signed), signed: failure})

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DeveloperServerError) as caught:
                download(Path(directory) / "mod.zip", opener, client(origins=("cdn.example.invalid",)))

            message = str(caught.exception)
            self.assertIn("cdn.example.invalid/signed/mod.zip", message)
            self.assertNotIn("secret", message, "签名是一次性凭据，不进报错文案")

    def test_an_undeclared_origin_is_refused(self) -> None:
        signed = "https://evil.example.invalid/mod.zip"
        opener = _FakeOpener({DOWNLOAD_URL: redirect(signed), signed: _Response(b"nope")})

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mod.zip"
            with self.assertRaisesRegex(DeveloperServerError, "outside its allowed origins"):
                download(target, opener, client())

            self.assertEqual(opener.requested, [DOWNLOAD_URL], "越界的源根本不该被请求")
            self.assertFalse(target.exists())

    def test_a_bare_host_only_matches_https(self) -> None:
        instance = client(origins=("cdn.example.invalid",))

        self.assertTrue(instance._origin_allowed("https://cdn.example.invalid/x"))
        self.assertFalse(
            instance._origin_allowed("http://cdn.example.invalid/x"),
            "裸主机名只认 https：否则中间人可以把它降级成明文",
        )

    def test_a_full_origin_matches_scheme_host_and_port(self) -> None:
        instance = client(origins=("http://127.0.0.1:9000",))

        self.assertTrue(instance._origin_allowed("http://127.0.0.1:9000/x"))
        self.assertFalse(instance._origin_allowed("http://127.0.0.1:9001/x"))
        self.assertFalse(instance._origin_allowed("https://127.0.0.1:9000/x"))

    def test_a_size_mismatch_fails_and_leaves_nothing_behind(self) -> None:
        opener = _FakeOpener({DOWNLOAD_URL: _Response(b"short")})

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mod.zip"
            with patch(
                "sprocket_mod_manager.infrastructure.private_servers"
                ".developer_server_client.build_opener",
                return_value=opener,
            ):
                with self.assertRaisesRegex(DeveloperServerError, "size mismatch"):
                    client().download(PACKAGE, VERSION, target, expected_size=999)

            self.assertFalse(target.exists(), "半截文件不许留下")

    def test_an_entry_url_without_a_version_gets_the_resolved_one(self) -> None:
        """条目说的是端点，版本由客户端按解析出来的那版给：不补这一下服务器会回 400。"""
        payload = b"PK\x03\x04archive-bytes"
        opener = _FakeOpener({DOWNLOAD_URL: _Response(payload)})

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mod.zip"
            with patch(
                "sprocket_mod_manager.infrastructure.private_servers"
                ".developer_server_client.build_opener",
                return_value=opener,
            ):
                written = client().download(PACKAGE, VERSION, target, url=ENTRY_URL)

            self.assertEqual(written, len(payload))
            self.assertEqual(opener.requested, [DOWNLOAD_URL])

    def test_an_entry_url_that_names_a_version_is_used_as_it_is(self) -> None:
        older = f"{ENTRY_URL}?version=0.9.0"
        opener = _FakeOpener({older: _Response(b"older")})

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mod.zip"
            with patch(
                "sprocket_mod_manager.infrastructure.private_servers"
                ".developer_server_client.build_opener",
                return_value=opener,
            ):
                client().download(PACKAGE, VERSION, target, url=older)

            self.assertEqual(opener.requested, [older], "服务器点名的版本不改写")

    def test_a_signed_url_is_used_as_it_is(self) -> None:
        """对象存储/签名直链：加一个查询参数会把签名弄坏。"""
        signed = "https://cdn.example.invalid/objects/mod.zip?X-Signature=abc"
        opener = _FakeOpener({signed: _Response(b"signed")})

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mod.zip"
            with patch(
                "sprocket_mod_manager.infrastructure.private_servers"
                ".developer_server_client.build_opener",
                return_value=opener,
            ):
                client(origins=("cdn.example.invalid",)).download(
                    PACKAGE, VERSION, target, url=signed
                )

            self.assertEqual(opener.requested, [signed])

    def test_a_download_without_a_session_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "session is required"):
                client(session="").download(PACKAGE, VERSION, Path(directory) / "mod.zip")

    def test_an_error_response_carries_the_server_code(self) -> None:
        failure = HTTPError(DOWNLOAD_URL, 403, "Forbidden", {}, None)
        opener = _FakeOpener({DOWNLOAD_URL: failure})

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mod.zip"
            with self.assertRaises(DeveloperServerError) as caught:
                download(target, opener, client())

            self.assertEqual(caught.exception.status, 403)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
