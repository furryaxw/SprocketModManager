import json
import base64
import hashlib
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from sprocket_mod_manager.domain.models import RegistryPackage
from sprocket_mod_manager.infrastructure.private_servers import (
    DeveloperServerClient,
    DeveloperServerError,
    PrivateCatalogCache,
    PrivatePackageManifest,
    normalize_server_url,
    github_gist_sync,
    GITHUB_GIST_FILENAME,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from sprocket_mod_manager.utilities.signatures import sign_detached
from sprocket_mod_manager.utilities.signatures import public_key_fingerprint
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.application.solver import DependencySolver
from sprocket_mod_manager.presentation.web_gui import ClientApi


def client_javascript(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((root / "js").glob("*.js"))
    )


def server_reading(api: ClientApi) -> dict:
    """开发者服务器那份读数（服务器 / 私有包 / 登录状态）归数据层：测试从那里读。"""
    return api.data.get("servers") or {}


def signing_identity(private_key, key_id):
    public = private_key.public_key()
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {
        "algorithm": "ed25519",
        "encoding": "base64url",
        "key_id": key_id,
        "public_key": base64.urlsafe_b64encode(raw).decode().rstrip("="),
        "fingerprint": public_key_fingerprint(public),
    }


class DemoHandler(BaseHTTPRequestHandler):
    package_files = []
    package_archive = b""
    package_archive_name = "PrivateMod.zip"
    package_version = "1.0.0"
    package_install = {"scan_dlls": False, "exclude": [], "overrides": [{"match": "PrivateMod.dll", "target": "Mods"}]}
    download_status = 200
    last_idempotency_key = ""
    idempotency_keys = []
    def do_GET(self):
        if self.path == "/v1/server-info":
            self.respond({
                "protocol_version": 1,
                "server_id": "test-server",
                "name": "Test Server",
                "operator": "Tester",
                "demo_auth": True,
                "signing_identity": None,
            })
        elif self.path == "/v1/entitlements" or self.path.startswith("/v1/entitlements?"):
            self.respond({
                "github_user_id": "123",
                "server_time": 1787052553,
                "permissions": ["download:private.mod"],
                "grants": [{"id": 1, "expires_at": 1893456000, "active": True, "state": "active"}],
            })
        elif self.path.startswith("/v1/packages/private.mod/download?"):
            if self.download_status != 200:
                self.respond({
                    "code": "download_unauthorized",
                    "error": "download authorization expired",
                }, self.download_status)
                return
            if not self.package_archive:
                self.respond({"error": "archive unavailable"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(self.package_archive)))
            self.end_headers()
            self.wfile.write(self.package_archive)
        elif self.path == "/v1/packages" or self.path.startswith("/v1/packages?"):
            package = {
                "schema_version": 1,
                "id": "private.mod", "name": "PrivateMod",
                "authors": ["Tester"], "license": "Private distribution",
                "display_name": {"en": "Private Mod"},
                "description": {"en": "test"}, "dependencies": [],
                "install": self.package_install,
                "category": "other", "tags": [], "recommendations": [],
                "files": self.package_files,
                "releases": [{
                    "id": 1, "tag": self.package_version, "version": self.package_version,
                    "prerelease": "-" in self.package_version, "published_at": "", "assets": [],
                }],
            }
            if self.package_archive:
                package["releases"][0]["assets"] = [{
                    "name": self.package_archive_name,
                    "size": len(self.package_archive),
                    "digest": f"sha256:{hashlib.sha256(self.package_archive).hexdigest()}",
                    "download_path": "/v1/packages/private.mod/download",
                }]
            self.respond({"packages": [package]})
        else:
            self.respond({"error": "not found"}, 404)

    def do_POST(self):
        type(self).last_idempotency_key = self.headers.get("Idempotency-Key", "")
        type(self).idempotency_keys.append(type(self).last_idempotency_key)
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        if self.path == "/v1/packages/private.mod/download-url" and body.get("version") == self.package_version:
            self.respond({"token": "download-token", "expires_in": 120})
        elif self.path == "/v1/keys/redeem" and body.get("key") == "TEST-KEY":
            self.respond({"permissions": ["download:private.mod"]})
        else:
            self.respond({"error": "bad request"}, 400)

    def respond(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, _format, *_args):
        pass


def private_manifest(package_id, *, dependencies=(), archive=b"package"):
    asset_name = package_id.replace(".", "-") + ".dll"
    return PrivatePackageManifest.from_dict({
        "schema_version": 1,
        "id": package_id,
        "name": package_id.replace(".", "_").title(),
        "authors": ["Tester"],
        "license": "Private distribution",
        "display_name": {"en": package_id},
        "description": {"en": "test"},
        "dependencies": list(dependencies),
        "install": {
            "scan_dlls": False,
            "exclude": [],
            "overrides": [{"match": asset_name, "target": "Mods"}],
        },
        "category": "other",
        "tags": [],
        "recommendations": [],
        "files": [{
            "name": asset_name,
            "target": f"Mods/{asset_name}",
            "sha256": hashlib.sha256(archive).hexdigest(),
        }],
        "releases": [{
            "id": 1,
            "tag": "1.0.0",
            "version": "1.0.0",
            "prerelease": False,
            "published_at": "",
            "assets": [] if not archive else [{
                "id": 1,
                "name": asset_name,
                "size": len(archive),
                "digest": f"sha256:{hashlib.sha256(archive).hexdigest()}",
                "download_path": f"/v1/packages/{package_id}/download",
            }],
        }],
    })


def public_package(package_id, version="1.0.0"):
    asset_name = package_id.replace(".", "-") + ".dll"
    return RegistryPackage.from_dict({
        "id": package_id,
        "name": package_id.replace(".", "_").title(),
        "authors": ["Tester"],
        "repository": "test/public-mod",
        "license": "MIT",
        "display_name": {"en": package_id},
        "description": {"en": "test"},
        "release": {
            "include_prerelease": False,
            "version_pattern": "^v(.+)$",
            "assets": {"include": [asset_name], "exclude": []},
        },
        "dependencies": [],
        "install": {"scan_dlls": True, "exclude": [], "overrides": []},
        "category": "other",
        "tags": [],
        "recommendations": [],
        "releases": [{
            "id": 1,
            "tag": f"v{version}",
            "version": version,
            "prerelease": False,
            "published_at": "",
            "page_url": f"https://github.com/test/public-mod/releases/tag/v{version}",
            "assets": [{
                "id": 1,
                "name": asset_name,
                "size": 1,
                "digest": "sha256:" + hashlib.sha256(b"x").hexdigest(),
                "download_url": f"https://github.com/test/public-mod/releases/download/v{version}/{asset_name}",
            }],
        }],
    })


class PrivateServerTests(unittest.TestCase):
    def test_verified_rotation_updates_persisted_server_identity(self):
        previous = Ed25519PrivateKey.generate()
        next_key = Ed25519PrivateKey.generate()
        previous_identity = signing_identity(previous, "key-1")
        next_identity = signing_identity(next_key, "key-2")
        now = int(time.time())
        declaration = {
            "schema_version": 1,
            "server_id": "signed",
            "previous_key_id": "key-1",
            "next_key_id": "key-2",
            "next_public_key": next_identity["public_key"],
            "effective_at": now - 10,
            "previous_key_expires_at": now + 3600,
        }
        server_info = {
            "protocol_version": 1,
            "server_id": "signed",
            "name": "Signed",
            "signing_identity": next_identity,
            "signing_rotation": {
                "declaration": declaration,
                "previous_signature": sign_detached(declaration, previous, key_id="key-1"),
                "next_signature": sign_detached(declaration, next_key, key_id="key-2"),
            },
        }
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                entry = {
                    "server_id": "signed",
                    "name": "Signed",
                    "url": "https://signed.example",
                    "public_key_fingerprint": previous_identity["fingerprint"],
                    "signing_identity": previous_identity,
                }
                api.config["developer_servers"] = [entry]
                api.config_store.save(api.config)
                with patch.object(DeveloperServerClient, "_request", return_value=server_info):
                    client, _info = api._private_controller._trusted_server_client(entry)
                stored = api.config_store.load()["developer_servers"][0]
            finally:
                api.install_queue.close()
        self.assertTrue(client.rotation_applied)
        self.assertEqual(stored["signing_identity"]["key_id"], "key-2")
        self.assertEqual(stored["public_key_fingerprint"], next_identity["fingerprint"])

    def test_expired_signed_cache_is_not_exposed_while_server_is_offline(self):
        private = Ed25519PrivateKey.generate()
        identity = signing_identity(private, "key-1")
        now = int(time.time())
        status = {
            "schema_version": 1,
            "server_id": "signed",
            "active_key_id": "key-1",
            "revoked_key_ids": [],
            "issued_at": now - 3600,
            "expires_at": now - 1,
        }
        snapshot = {"status": status, "signature": sign_detached(status, private, key_id="key-1")}
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                entry = {
                    "server_id": "signed",
                    "name": "Signed",
                    "url": "https://signed.example",
                    "public_key_fingerprint": identity["fingerprint"],
                    "signing_identity": identity,
                }
                api.config["github_user_id"] = "123"
                api.config["developer_servers"] = [entry]
                api.config_store.save(api.config)
                api.private_catalog_cache.save(
                    "signed", "123", (private_manifest("private.cached"),),
                    {"permissions": ["download:private.cached"], "grants": []},
                    key_status=snapshot,
                    signing_identity=identity,
                )
                with patch.object(DeveloperServerClient, "info", side_effect=ValueError("offline")):
                    api.get_developer_servers()
                    reading = server_reading(api)
            finally:
                api.install_queue.close()
        self.assertEqual(reading["servers"][0]["status"], "offline")
        self.assertNotIn("cached", reading["servers"][0])
        self.assertIn("expired", reading["servers"][0]["cache_error"])
        self.assertEqual(reading["packages"], [])

    def test_strict_key_status_rejects_expired_or_revoked_signing_key(self):
        private = Ed25519PrivateKey.generate()
        raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        identity = {"algorithm": "ed25519", "encoding": "base64url", "key_id": "key-1",
                    "public_key": base64.urlsafe_b64encode(raw).decode().rstrip("="),
                    "fingerprint": "sha256:" + hashlib.sha256(raw).hexdigest()}
        now = int(time.time())
        status = {"schema_version": 1, "server_id": "signed", "active_key_id": "key-1",
                  "revoked_key_ids": [], "issued_at": now, "expires_at": now + 3600}
        client = DeveloperServerClient("https://private.example")
        with patch.object(client, "_request", side_effect=[
            {"protocol_version": 1, "server_id": "signed", "name": "Signed", "signing_identity": identity},
            {"status": status, "signature": sign_detached(status, private, key_id="key-1")},
        ]):
            self.assertEqual(client.key_status()["active_key_id"], "key-1")
        revoked = {**status, "revoked_key_ids": ["key-1"]}
        client = DeveloperServerClient("https://private.example")
        with patch.object(client, "_request", side_effect=[
            {"protocol_version": 1, "server_id": "signed", "name": "Signed", "signing_identity": identity},
            {"status": revoked, "signature": sign_detached(revoked, private, key_id="key-1")},
        ]):
            with self.assertRaisesRegex(ValueError, "revoked"):
                client.key_status()
    def test_signed_private_manifest_is_verified_and_tampering_rejected(self):
        private = Ed25519PrivateKey.generate()
        raw = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        manifest = private_manifest("private.signed").wire
        manifest["signature"] = sign_detached(manifest, private, key_id="key-1")
        client = DeveloperServerClient("https://private.example")
        with patch.object(client, "_request", side_effect=[
            {"protocol_version": 1, "server_id": "signed", "name": "Signed", "signing_identity": {
                "algorithm": "ed25519", "encoding": "base64url", "key_id": "key-1",
                "public_key": base64.urlsafe_b64encode(raw).decode().rstrip("="),
                "fingerprint": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }},
            {"packages": [manifest]},
        ]):
            client.info()
            self.assertEqual(client.packages("123")[0].id, "private.signed")
        manifest["version"] = "9.9.9"
        client = DeveloperServerClient("https://private.example")
        with patch.object(client, "_request", side_effect=[
            {"protocol_version": 1, "server_id": "signed", "name": "Signed", "signing_identity": {
                "algorithm": "ed25519", "encoding": "base64url", "key_id": "key-1",
                "public_key": base64.urlsafe_b64encode(raw).decode().rstrip("="),
                "fingerprint": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }},
            {"packages": [manifest]},
        ]):
            client.info()
            with self.assertRaisesRegex(ValueError, "verification failed"):
                client.packages("123")
    def test_gist_sync_merges_and_writes_private_server_index(self):
        calls = []
        remote = [{"id": "g1", "files": {GITHUB_GIST_FILENAME: {"content": json.dumps({"schema_version": 1, "servers": [{"server_id": "remote", "url": "https://remote.example", "name": "Remote"}]})}}}]
        def fake(method, url, token, payload=None, **_kwargs):
            calls.append((method, url, payload))
            if method == "GET" and url.endswith("/gists?per_page=100"):
                return remote
            if method == "POST":
                return {"id": "g1"}
            return {"id": "g1"}
        with patch("sprocket_mod_manager.infrastructure.private_servers.github_sync._github_json_request", side_effect=fake):
            gist_id, merged = github_gist_sync("token", [{
                "server_id": "local",
                "url": "https://local.example",
                "name": "Local",
                "operator": "must-not-sync",
                "session_token": "server-secret",
                "session_credential": "credential-target",
            }])
        self.assertEqual(gist_id, "g1")
        self.assertEqual({item["server_id"] for item in merged}, {"local", "remote"})
        self.assertEqual(calls[-1][0], "PATCH")
        self.assertIn(GITHUB_GIST_FILENAME, calls[-1][2]["files"])
        content = calls[-1][2]["files"][GITHUB_GIST_FILENAME]["content"]
        self.assertNotIn("session_token", content)
        self.assertNotIn("session_credential", content)
        self.assertNotIn("server-secret", content)
        self.assertNotIn("credential-target", content)
        self.assertNotIn("must-not-sync", content)
        self.assertNotIn("download_url", content)

    def test_gist_sync_reports_identity_conflict_and_preserves_local_entry(self):
        calls = []
        remote = [{"id": "g1", "files": {GITHUB_GIST_FILENAME: {"content": json.dumps({
            "schema_version": 1,
            "servers": [{
                "server_id": "local",
                "url": "https://attacker.example",
                "name": "Changed",
                "public_key_fingerprint": "sha256:remote",
                "updated_at": 200,
            }],
        })}}}]

        def fake(method, url, _token, payload=None, **_kwargs):
            calls.append((method, payload))
            if method == "GET" and url.endswith("/gists?per_page=100"):
                return remote
            return {"id": "g1"}

        with patch("sprocket_mod_manager.infrastructure.private_servers.github_sync._github_json_request", side_effect=fake):
            gist_id, merged, conflicts = github_gist_sync(
                "token",
                [{
                    "server_id": "local",
                    "url": "https://trusted.example",
                    "name": "Trusted",
                    "public_key_fingerprint": "sha256:local",
                    "updated_at": 100,
                }],
                return_conflicts=True,
            )

        self.assertEqual(gist_id, "g1")
        self.assertEqual(merged[0]["url"], "https://trusted.example")
        self.assertEqual(conflicts[0]["server_id"], "local")
        written = calls[-1][1]["files"][GITHUB_GIST_FILENAME]["content"]
        self.assertIn("trusted.example", written)
        self.assertNotIn("attacker.example", written)

    def test_deleted_gist_server_is_not_loaded_as_active_server(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                api.config["developer_servers"] = [{
                    "server_id": "deleted",
                    "url": "https://deleted.example",
                    "deleted": True,
                }]
                self.assertEqual(api._developer_server_entries(), [])
            finally:
                api.install_queue.close()

    def test_remove_developer_server_writes_a_tombstone_for_gist_sync(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                api.config["developer_servers"] = [{
                    "server_id": "remove-me",
                    "url": "https://remove.example",
                    "name": "Remove Me",
                }]
                api.config_store.save(api.config)
                with patch.object(api._private_controller, "sync_github_gist") as sync:
                    result = api.remove_developer_server("remove-me")
                self.assertTrue(result["ok"])
                self.assertEqual(api._developer_server_entries(), [])
                stored = api.config["developer_servers"]
                self.assertEqual(len(stored), 1)
                self.assertTrue(stored[0]["deleted"])
                self.assertGreater(stored[0]["updated_at"], 0)
                sync.assert_not_called()
            finally:
                api.install_queue.close()

    def test_gist_sync_preserves_local_tombstones(self):
        calls = []

        def fake(method, url, _token, payload=None, **_kwargs):
            calls.append((method, payload))
            if method == "GET" and url.endswith("/gists?per_page=100"):
                return []
            return {"id": "g1"}

        with patch("sprocket_mod_manager.infrastructure.private_servers.github_sync._github_json_request", side_effect=fake):
            gist_id, merged = github_gist_sync("token", [{
                "server_id": "deleted",
                "url": "https://deleted.example",
                "name": "Deleted",
                "updated_at": 123,
                "deleted": True,
            }])

        self.assertEqual(gist_id, "g1")
        self.assertEqual(merged[0]["server_id"], "deleted")
        self.assertTrue(merged[0]["deleted"])

    def test_two_device_gist_simulation_keeps_newer_deletion(self):
        gist = {
            "servers": [{
                "server_id": "shared",
                "url": "https://shared.example",
                "name": "Shared",
                "public_key_fingerprint": "sha256:shared",
                "updated_at": 100,
            }],
        }

        def fake(method, url, _token, payload=None, **_kwargs):
            if method == "GET" and url.endswith("/gists?per_page=100"):
                return [{"id": "g1", "files": {GITHUB_GIST_FILENAME: {"content": json.dumps(gist)}}}]
            if method == "GET" and url.endswith("/gists/g1"):
                return {"id": "g1", "files": {GITHUB_GIST_FILENAME: {"content": json.dumps(gist)}}}
            if method == "PATCH":
                content = payload["files"][GITHUB_GIST_FILENAME]["content"]
                gist.clear()
                gist.update(json.loads(content))
                return {"id": "g1"}
            raise AssertionError(f"unexpected GitHub request: {method} {url}")

        deleted = {
            "server_id": "shared",
            "url": "https://shared.example",
            "name": "Shared",
            "public_key_fingerprint": "sha256:shared",
            "updated_at": 200,
            "deleted": True,
        }
        active_old = {**deleted, "updated_at": 100}
        active_old.pop("deleted")
        with patch("sprocket_mod_manager.infrastructure.private_servers.github_sync._github_json_request", side_effect=fake):
            github_gist_sync("token", [deleted], "g1")
            _gist_id, merged = github_gist_sync("token", [active_old], "g1")

        self.assertTrue(merged[0]["deleted"])
        self.assertEqual(merged[0]["updated_at"], 200)

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def test_only_https_or_loopback_http_servers_are_allowed(self):
        self.assertEqual(normalize_server_url(self.url + "/"), self.url)
        self.assertEqual(normalize_server_url("https://beta.example.com"), "https://beta.example.com")
        with self.assertRaises(ValueError):
            normalize_server_url("http://beta.example.com")

    def test_client_reads_identity_redeems_and_lists_packages(self):
        client = DeveloperServerClient(self.url)
        self.assertEqual(client.info().server_id, "test-server")
        DemoHandler.idempotency_keys = []
        self.assertEqual(client.redeem("TEST-KEY", "123")["permissions"], ["download:private.mod"])
        self.assertEqual(client.redeem("TEST-KEY", "123")["permissions"], ["download:private.mod"])
        self.assertTrue(DemoHandler.last_idempotency_key)
        self.assertNotEqual(DemoHandler.idempotency_keys[0], DemoHandler.idempotency_keys[1])
        self.assertNotIn("TEST-KEY", DemoHandler.last_idempotency_key)
        self.assertEqual(client.entitlements("123")["server_time"], 1787052553)
        self.assertEqual(client.packages("123")[0].id, "private.mod")

    def test_private_manifest_rejects_invalid_identity_version_and_download_metadata(self):
        valid = {
            "schema_version": 1,
            "id": "private.mod",
            "name": "PrivateMod",
            "authors": ["Tester"],
            "license": "Private distribution",
            "display_name": {"en": "Private Mod"},
            "description": {"en": "test"},
            "dependencies": [],
            "install": {"scan_dlls": True, "exclude": [], "overrides": []},
            "category": "other",
            "tags": [],
            "recommendations": [],
            "files": [{
                "name": "PrivateMod.dll",
                "target": "Mods/PrivateMod.dll",
                "sha256": "a" * 64,
            }],
            "releases": [{
                "id": 1, "tag": "1.0.0", "version": "1.0.0",
                "prerelease": False, "published_at": "", "assets": [{
                    "name": "PrivateMod.zip", "size": 123, "digest": "sha256:" + "b" * 64,
                    "download_path": "/v1/packages/private.mod/download",
                }],
            }],
        }
        self.assertEqual(PrivatePackageManifest.from_dict(valid).version, "1.0.0")
        for update in (
            {"id": "Private Mod"},
            {"releases": [{**valid["releases"][0], "version": "1.0"}]},
            {"files": [{**valid["files"][0], "target": "../PrivateMod.dll"}]},
            {"releases": [{**valid["releases"][0], "assets": [{**valid["releases"][0]["assets"][0], "name": "PrivateMod.smod"}]}]},
            {"releases": [{**valid["releases"][0], "assets": [{**valid["releases"][0]["assets"][0], "digest": "md5:bad"}]}]},
            {"releases": [{**valid["releases"][0], "assets": [{**valid["releases"][0]["assets"][0], "download_path": "https://other.invalid/mod.zip"}]}]},
        ):
            candidate = {**valid, **update}
            with self.subTest(update=update), self.assertRaises(ValueError):
                PrivatePackageManifest.from_dict(candidate)

    def test_private_catalog_cache_is_scoped_to_github_user(self):
        manifest = PrivatePackageManifest.from_dict({
            "schema_version": 1,
            "id": "private.mod",
            "name": "PrivateMod",
            "authors": ["Tester"], "license": "Private distribution",
            "display_name": {"en": "Private Mod"}, "description": {"en": "test"},
            "dependencies": [], "install": {"scan_dlls": True, "exclude": [], "overrides": []},
            "category": "other", "tags": [], "recommendations": [],
            "files": [],
            "releases": [],
        })
        with TemporaryDirectory() as directory:
            cache = PrivateCatalogCache(Path(directory))
            cache.save(
                "test-server",
                "123",
                (manifest,),
                {"permissions": ["download:private.mod"], "grants": []},
                synced_at=1787052553,
            )
            snapshot = cache.load("test-server", "123")
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.synced_at, 1787052553)
            self.assertEqual(snapshot.packages[0].id, "private.mod")
            self.assertIsNone(cache.load("test-server", "456"))

    def test_private_catalog_cache_does_not_persist_download_paths_or_tokens(self):
        manifest = private_manifest("private.cached")
        with TemporaryDirectory() as directory:
            cache = PrivateCatalogCache(Path(directory))
            cache.save(
                "test-server",
                "123",
                (manifest,),
                {
                    "permissions": ["download:private.cached"],
                    "grants": [{"id": 1, "state": "active", "session_token": "grant-secret"}],
                    "session_token": "entitlement-secret",
                    "download_url": "https://secret.example/download",
                },
                synced_at=1787052553,
            )
            persisted = cache.path.read_text(encoding="utf-8")
            snapshot = cache.load("test-server", "123")

        self.assertNotIn("download_path", persisted)
        self.assertNotIn("/download", persisted)
        self.assertNotIn("session_token", persisted)
        self.assertNotIn("entitlement-secret", persisted)
        self.assertNotIn("grant-secret", persisted)
        self.assertNotIn("secret.example", persisted)
        self.assertIsNotNone(snapshot)
        self.assertIsNone(snapshot.packages[0].archive)

    def test_private_catalog_cache_persists_signed_identity_and_key_status(self):
        manifest = private_manifest("private.cached-status")
        identity = {
            "algorithm": "ed25519", "encoding": "base64url", "key_id": "key-1",
            "public_key": "public-key", "fingerprint": "sha256:fingerprint",
        }
        status = {
            "schema_version": 1, "server_id": "test-server", "active_key_id": "key-1",
            "revoked_key_ids": [], "issued_at": 1000, "expires_at": 1100,
        }
        with TemporaryDirectory() as directory:
            cache = PrivateCatalogCache(Path(directory))
            cache.save(
                "test-server", "123", (manifest,), {"permissions": [], "grants": []},
                key_status={"status": status, "signature": {"signature": "detached"}},
                signing_identity=identity,
            )
            snapshot = cache.load("test-server", "123")
            persisted = cache.path.read_text(encoding="utf-8")
        self.assertEqual(snapshot.key_status["status"]["active_key_id"], "key-1")
        self.assertEqual(snapshot.signing_identity["fingerprint"], "sha256:fingerprint")
        self.assertNotIn("download_path", persisted)

    def test_client_downloads_authorized_private_archive(self):
        DemoHandler.package_archive = b"private archive"
        try:
            client = DeveloperServerClient(self.url)
            with TemporaryDirectory() as directory:
                destination = Path(directory) / "PrivateMod.zip"
                client.download_archive(
                    "/v1/packages/private.mod/download",
                    "123",
                    destination,
                    version="1.0.0",
                    expected_size=len(DemoHandler.package_archive),
                )
                self.assertEqual(destination.read_bytes(), DemoHandler.package_archive)
        finally:
            DemoHandler.package_archive = b""

    def test_private_download_authorization_failure_cleans_partial_file_and_can_retry(self):
        DemoHandler.package_archive = b"private archive"
        DemoHandler.download_status = 401
        try:
            client = DeveloperServerClient(self.url)
            with TemporaryDirectory() as directory:
                root = Path(directory)
                destination = root / "PrivateMod.zip"
                with self.assertRaisesRegex(DeveloperServerError, "authorization expired") as raised:
                    client.download_archive(
                        "/v1/packages/private.mod/download",
                        "123",
                        destination,
                        version="1.0.0",
                        expected_size=len(DemoHandler.package_archive),
                    )
                self.assertEqual(raised.exception.code, "download_unauthorized")
                self.assertFalse(destination.exists())
                self.assertEqual(list(root.iterdir()), [])

                DemoHandler.download_status = 200
                client.download_archive(
                    "/v1/packages/private.mod/download",
                    "123",
                    destination,
                    version="1.0.0",
                    expected_size=len(DemoHandler.package_archive),
                )
                self.assertEqual(destination.read_bytes(), DemoHandler.package_archive)
        finally:
            DemoHandler.download_status = 200
            DemoHandler.package_archive = b""

    def test_private_resolution_supports_public_and_same_server_dependencies(self):
        manifests = (
            private_manifest("private.root", dependencies=(
                {"id": "public.dep", "version": ">=1.0.0 <2.0.0"},
                {"id": "private.dep", "version": ">=1.0.0 <2.0.0"},
            )),
            private_manifest("private.dep"),
        )
        public = public_package("public.dep")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").touch()
            api = ClientApi("test", app_dir=root / "app")
            try:
                api.config["game_path"] = str(game)
                api.config["github_user_id"] = "123"
                api.config["developer_servers"] = [{
                    "server_id": "server-a",
                    "url": "https://server-a.invalid",
                }]
                api.service.registry = Registry([public])
                with patch.object(DeveloperServerClient, "packages", return_value=manifests):
                    _client, plan, downloaders = api._private_resolution("server-a:private.root")
                    planned = api.plan_install(["server-a:private.root"])
            finally:
                api.install_queue.close()

        self.assertEqual(
            [item.package.id for item in plan.packages],
            ["public.dep", "server-a:private.dep", "server-a:private.root"],
        )
        self.assertEqual(set(downloaders), {"server-a:private.root", "server-a:private.dep"})
        self.assertTrue(planned["ok"])
        self.assertEqual(
            [item["id"] for item in planned["plans"][0]["packages"]],
            ["public.dep", "server-a:private.dep", "server-a:private.root"],
        )

    def test_private_resolution_reports_mixed_dependency_conflict(self):
        manifests = (
            private_manifest("private.root", dependencies=(
                {"id": "public.dep", "version": ">=2.0.0 <3.0.0"},
            )),
        )
        public = public_package("public.dep", "1.0.0")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").touch()
            api = ClientApi("test", app_dir=root / "app")
            try:
                api.config["game_path"] = str(game)
                api.config["github_user_id"] = "123"
                api.config["developer_servers"] = [{
                    "server_id": "server-a",
                    "url": "https://server-a.invalid",
                }]
                api.service.registry = Registry([public])
                with patch.object(DeveloperServerClient, "packages", return_value=manifests):
                    result = api.plan_install(["server-a:private.root"])
            finally:
                api.install_queue.close()

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "install_plan_failed")
        self.assertIn("no compatible release set", result["message"])

    def test_private_plan_reports_missing_assets_and_expired_access(self):
        missing_asset = private_manifest("private.root", archive=b"")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").touch()
            api = ClientApi("test", app_dir=root / "app")
            try:
                api.config["game_path"] = str(game)
                api.config["github_user_id"] = "123"
                api.config["developer_servers"] = [{
                    "server_id": "server-a",
                    "url": "https://server-a.invalid",
                }]
                api.service.registry = Registry([])
                with patch.object(DeveloperServerClient, "packages", return_value=(missing_asset,)):
                    no_assets = api.plan_install(["server-a:private.root"])
                with patch.object(DeveloperServerClient, "packages", return_value=()):
                    expired = api.plan_install(["server-a:private.root"])
            finally:
                api.install_queue.close()

        self.assertFalse(no_assets["ok"])
        self.assertIn("no compatible release set", no_assets["message"])
        self.assertFalse(expired["ok"])
        self.assertIn("unavailable or permission has expired", expired["message"])

    def test_same_private_package_id_is_isolated_by_server_namespace(self):
        manifest = private_manifest("private.shared")
        packages = [
            manifest.namespaced_package("server-a", {manifest.id}),
            manifest.namespaced_package("server-b", {manifest.id}),
        ]
        registry = Registry(packages)
        github = SimpleNamespace(
            releases=lambda package: package.releases or (),
            install_assets=lambda _package, release: release.assets,
        )
        plan_a = DependencySolver(registry, github).resolve("server-a:private.shared")
        plan_b = DependencySolver(registry, github).resolve("server-b:private.shared")

        self.assertEqual(registry.get("server-a:private.shared").id, "server-a:private.shared")
        self.assertEqual(registry.get("server-b:private.shared").id, "server-b:private.shared")
        self.assertEqual([item.package.id for item in plan_a.packages], ["server-a:private.shared"])
        self.assertEqual([item.package.id for item in plan_b.packages], ["server-b:private.shared"])

    def test_webview_api_registers_activates_and_removes_server(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                added = api.add_developer_server(self.url)
                self.assertTrue(added["ok"])
                self.assertTrue(api.set_demo_github_login("123")["ok"])
                activated = api.activate_developer_server("test-server", "TEST-KEY")
                self.assertTrue(activated["ok"])
                api.get_developer_servers()
                servers = server_reading(api)
                self.assertEqual(servers["packages"][0]["server_name"], "Test Server")
                self.assertTrue(servers["packages"][0]["private"])
                removed = api.remove_developer_server("test-server")
                self.assertTrue(removed["ok"])
            finally:
                api.install_queue.close()

    def test_the_private_server_reading_goes_into_the_data_layer(self):
        """开发者服务器那份读数进数据层：`get_developer_servers` 只回 ack，读数由 `servers` 那个 key 持有。"""
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                self.assertTrue(api.add_developer_server(self.url)["ok"])
                self.assertTrue(api.set_demo_github_login("123")["ok"])
                self.assertTrue(api.activate_developer_server("test-server", "TEST-KEY")["ok"])
                ack = api.get_developer_servers()
                reading = server_reading(api)
            finally:
                api.install_queue.close()

        self.assertTrue(ack["ok"], ack)
        for key in ("servers", "packages", "adopted", "github_user_id", "github_login_expired"):
            self.assertNotIn(key, ack, f"读数不该在返回值里：{key}")
        self.assertEqual(reading["servers"][0]["server_id"], "test-server")
        self.assertEqual(reading["packages"][0]["server_name"], "Test Server")

    def test_server_added_after_global_github_login_receives_its_own_session(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                api._github_access_token = "gho-temporary"
                with patch.object(
                    DeveloperServerClient,
                    "exchange_github_token",
                    return_value={"token": "server-session", "github_user_id": "42"},
                ):
                    result = api.add_developer_server(self.url)
                self.assertTrue(result["ok"])
                entry = api.config["developer_servers"][0]
                self.assertTrue(entry["session_credential"])
                self.assertEqual(api._server_session_token(entry), "server-session")
                self.assertEqual(api.config["github_user_id"], "42")
            finally:
                api.install_queue.close()

    def test_add_server_clears_expired_github_login(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                api._github_access_token = "expired"
                api.config["github_user_id"] = "42"
                api.credentials.save("github-access-token", "expired")
                with patch.object(
                    DeveloperServerClient,
                    "exchange_github_token",
                    side_effect=DeveloperServerError(
                        "GitHub rejected the access token", status=401, code="github_token_rejected"
                    ),
                ):
                    result = api.add_developer_server(self.url)
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], "github_login_expired")
                self.assertEqual(api.config["github_user_id"], "")
                self.assertEqual(api._github_token(), "")
            finally:
                api.install_queue.close()

    def test_bootstrap_syncs_gist_from_saved_github_credential(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                api.credentials.save("github-access-token", "saved-token")
                class ImmediateThread:
                    def __init__(self, *, target, **_kwargs):
                        self.target = target

                    def start(self):
                        self.target()

                with patch("sprocket_mod_manager.presentation.controllers.settings_controller.threading.Thread", ImmediateThread), \
                     patch.object(api._private_controller, "_verify_github_login",
                                  return_value={"ok": True, "logged_in": True}), \
                     patch.object(api._private_controller, "sync_github_gist", return_value={"ok": True}) as sync, \
                     patch.object(api._private_controller, "_reconnect_developer_servers",
                                  return_value=[]) as reconnect:
                    result = api.bootstrap()
                self.assertTrue(result["ok"])
                sync.assert_called_once_with()
                reconnect.assert_called_once_with()
            finally:
                api.install_queue.close()

    def test_bootstrap_clears_expired_saved_github_login(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                api.config["github_user_id"] = "42"
                api.config_store.save(api.config)
                api.credentials.save("github-access-token", "expired-token")

                class ImmediateThread:
                    def __init__(self, *, target, **_kwargs):
                        self.target = target

                    def start(self):
                        self.target()

                with patch("sprocket_mod_manager.presentation.controllers.settings_controller.threading.Thread", ImmediateThread), \
                     patch("sprocket_mod_manager.presentation.controllers.private_distribution_controller.github_current_user",
                           side_effect=ValueError("GitHub access token is invalid")), \
                     patch.object(api._private_controller, "sync_github_gist") as sync:
                    result = api.bootstrap()
                self.assertTrue(result["ok"])
                self.assertEqual(api.config["github_user_id"], "")
                self.assertEqual(api._github_token(), "")
                sync.assert_not_called()
            finally:
                api.install_queue.close()

    def test_github_login_reconnects_servers_when_gist_sync_fails(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                api.config["developer_servers"] = [{
                    "server_id": "test-server", "url": self.url,
                }]
                api._github_device = {"client_id": "client", "device_code": "device", "interval": 5}
                with patch("sprocket_mod_manager.presentation.controllers.private_distribution_controller.github_device_poll",
                           return_value={"access_token": "token"}), \
                     patch("sprocket_mod_manager.presentation.controllers.private_distribution_controller.github_current_user",
                           return_value={"id": 42}), \
                     patch.object(api._private_controller, "sync_github_gist", return_value={"ok": False}), \
                     patch.object(DeveloperServerClient, "exchange_github_token",
                                  return_value={"token": "server-session"}):
                    result = api.poll_github_device_login()
                self.assertTrue(result["ok"])
                self.assertEqual(result["reconnected_server_ids"], ["test-server"])
                self.assertEqual(api._server_session_token({"server_id": "test-server"}), "server-session")
            finally:
                api.install_queue.close()

    def test_server_refresh_clears_expired_github_login(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                api.config["github_user_id"] = "42"
                api.config_store.save(api.config)
                api.credentials.save("github-access-token", "expired-token")
                with patch("sprocket_mod_manager.presentation.controllers.private_distribution_controller.github_current_user",
                           side_effect=ValueError("GitHub access token is invalid")):
                    result = api.get_developer_servers()
                    reading = server_reading(api)
                self.assertTrue(result["ok"])
                self.assertTrue(reading["github_login_expired"])
                self.assertEqual(reading["github_user_id"], "")
                self.assertEqual(api._github_token(), "")
            finally:
                api.install_queue.close()

    def test_github_login_identity_survives_offline_registered_server(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                api.config["developer_servers"] = [{"server_id": "offline", "url": "https://offline.example"}]
                api._github_device = {"client_id": "client", "device_code": "device", "interval": 5}
                with patch("sprocket_mod_manager.presentation.controllers.private_distribution_controller.github_device_poll", return_value={"access_token": "token"}), \
                     patch("sprocket_mod_manager.presentation.controllers.private_distribution_controller.github_current_user", return_value={"id": 42}), \
                     patch.object(DeveloperServerClient, "exchange_github_token", side_effect=ValueError("offline")), \
                     patch.object(api._private_controller, "sync_github_gist", return_value={"ok": False}):
                    result = api.poll_github_device_login()
                self.assertTrue(result["ok"])
                self.assertEqual(result["github_user_id"], "42")
                self.assertEqual(api.config["github_user_id"], "42")
            finally:
                api.install_queue.close()

    def test_github_login_returns_gist_conflicts_for_webview_review(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                api._github_device = {"client_id": "client", "device_code": "device", "interval": 5}
                conflict = {
                    "server_id": "server-a",
                    "local": {"url": "https://local.example"},
                    "remote": {"url": "https://remote.example"},
                }
                with patch("sprocket_mod_manager.presentation.controllers.private_distribution_controller.github_device_poll", return_value={"access_token": "token"}), \
                     patch("sprocket_mod_manager.presentation.controllers.private_distribution_controller.github_current_user", return_value={"id": 42}), \
                     patch.object(api._private_controller, "sync_github_gist", return_value={"ok": True, "conflicts": [conflict]}):
                    result = api.poll_github_device_login()
                self.assertEqual(result["conflicts"], [conflict])
            finally:
                api.install_queue.close()

    def test_logout_clears_global_identity_and_server_credentials(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                api.config["github_user_id"] = "42"
                api.config["developer_servers"] = [{"server_id": "test-server", "url": self.url}]
                api.config_store.save(api.config)
                api.credentials.save("test-server", "server-session")
                result = api.logout_github()
                self.assertTrue(result["ok"])
                self.assertEqual(api.config["github_user_id"], "")
                self.assertEqual(api._server_session_token({"server_id": "test-server"}), "")
            finally:
                api.install_queue.close()

    def test_webview_api_uses_read_only_cache_when_server_is_offline(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").touch()
            api = ClientApi("test", app_dir=root / "app")
            try:
                api.config["game_path"] = str(game)
                api.config_store.save(api.config)
                self.assertTrue(api.add_developer_server(self.url)["ok"])
                self.assertTrue(api.set_demo_github_login("123")["ok"])
                self.assertTrue(api.get_developer_servers()["ok"])
                with patch.object(DeveloperServerClient, "entitlements", side_effect=ValueError("offline")):
                    cached = api.get_developer_servers()
                    reading = server_reading(api)
                self.assertTrue(cached["ok"])
                self.assertEqual(reading["servers"][0]["status"], "offline")
                self.assertTrue(reading["servers"][0]["cached"])
                self.assertTrue(reading["packages"][0]["cached"])
                self.assertEqual(reading["adopted"], [])
                with patch.object(DeveloperServerClient, "packages", side_effect=ValueError("offline")):
                    planned = api.plan_install(["test-server:private.mod"])
                self.assertFalse(planned["ok"])
            finally:
                api.install_queue.close()

    def test_expired_server_session_is_reported_as_reauthentication_required(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                self.assertTrue(api.add_developer_server(self.url)["ok"])
                api.config["github_user_id"] = "123"
                api.config_store.save(api.config)
                api.credentials.save("test-server", "expired-session")
                with patch.object(
                    DeveloperServerClient,
                    "entitlements",
                    side_effect=DeveloperServerError(
                        "localized session failure",
                        status=401,
                        code="invalid_session",
                    ),
                ):
                    api.get_developer_servers()
                    reading = server_reading(api)
                self.assertEqual(reading["servers"][0]["status"], "reauth_required")
            finally:
                api.install_queue.close()

    def test_save_settings_preserves_registered_servers(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("test", app_dir=Path(directory))
            try:
                self.assertTrue(api.add_developer_server(self.url)["ok"])
                self.assertTrue(api.set_demo_github_login("123")["ok"])
                result = api.save_settings({"language": "en", "text_scale": 100})
                self.assertEqual(result["settings"]["developer_servers"][0]["server_id"], "test-server")
                self.assertEqual(result["settings"]["github_user_id"], "123")
            finally:
                api.install_queue.close()

    def test_manager_ui_exposes_server_activation_and_private_catalog_section(self):
        ui_root = Path(__file__).resolve().parents[1] / "sprocket_mod_manager" / "presentation" / "client_ui"
        html = (ui_root / "index.html").read_text(encoding="utf-8")
        javascript = client_javascript(ui_root)
        self.assertIn('id="developer-server-list"', html)
        self.assertIn('id="add-developer-server"', html)
        self.assertIn('id="github-login-button"', html)
        self.assertNotIn('id="global-github-user-id"', html)
        self.assertLess(html.index('id="github-login-button"'), html.index('id="developer-server-url"'))
        self.assertIn('callApi("activate_developer_server"', javascript)
        self.assertIn('"resolve_github_gist_conflicts"', javascript)
        self.assertIn("reviewGistConflicts", javascript)
        self.assertIn("const privateRefresh = loadDeveloperServers();", javascript)
        self.assertIn("await privateRefresh;", javascript)
        self.assertIn('header.className = "private-section-header"', javascript)
        self.assertIn('`${tr("privateLabel")} · ${pkg.server_name}`', javascript)
        self.assertIn("if (pkg.cached) return", javascript)
        self.assertIn("!(pkg.install_assets || []).length", javascript)
        self.assertIn("!pkg.cached", javascript)
        self.assertIn('entry.state === "failed"', javascript)
        self.assertIn('callApi("enqueue_install", [entry.package_id], true)', javascript)
        self.assertNotIn('row.classList.toggle("private-package"', javascript)
        self.assertNotIn('metadata.classList.toggle("private-source"', javascript)
        self.assertNotIn('className = "developer-server-activation"', javascript)
        self.assertNotIn('server.status}${user}', javascript)

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_private_package_with_matching_manifest_is_adopted(self, _running):
        content = b"private release dll"
        digest = hashlib.sha256(content).hexdigest()
        DemoHandler.package_files = [{
            "name": "PrivateMod.dll",
            "target": "Mods/PrivateMod.dll",
            "sha256": digest,
        }]
        try:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                app = root / "app"
                game = root / "game"
                target = game / "Mods" / "PrivateMod.dll"
                target.parent.mkdir(parents=True)
                (game / "Sprocket.exe").touch()
                target.write_bytes(content)
                api = ClientApi("test", app_dir=app)
                try:
                    api.config["game_path"] = str(game)
                    api.config_store.save(api.config)
                    self.assertTrue(api.add_developer_server(self.url)["ok"])
                    self.assertTrue(api.set_demo_github_login("123")["ok"])
                    api.get_developer_servers()
                    reading = server_reading(api)
                    package_id = "test-server:private.mod"
                    self.assertEqual(reading["adopted"][0]["id"], package_id)
                    self.assertNotIn("adopted", reading["packages"][0]["installed"])
                    self.assertEqual(api._installed_data()[0]["id"], package_id)
                    self.assertTrue(api.remove(package_id)["ok"])
                    self.assertFalse(target.exists())
                finally:
                    api.install_queue.close()
        finally:
            DemoHandler.package_files = []

    def test_ambiguous_private_manifests_are_not_adopted(self):
        content = b"shared private dll"
        digest = hashlib.sha256(content).hexdigest()
        manifest = [{
            "name": "Shared.dll",
            "target": "Mods/Shared.dll",
            "sha256": digest,
        }]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            target = game / "Mods" / "Shared.dll"
            target.parent.mkdir(parents=True)
            (game / "Sprocket.exe").touch()
            target.write_bytes(content)
            api = ClientApi("test", app_dir=root / "app")
            try:
                api.config["game_path"] = str(game)
                api.config_store.save(api.config)
                packages = [
                    {
                        "id": "server-a:private.mod",
                        "name": "Private A",
                        "server_url": "https://a.invalid",
                        "release": {"version": "1.0.0"},
                        "authors": ["A"],
                        "display_name": {"en": "Private A"},
                        "description": {"en": "test"},
                        "adoption_files": manifest,
                    },
                    {
                        "id": "server-b:private.mod",
                        "name": "Private B",
                        "server_url": "https://b.invalid",
                        "release": {"version": "1.0.0"},
                        "authors": ["B"],
                        "display_name": {"en": "Private B"},
                        "description": {"en": "test"},
                        "adoption_files": manifest,
                    },
                ]

                self.assertEqual(api._adopt_private_packages(packages), [])
                self.assertEqual(api._current_service().installed(game), {})
            finally:
                api.install_queue.close()

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_private_archive_is_verified_installed_and_removed(self, _running):
        dll = b"private archive dll"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "PrivateMod.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("PrivateMod.dll", dll)
                archive.writestr("README.txt", "ignored")
            DemoHandler.package_archive = archive_path.read_bytes()
            DemoHandler.package_files = [{
                "name": "PrivateMod.dll",
                "target": "Mods/PrivateMod.dll",
                "sha256": hashlib.sha256(dll).hexdigest(),
            }]
            try:
                game = root / "game"
                game.mkdir()
                (game / "Sprocket.exe").touch()
                api = ClientApi("test", app_dir=root / "app")
                try:
                    api.config["game_path"] = str(game)
                    api.config_store.save(api.config)
                    self.assertTrue(api.add_developer_server(self.url)["ok"])
                    self.assertTrue(api.set_demo_github_login("123")["ok"])
                    package_id = "test-server:private.mod"
                    planned = api.plan_install([package_id])
                    self.assertTrue(planned["ok"])
                    self.assertEqual(planned["plans"][0]["id"], package_id)
                    api._install_private_package(package_id, game, lambda _message: None, force_conflicts=False)
                    target = game / "Mods" / "PrivateMod.dll"
                    self.assertEqual(target.read_bytes(), dll)
                    DemoHandler.package_version = "1.1.0"
                    updated = api.update_all()
                    self.assertTrue(updated["ok"])
                    self.assertEqual(updated["count"], 1)
                    self.assertTrue(api.install_queue.wait_until_idle(2))
                    self.assertEqual(api._installed()[package_id]["version"], "1.1.0")
                    self.assertTrue(api.remove_developer_server("test-server")["ok"])
                    self.assertTrue(api.remove(package_id)["ok"])
                    self.assertFalse(target.exists())
                finally:
                    api.install_queue.close()
            finally:
                DemoHandler.package_files = []
                DemoHandler.package_archive = b""
                DemoHandler.package_version = "1.0.0"
                DemoHandler.package_install = {"scan_dlls": False, "exclude": [], "overrides": [{"match": "PrivateMod.dll", "target": "Mods"}]}


if __name__ == "__main__":
    unittest.main()
