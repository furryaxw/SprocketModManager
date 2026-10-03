"""私有索引的验签契约：条目是公开 v3 条目加条目级 signature，任何一条不过就拒整份索引。"""

from __future__ import annotations

import base64
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sprocket_mod_manager.infrastructure.private_servers import DeveloperServerClient
from sprocket_mod_manager.utilities.signatures import (
    canonical_json,
    public_key_fingerprint,
    public_key_from_identity,
    sign_detached,
    verify_detached,
)

KEY_ID = "test-key"


def make_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def identity_for(key: Ed25519PrivateKey) -> dict:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {
        "algorithm": "ed25519",
        "encoding": "base64url",
        "key_id": KEY_ID,
        "public_key": base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="),
        "fingerprint": public_key_fingerprint(key.public_key()),
    }


def entry() -> dict:
    """一条 v3 条目：只强制四项，其余按需。"""
    return {
        "schema_version": 3,
        "id": "team1.example-mod",
        "name": "ExampleMod",
        "install": {"files": [{"match": "Example.dll", "type": "melonloader:mod"}]},
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


def serve(index: dict):
    """把 `/v1/packages` 的响应交给 client，其余端点一律视为测试写错。"""

    def fake_urlopen(request, timeout=0):  # noqa: ARG001 - 契约如此
        url = getattr(request, "full_url", str(request))
        if url.endswith("/v1/packages"):
            return _Response(index)
        raise AssertionError(f"unexpected request: {url}")

    return fake_urlopen


def index_with(*entries: dict) -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-10-03T00:00:00Z",
        "server": {"server_id": "test-server", "name": "Test server"},
        "teams": [{"team_id": "team1", "name": "Team One", "packages": list(entries)}],
    }


def client_for(key: Ed25519PrivateKey) -> DeveloperServerClient:
    """信任已经建立好的 client：这一组用例只问验签，不问 server-info 的协商。"""
    client = DeveloperServerClient("http://127.0.0.1:8787")
    client._signing_identity = {"key": key.public_key(), "key_id": KEY_ID}
    client._info_loaded = True
    return client


class PrivatePackageIndexTests(unittest.TestCase):
    def test_a_signed_entry_comes_back_without_its_signature(self) -> None:
        key = make_key()
        raw = entry()
        signed = {**raw, "signature": sign_detached(raw, key, key_id=KEY_ID)}
        client = client_for(key)

        with patch(
            "sprocket_mod_manager.infrastructure.private_servers.developer_server_client.urlopen",
            serve(index_with(signed)),
        ):
            packages = client.packages()

        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0]["id"], "team1.example-mod")
        self.assertEqual(packages[0]["schema_version"], 3)
        self.assertNotIn("signature", packages[0], "验签过了就不必把签名继续往下传")

    def test_every_team_is_flattened_into_one_list(self) -> None:
        key = make_key()
        first, second = entry(), {**entry(), "id": "team2.other-mod"}
        index = {
            "schema_version": 1,
            "generated_at": "2026-10-03T00:00:00Z",
            "server": {"server_id": "test-server", "name": "Test server"},
            "teams": [
                {
                    "team_id": "team1",
                    "name": "Team One",
                    "packages": [{**first, "signature": sign_detached(first, key, key_id=KEY_ID)}],
                },
                {
                    "team_id": "team2",
                    "name": "Team Two",
                    "packages": [{**second, "signature": sign_detached(second, key, key_id=KEY_ID)}],
                },
            ],
        }
        client = client_for(key)

        with patch(
            "sprocket_mod_manager.infrastructure.private_servers.developer_server_client.urlopen",
            serve(index),
        ):
            packages = client.packages()

        self.assertEqual([item["id"] for item in packages], ["team1.example-mod", "team2.other-mod"])

    def test_an_unsigned_entry_rejects_the_whole_index(self) -> None:
        key = make_key()
        client = client_for(key)

        with patch(
            "sprocket_mod_manager.infrastructure.private_servers.developer_server_client.urlopen",
            serve(index_with(entry())),
        ):
            with self.assertRaisesRegex(ValueError, "not signed"):
                client.packages()

    def test_a_tampered_entry_rejects_the_whole_index(self) -> None:
        key = make_key()
        raw = entry()
        signed = {**raw, "signature": sign_detached(raw, key, key_id=KEY_ID)}
        signed["name"] = "Tampered"  # 签名之后再改字段
        client = client_for(key)

        with patch(
            "sprocket_mod_manager.infrastructure.private_servers.developer_server_client.urlopen",
            serve(index_with(signed)),
        ):
            with self.assertRaisesRegex(ValueError, "signature verification failed"):
                client.packages()

    def test_a_signature_from_another_key_rejects_the_whole_index(self) -> None:
        trusted, other = make_key(), make_key()
        raw = entry()
        signed = {**raw, "signature": sign_detached(raw, other, key_id=KEY_ID)}
        client = client_for(trusted)

        with patch(
            "sprocket_mod_manager.infrastructure.private_servers.developer_server_client.urlopen",
            serve(index_with(signed)),
        ):
            with self.assertRaises(ValueError):
                client.packages()

    def test_a_signature_naming_another_key_id_is_rejected(self) -> None:
        key = make_key()
        raw = entry()
        signed = {**raw, "signature": sign_detached(raw, key, key_id="someone-else")}
        client = client_for(key)

        with patch(
            "sprocket_mod_manager.infrastructure.private_servers.developer_server_client.urlopen",
            serve(index_with(signed)),
        ):
            with self.assertRaisesRegex(ValueError, "unexpected key"):
                client.packages()


class CrossImplementationTests(unittest.TestCase):
    """锁定与服务端的字节级兼容：fixture 里这个签名是服务端实现签出来的。

    两侧各有一份 `canonical_json`，只要序列化差一个字节，真机上全部私有条目都会验签失败 ——
    这种失败在本地用自己签自己验是看不出来的，所以拿服务端的产物当回归基线。
    """

    FIXTURE = Path(__file__).parent / "fixtures" / "private" / "cross-implementation-entry.json"

    def payload(self) -> dict:
        return json.loads(self.FIXTURE.read_text(encoding="utf-8"))

    def test_a_server_signed_entry_verifies_here(self) -> None:
        payload = self.payload()
        signed = payload["signed"]
        manifest = {key: value for key, value in signed.items() if key != "signature"}

        verify_detached(
            manifest, signed["signature"], public_key_from_identity(payload["identity"])
        )

    def test_canonical_json_matches_the_server_byte_for_byte(self) -> None:
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
