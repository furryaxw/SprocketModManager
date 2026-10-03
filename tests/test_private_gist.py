"""恢复 Gist 的合并契约：它是恢复索引，不是认证或权限来源。

这里钉住四件事：只同步白名单字段、远端多出的会补进来、身份字段不一致只记冲突、
远端缺某条**不代表**本地要删。
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from sprocket_mod_manager.infrastructure.private_servers import (
    GITHUB_GIST_FILENAME,
    github_gist_sync,
    recovery_entry,
)

TOKEN = "github-token"
GIST_ID = "gist-123"
GIST_URL = f"https://api.github.com/gists/{GIST_ID}"


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = {"Content-Length": str(len(self._body))}

    def read(self, _limit: int | None = None) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class _GitHub:
    """按方法记录请求；GET 回远端文档，写回一律成功。"""

    def __init__(self, remote: dict | None) -> None:
        self.remote = remote
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, request, timeout: int = 0):  # noqa: ARG002 - 契约如此
        url = request.full_url
        method = request.get_method()
        payload = json.loads(request.data.decode("utf-8")) if request.data else None
        self.calls.append((method, url, payload))
        if method == "GET" and url == GIST_URL:
            content = (
                json.dumps(self.remote, ensure_ascii=False) if self.remote is not None else ""
            )
            return _Response({"files": {GITHUB_GIST_FILENAME: {"content": content}}})
        return _Response({"id": GIST_ID})

    @property
    def written(self) -> dict:
        for method, _url, payload in reversed(self.calls):
            if method in {"PATCH", "POST"} and payload:
                return payload
        raise AssertionError("nothing was written back")


def entry(server_id: str, *, name: str = "S", fingerprint: str = "sha256:aa", updated: int = 1) -> dict:
    return {
        "server_id": server_id,
        "url": "https://mods.example.invalid",
        "name": name,
        "public_key_fingerprint": fingerprint,
        "updated_at": updated,
    }


class RecoveryEntryTests(unittest.TestCase):
    def test_only_the_documented_fields_survive(self) -> None:
        result = recovery_entry({
            **entry("local-test"),
            "session_token": "secret-session",
            "github_access_token": "secret-token",
            "package_manifests": [{"id": "a.b"}],
        })

        self.assertNotIn("session_token", result)
        self.assertNotIn("github_access_token", result)
        self.assertNotIn("package_manifests", result)
        self.assertEqual(result["server_id"], "local-test")
        self.assertEqual(result["url"], "https://mods.example.invalid")

    def test_a_deleted_marker_is_carried_but_not_invented(self) -> None:
        self.assertIs(recovery_entry({**entry("a"), "deleted": True})["deleted"], True)
        self.assertNotIn("deleted", recovery_entry(entry("a")))


class GistSyncTests(unittest.TestCase):
    def sync(self, github: _GitHub, entries: list[dict], **kwargs):
        with patch(
            "sprocket_mod_manager.infrastructure.private_servers.github_gist.urlopen", github
        ):
            return github_gist_sync(TOKEN, entries, GIST_ID, return_conflicts=True, **kwargs)

    def test_a_remote_only_entry_is_added_locally(self) -> None:
        github = _GitHub({"schema_version": 1, "servers": [entry("from-other-machine")]})

        _gist_id, servers, conflicts = self.sync(github, [entry("local-one")])

        self.assertEqual([item["server_id"] for item in servers], ["from-other-machine", "local-one"])
        self.assertEqual(conflicts, [])

    def test_a_local_entry_missing_remotely_is_kept(self) -> None:
        """缺失是常态（比如另一台机器还没同步过），不是删除信号。"""
        github = _GitHub({"schema_version": 1, "servers": []})

        _gist_id, servers, conflicts = self.sync(github, [entry("kept-locally")])

        self.assertEqual([item["server_id"] for item in servers], ["kept-locally"])
        self.assertEqual(conflicts, [])

    def test_a_changed_identity_is_a_conflict_not_an_overwrite(self) -> None:
        github = _GitHub({
            "schema_version": 1,
            "servers": [entry("same-id", name="Renamed", fingerprint="sha256:bb", updated=99)],
        })

        _gist_id, servers, conflicts = self.sync(github, [entry("same-id")])

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["server_id"], "same-id")
        kept = next(item for item in servers if item["server_id"] == "same-id")
        self.assertEqual(
            kept["public_key_fingerprint"], "sha256:aa", "冲突时保留本地那份，等用户确认"
        )

    def test_a_newer_remote_entry_wins_when_the_identity_matches(self) -> None:
        github = _GitHub({
            "schema_version": 1,
            "servers": [entry("same-id", name="Newer", updated=99)],
        })

        _gist_id, servers, conflicts = self.sync(github, [entry("same-id", name="Newer", updated=1)])

        self.assertEqual(conflicts, [])
        self.assertEqual(servers[0]["updated_at"], 99)

    def test_the_written_gist_is_private_and_uses_the_fixed_name(self) -> None:
        github = _GitHub({"schema_version": 1, "servers": []})

        self.sync(github, [entry("local-one")])

        written = github.written
        self.assertIs(written["public"], False, "这个文件只该本人看得到")
        self.assertEqual(list(written["files"]), [GITHUB_GIST_FILENAME])
        content = json.loads(written["files"][GITHUB_GIST_FILENAME]["content"])
        self.assertEqual(content["schema_version"], 1)
        self.assertEqual([item["server_id"] for item in content["servers"]], ["local-one"])


if __name__ == "__main__":
    unittest.main()
