"""私有索引文档的验签契约：`GET /packages.json` 是**文档级**签名，任何一处不过就拒整份。

条目就是公开 v3 条目（签名只加在文档上），所以这一组只问文档这一层。
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sprocket_mod_manager.infrastructure.private_servers import DeveloperServerClient
from sprocket_mod_manager.utilities.signatures import (
    canonical_json,
    public_key_from_identity,
    sign_detached,
    verify_detached,
)

KEY_ID = "test-key"
SERVER_ID = "test-server"


def make_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def entry() -> dict:
    """一条 v3 条目：只强制四项，其余按需。`release` 与 `release.source` 由服务器自己写。"""
    return {
        "schema_version": 3,
        "id": "team1.example-mod",
        "name": "ExampleMod",
        "install": {"files": [{"match": "Example.dll", "type": "melonloader:mod"}]},
        "release": {"source": {"type": "external", "hosts": ["mods.example.invalid"]}},
        "releases": [{"id": 1, "version": "1.0.0", "assets": []}],
    }


class _Response:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = {"Content-Length": str(len(self._body))}

    def read(self, _limit: int | None = None) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def document_with(*entries: dict) -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-10-17T00:00:00Z",
        "server": {"server_id": SERVER_ID, "name": "Test server"},
        "packages": list(entries),
    }


def signed(document: dict, key: Ed25519PrivateKey, *, key_id: str = KEY_ID) -> dict:
    return {**document, "signature": sign_detached(document, key, key_id=key_id)}


def serve(document: dict):
    """把 `/packages.json` 的响应交给 client，其余端点一律视为测试写错。"""

    def fake_urlopen(request, timeout=0):  # noqa: ARG001 - 契约如此
        url = getattr(request, "full_url", str(request))
        if url.endswith("/packages.json"):
            return _Response(document)
        raise AssertionError(f"unexpected request: {url}")

    return fake_urlopen


def client_for(key: Ed25519PrivateKey) -> DeveloperServerClient:
    """信任已经建立好的 client：这一组用例只问验签，不问 server-info 的协商。"""
    client = DeveloperServerClient("http://127.0.0.1:8787")
    client._signing_identity = {"key": key.public_key(), "key_id": KEY_ID}
    client._server_id = SERVER_ID
    client._info_loaded = True
    return client


def fetch(document: dict, key: Ed25519PrivateKey) -> list[dict]:
    with patch(
        "sprocket_mod_manager.infrastructure.private_servers.developer_server_client.urlopen",
        serve(document),
    ):
        return client_for(key).packages()


class PrivatePackageIndexTests(unittest.TestCase):
    def test_a_signed_document_returns_its_packages(self) -> None:
        key = make_key()

        packages = fetch(signed(document_with(entry()), key), key)

        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0]["id"], "team1.example-mod")
        self.assertEqual(packages[0]["schema_version"], 3)
        self.assertNotIn("signature", packages[0], "条目本身没有签名")

    def test_the_whole_list_comes_from_one_document(self) -> None:
        key = make_key()
        second = {**entry(), "id": "team1.other-mod"}

        packages = fetch(signed(document_with(entry(), second), key), key)

        self.assertEqual(
            [item["id"] for item in packages], ["team1.example-mod", "team1.other-mod"]
        )

    def test_an_unsigned_document_rejects_the_index(self) -> None:
        key = make_key()

        with self.assertRaisesRegex(ValueError, "not signed"):
            fetch(document_with(entry()), key)

    def test_a_tampered_document_rejects_the_index(self) -> None:
        key = make_key()
        document = signed(document_with(entry()), key)
        document["packages"][0]["name"] = "Tampered"  # 签名之后再改字段

        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            fetch(document, key)

    def test_a_document_from_another_server_rejects_the_index(self) -> None:
        key = make_key()
        document = signed(document_with(entry()), key)
        document["server"]["server_id"] = "someone-else"
        document["signature"] = sign_detached(
            {k: v for k, v in document.items() if k != "signature"}, key, key_id=KEY_ID
        )

        with self.assertRaisesRegex(ValueError, "not from this server"):
            fetch(document, key)

    def test_a_signature_from_another_key_rejects_the_index(self) -> None:
        trusted, other = make_key(), make_key()

        with self.assertRaises(ValueError):
            fetch(signed(document_with(entry()), other), trusted)

    def test_a_signature_naming_another_key_id_is_rejected(self) -> None:
        key = make_key()

        with self.assertRaisesRegex(ValueError, "unexpected key"):
            fetch(signed(document_with(entry()), key, key_id="someone-else"), key)

    def test_packages_must_be_a_list(self) -> None:
        key = make_key()
        document = signed({**document_with(), "packages": {"team1": []}}, key)

        with self.assertRaisesRegex(ValueError, "index is invalid"):
            fetch(document, key)


class CrossImplementationTests(unittest.TestCase):
    """锁定 canonical JSON 的字节级规则。

    两侧各有一份 `canonical_json`，只要序列化差一个字节，真机上全部私有包都会验签失败 ——
    这种失败在本地用自己签自己验是看不出来的，所以留一份已知答案当回归基线。
    """

    FIXTURE = Path(__file__).parent / "fixtures" / "private" / "cross-implementation-document.json"

    def payload(self) -> dict:
        return json.loads(self.FIXTURE.read_text(encoding="utf-8"))

    def test_a_signed_document_verifies_here(self) -> None:
        payload = self.payload()
        document = payload["signed"]
        manifest = {key: value for key, value in document.items() if key != "signature"}

        verify_detached(
            manifest, document["signature"], public_key_from_identity(payload["identity"])
        )

    def test_canonical_json_matches_the_known_answer(self) -> None:
        payload = self.payload()
        manifest = {
            key: value for key, value in payload["signed"].items() if key != "signature"
        }

        self.assertEqual(
            hashlib.sha256(canonical_json(manifest)).hexdigest(),
            payload["canonical_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
