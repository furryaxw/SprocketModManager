"""开发者服务器的编排行为：指纹确认、软删除、登出不动服务器、Gist 冲突只采纳勾选的。

这里不测协议本身（那在 `test_private_package_index` / `test_private_download` / `test_private_gist`），
只测 controller 在几个关键岔路口怎么选。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.infrastructure.private_servers import (
    DeveloperServerError,
    DeveloperServerInfo,
)
from sprocket_mod_manager.presentation.controllers import developer_server_controller as module
from sprocket_mod_manager.presentation.web_gui import ClientApi

SERVER_ID = "test-server"
SERVER_NAME = "Test server"
FINGERPRINT = "sha256:" + "ab" * 32
# 一条服务器下发的私有条目：**只有 `releases`，没有 `release`**（抓取规则被剪掉了）。
PRIVATE_ENTRY = {
    "schema_version": 3,
    "id": "team1.private-mod",
    "name": "PrivateMod",
    "authors": ["team1"],
    "license": "Private distribution",
    # 真实私有包是**带 repository** 的：下载地址的校验会走「必须落在 github.com」那条，
    # 于是私有服务端的地址被判非法。fixture 里也写上一个，免得测出假绿。
    "repository": "team1/private-mod",
    "display_name": {"en": "Private Mod", "zh": "私有模组"},
    "description": {"en": "private", "zh": "私有"},
    "dependencies": [],
    "recommendations": [],
    "category": "other",
    "tags": [],
    "install": {"files": [{"match": "**", "type": "melonloader:mod", "layout": "tree"}]},
    "releases": [
        {
            "id": 1,
            "version": "1.0.0",
            "assets": [
                {
                    "id": 11,
                    "name": "mod.zip",
                    "size": 10,
                    "download_url": "https://mods.example.invalid/v1/packages/team1.private-mod/download",
                    "digest": "sha256:" + "ab" * 32,
                }
            ],
        }
    ],
}
IDENTITY = {
    "algorithm": "ed25519",
    "encoding": "base64url",
    "key_id": "test-key",
    "public_key": "ohEYTNhObmtqUMfMmdIs9f8ozc28j6eFSRHtFQJ7qzo",
    "fingerprint": FINGERPRINT,
}


def server_info(*, signed: bool = True) -> DeveloperServerInfo:
    return DeveloperServerInfo(
        server_id=SERVER_ID,
        name=SERVER_NAME,
        operator="",
        protocol_version=2,
        signing_identity=dict(IDENTITY) if signed else None,
    )


class FakeServerClient:
    """一台假服务器。`expired_sessions` 模拟服务端那边已经失效的会话；
    `rejected_tokens` / `forbidden_tokens` 模拟服务器把 GitHub 令牌送去 GitHub 之后被拒的那两种码。"""

    expired_sessions: set[str] = set()
    packages_result: list[dict] = []
    # GitHub 判这张访问令牌无效（401）；`github_token_rejected`。
    rejected_tokens: set[str] = set()
    # GitHub 限流或 scope 不足（403）；`github_token_forbidden`。
    forbidden_tokens: set[str] = set()

    def __init__(self, url, *, session_token="", trusted_signing_identity=None):
        self.url = url
        self.session_token = session_token
        self.trusted_signing_identity = trusted_signing_identity
        self.rotation_applied = False

    def info(self):
        return server_info()

    def exchange_github_token(self, access_token):
        if access_token in self.rejected_tokens:
            raise DeveloperServerError(
                "GitHub rejected the access token (check token validity and permissions)",
                status=401,
                code="github_token_rejected",
            )
        if access_token in self.forbidden_tokens:
            raise DeveloperServerError(
                "GitHub rejected the access token (check token validity and permissions)",
                status=403,
                code="github_token_forbidden",
            )
        return {"token": "session-token"}

    def packages(self):
        if self.session_token in self.expired_sessions:
            raise DeveloperServerError(
                "session is invalid or expired", status=401, code="invalid_session"
            )
        return [dict(item) for item in type(self).packages_result]

    def redeem(self, key, github_user_id, **kwargs):
        return {"team_id": "team1"}

    def accept_invitation(self, token, **kwargs):
        return {"team_id": "team1"}


def gist_stub(*args, **kwargs):
    """默认不同步：这些用例关心的是列表维护，不是 Gist 往返。"""
    return ("", [], [])


class DeveloperServerControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        FakeServerClient.expired_sessions = set()
        FakeServerClient.packages_result = []
        FakeServerClient.rejected_tokens = set()
        FakeServerClient.forbidden_tokens = set()
        self._patches = [
            patch.object(module, "DeveloperServerClient", FakeServerClient),
            patch.object(module, "github_gist_sync", gist_stub),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        self.api = ClientApi("test", app_dir=self.root)
        self.addCleanup(self.api.install_queue.close)

    def servers(self) -> list[dict]:
        return self.api.config_store.load().get("developer_servers") or []

    def credential_file(self, name: str) -> Path:
        return self.root / "credentials" / f"{name}.bin"

    # ---- add --------------------------------------------------------------

    def test_a_signed_server_asks_for_fingerprint_confirmation_first(self) -> None:
        """首次调用是**探测**：回确认请求，界面据此把指纹摆给用户核对。"""
        result = self.api.add_developer_server("https://mods.example.invalid")

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(result["signing_identity"]["fingerprint"], FINGERPRINT)
        self.assertEqual(result["server"]["name"], SERVER_NAME)
        self.assertEqual(self.servers(), [], "没确认就一个字都不该落库")

    def test_a_confirmed_fingerprint_admits_the_server(self) -> None:
        result = self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["server_id"], SERVER_ID)
        entries = self.servers()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["public_key_fingerprint"], FINGERPRINT)
        self.assertIn("signing_identity", entries[0])

    def test_adding_the_same_server_twice_updates_instead_of_duplicating(self) -> None:
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        result = self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)

        self.assertTrue(result["ok"], result)
        self.assertFalse(result["added"], "第二次是更新，不是新增")
        self.assertEqual(len(self.servers()), 1)

    def test_an_unsigned_server_needs_no_fingerprint(self) -> None:
        with patch.object(FakeServerClient, "info", return_value=server_info(signed=False)):
            result = self.api.add_developer_server("http://127.0.0.1:8787")

        self.assertTrue(result["ok"], result)
        self.assertNotIn("requires_confirmation", result)
        self.assertEqual(len(self.servers()), 1)
        self.assertNotIn("public_key_fingerprint", self.servers()[0])

    def test_the_servers_data_key_is_refreshable(self) -> None:
        """前端订阅了 `servers`：没有刷新器时数据层回 `unknown_key`，"开发者服务器"那块永远是空的。"""
        request = self.api.data_request("servers")

        self.assertTrue(request["ok"], request)
        self.assertNotEqual(request.get("code"), "unknown_key")

    def test_the_servers_reading_carries_the_registered_servers(self) -> None:
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)

        payload = self.api.servers_payload()

        self.assertEqual([item["server_id"] for item in payload["servers"]], [SERVER_ID])
        self.assertEqual(payload["github_user_id"], "")

    def test_a_server_that_cannot_be_reached_degrades_only_itself(self) -> None:
        """一台服务器离线不该让整个私有来源看起来是空的。"""
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api.config["github_user_id"] = "12345"
        self.api.config_store.save(self.api.config)

        class Offline(FakeServerClient):
            def packages(self):
                raise DeveloperServerError("cannot connect", unreachable=True)

        with patch.object(
            module.DeveloperServerController, "_connected_client",
            lambda self, entry: Offline("https://mods.example.invalid"),
        ):
            payload = self.api.servers_payload()

        self.assertEqual(len(payload["servers"]), 1)
        self.assertEqual(payload["servers"][0]["status"], "offline")
        self.assertEqual(payload["packages"], [])

    # ---- refresh ----------------------------------------------------------

    def test_refreshing_a_server_establishes_and_stores_a_session(self) -> None:
        """连服务器要能存下会话。凭据名一旦不合法，整条路会报 `invalid credential target`。"""
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api.config["github_user_id"] = "12345"
        self.api.config_store.save(self.api.config)
        self.api.credentials.save("github-access-token", "github-token")

        result = self.api.refresh_developer_server(SERVER_ID)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["packages"], 0)
        self.assertEqual(
            self.api.credentials.load(SERVER_ID), "session-token",
            "会话按裸 server_id 存；名字里带 / 会被凭据库直接拒",
        )

    def test_refreshing_an_unknown_server_fails(self) -> None:
        result = self.api.refresh_developer_server("nobody")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "developer_server_refresh_failed")

    def test_a_credential_name_may_not_contain_a_slash(self) -> None:
        """凭据库拒绝带 `/` 或 `\\` 的名字。

        会话名一旦加上 `developer-server/` 这种前缀，整条「连服务器」的路都会以
        `invalid credential target` 收场 —— 这条把那个约束钉在这里。
        """
        for name in ("developer-server/x", "a\\b"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "invalid credential target"):
                    self.api.credentials.save(name, "token")

    def test_refreshing_without_github_login_is_refused(self) -> None:
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)

        result = self.api.refresh_developer_server(SERVER_ID)

        self.assertFalse(result["ok"])
        self.assertIn("GitHub login is required", result["message"])

    def test_refresh_replaces_a_session_the_server_no_longer_accepts(self) -> None:
        """会话会在服务端过期；「刷新」的语义就是把过期的换掉，而不是拿旧的再撞一次。"""
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api.config["github_user_id"] = "12345"
        self.api.config_store.save(self.api.config)
        self.api.credentials.save("github-access-token", "github-token")
        self.api.credentials.save(SERVER_ID, "stale-session")
        FakeServerClient.expired_sessions = {"stale-session"}

        result = self.api.refresh_developer_server(SERVER_ID)

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            self.api.credentials.load(SERVER_ID), "session-token", "过期的会话要被换掉"
        )

    def test_the_servers_reading_reauths_an_expired_session(self) -> None:
        """读数不该因为一份过期会话就把整台服务器标成需要重新登录 —— GitHub 令牌还在。"""
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api.config["github_user_id"] = "12345"
        self.api.config_store.save(self.api.config)
        self.api.credentials.save("github-access-token", "github-token")
        self.api.credentials.save(SERVER_ID, "stale-session")
        FakeServerClient.expired_sessions = {"stale-session"}

        payload = self.api.servers_payload()

        self.assertEqual(payload["servers"][0]["status"], "active")
        self.assertEqual(self.api.credentials.load(SERVER_ID), "session-token")

    def test_a_dead_github_token_leaves_the_session_expired(self) -> None:
        """GitHub 令牌也没了，就只能如实报「需要重新登录」。"""
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api.config["github_user_id"] = "12345"
        self.api.config_store.save(self.api.config)
        self.api.credentials.save(SERVER_ID, "stale-session")
        FakeServerClient.expired_sessions = {"stale-session"}

        payload = self.api.servers_payload()

        self.assertEqual(payload["servers"][0]["status"], "reauth_required")

    def test_a_github_token_github_rejects_is_replaced_by_the_refresh_token(self) -> None:
        """服务器答 `github_token_rejected`：先拿刷新令牌换一张，再换会话。

        刷新令牌是一次性的 —— 换回来的那一张不落盘，下一次就没得换了。
        """
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api.config["github_user_id"] = "12345"
        self.api.config_store.save(self.api.config)
        self.api.credentials.save("github-access-token", "stale-github-token")
        self.api.credentials.save("github-refresh-token", "refresh-token")
        FakeServerClient.rejected_tokens = {"stale-github-token"}

        with patch.object(
                module, "github_token_refresh",
                return_value={
                    "access_token": "fresh-github-token",
                    "refresh_token": "next-refresh-token",
                },
        ) as renew:
            result = self.api.refresh_developer_server(SERVER_ID)

        self.assertTrue(result["ok"], result)
        renew.assert_called_once_with(module.GITHUB_OAUTH_CLIENT_ID, "refresh-token")
        self.assertEqual(self.api.credentials.load("github-access-token"), "fresh-github-token")
        self.assertEqual(
            self.api.credentials.load("github-refresh-token"), "next-refresh-token",
            "刷新令牌只能用一次：换回来的那张必须落盘",
        )
        self.assertEqual(self.api.credentials.load(SERVER_ID), "session-token")

    def test_a_dead_refresh_token_asks_for_a_new_login(self) -> None:
        """刷新令牌也换不出新的来：报「登录过期」，并把作废的两份凭据从本机撤掉。

        撤掉是重点：留着的话下一次还会拿同一张死令牌去撞服务器。
        """
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api.config["github_user_id"] = "12345"
        self.api.config_store.save(self.api.config)
        self.api.credentials.save("github-access-token", "stale-github-token")
        self.api.credentials.save("github-refresh-token", "dead-refresh-token")
        for name in ("github-access-token", "github-refresh-token"):
            self.assertTrue(self.credential_file(name).is_file(), f"{name} 该先落在盘上")
        FakeServerClient.rejected_tokens = {"stale-github-token"}

        with patch.object(
                module, "github_token_refresh",
                side_effect=module.GitHubLoginExpired("refresh token was rejected"),
        ):
            result = self.api.refresh_developer_server(SERVER_ID)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "github_login_expired", "界面按这个码说「重新登录」")
        self.assertFalse(self.credential_file("github-access-token").exists())
        self.assertFalse(self.credential_file("github-refresh-token").exists())
        self.assertEqual(self.api.config_store.load().get("github_user_id"), "")

    def test_a_rate_limited_github_token_is_kept(self) -> None:
        """403 是限流或 scope 不足，令牌本身可能还有效：不许像 401 那样把它删掉。"""
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api.config["github_user_id"] = "12345"
        self.api.config_store.save(self.api.config)
        self.api.credentials.save("github-access-token", "rate-limited-github-token")
        self.api.credentials.save("github-refresh-token", "refresh-token")
        FakeServerClient.forbidden_tokens = {"rate-limited-github-token"}

        result = self.api.refresh_developer_server(SERVER_ID)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "developer_server_refresh_failed")
        self.assertIn("GitHub rejected the access token", result["message"])
        self.assertEqual(self.api.credentials.load("github-access-token"), "rate-limited-github-token")
        self.assertEqual(self.api.credentials.load("github-refresh-token"), "refresh-token")

    def test_a_private_package_carries_an_installable_release(self) -> None:
        """界面判「有没有可装的版本」看 `release` 与 `install_assets`。

        服务器只下发 `releases`（抓取规则那条 `release` 被剪掉了）；原样递给界面，每一条都会
        显示成「无可用版本」——所以要让它们过一遍目录那套加工。
        """
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api.config["github_user_id"] = "12345"
        self.api.config_store.save(self.api.config)
        self.api.credentials.save("github-access-token", "github-token")
        FakeServerClient.packages_result = [PRIVATE_ENTRY]

        payload = self.api.servers_payload()

        self.assertEqual(payload["servers"][0]["status"], "active")
        package = payload["packages"][0]
        self.assertEqual(package["id"], "team1.private-mod")
        self.assertTrue(package["private"], "私有标记要留着，界面按它区分来源")
        self.assertIsNotNone(package["release"], "没有它界面会判「无可用版本」")
        self.assertEqual(package["release"]["version"], "1.0.0")
        self.assertEqual(package["install_assets"], ["mod.zip"])
        self.assertEqual(package["install_target"], "1.0.0")

    def test_a_private_entry_that_cannot_be_read_is_still_listed(self) -> None:
        """读不出来的条目也要露出来，并带上原因。

        静默丢掉会让界面显示成「这台服务器上没有包」，而事实是「有包，但读不出来」——
        这两种情况必须能分辨，否则只能靠翻日志。这里用一条 http 的下载地址复现真实场景：
        客户端要求 https，非 https 的地址会被判非法。
        """
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api.config["github_user_id"] = "12345"
        self.api.config_store.save(self.api.config)
        self.api.credentials.save("github-access-token", "github-token")
        broken = {
            **PRIVATE_ENTRY,
            "releases": [
                {
                    "id": 1,
                    "version": "1.0.0",
                    "assets": [
                        {
                            "id": 11,
                            "name": "mod.zip",
                            "size": 10,
                            "download_url": "http://plain.example.invalid/mod.zip",
                            "digest": "sha256:" + "ab" * 32,
                        }
                    ],
                }
            ],
        }
        FakeServerClient.packages_result = [broken]

        payload = self.api.servers_payload()

        self.assertEqual(len(payload["packages"]), 1, "读不出来也得列出来")
        package = payload["packages"][0]
        self.assertEqual(package["id"], "team1.private-mod")
        self.assertFalse(package["available"])
        self.assertTrue(package["issues"], "原因要带给界面")

    # ---- remove -----------------------------------------------------------

    def test_removing_a_server_marks_it_deleted_instead_of_erasing_it(self) -> None:
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)

        result = self.api.remove_developer_server(SERVER_ID)

        self.assertTrue(result["ok"], result)
        entries = self.servers()
        self.assertEqual(len(entries), 1, "抹掉的话下次同步会把它从远端拉回来")
        self.assertIs(entries[0]["deleted"], True)
        self.assertEqual(self.api.servers_payload()["servers"], [],
                         "读数里不该再出现已删除的服务器")

    def test_removing_an_unknown_server_fails(self) -> None:
        result = self.api.remove_developer_server("nobody")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "developer_server_remove_failed")

    # ---- logout -----------------------------------------------------------

    def test_logging_out_keeps_the_registered_servers(self) -> None:
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api.config["github_user_id"] = "12345"
        self.api.config_store.save(self.api.config)

        result = self.api.logout_github()

        self.assertTrue(result["ok"], result)
        self.assertEqual(self.api.config_store.load().get("github_user_id"), "")
        self.assertEqual(len(self.servers()), 1, "退出登录不是注销服务器")

    def test_logging_out_clears_both_github_credentials(self) -> None:
        self.api.credentials.save("github-access-token", "github-token")
        self.api.credentials.save("github-refresh-token", "refresh-token")
        for name in ("github-access-token", "github-refresh-token"):
            self.assertTrue(self.credential_file(name).is_file(), f"{name} 该先落在盘上")

        self.api.logout_github()

        for name in ("github-access-token", "github-refresh-token"):
            with self.subTest(name=name):
                self.assertFalse(self.credential_file(name).exists())

    # ---- GitHub 登录 ------------------------------------------------------

    def test_a_github_login_stores_the_refresh_token_the_reply_carries(self) -> None:
        """设备流那一次回答里就有刷新令牌；只存访问令牌的话，令牌一过期就再也换不回来。"""
        self.api._developer_server_controller._github_device = {
            "client_id": module.GITHUB_OAUTH_CLIENT_ID,
            "device_code": "device-code",
            "interval": 5,
        }
        reply = {"access_token": "github-token", "refresh_token": "refresh-token"}

        with patch.object(module, "github_device_poll", return_value=reply), patch.object(
            module, "github_current_user", return_value={"id": 12345}
        ):
            result = self.api.poll_github_device_login()

        self.assertTrue(result["ok"], result)
        self.assertEqual(self.api.credentials.load("github-access-token"), "github-token")
        self.assertEqual(self.api.credentials.load("github-refresh-token"), "refresh-token")

    # ---- gist conflicts ---------------------------------------------------

    def test_only_the_selected_conflicts_take_the_remote_identity(self) -> None:
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api._developer_server_controller._gist_conflicts = [{
            "server_id": SERVER_ID,
            "local": {"server_id": SERVER_ID, "name": SERVER_NAME},
            "remote": {"server_id": SERVER_ID, "name": "Remote name", "url": "https://remote.invalid"},
        }]

        result = self.api.resolve_github_gist_conflicts([SERVER_ID])

        self.assertTrue(result["ok"], result)
        self.assertEqual(self.servers()[0]["name"], "Remote name")

    def test_resolving_nothing_keeps_the_local_identity(self) -> None:
        self.api.add_developer_server("https://mods.example.invalid", FINGERPRINT)
        self.api._developer_server_controller._gist_conflicts = [{
            "server_id": SERVER_ID,
            "local": {"server_id": SERVER_ID, "name": SERVER_NAME},
            "remote": {"server_id": SERVER_ID, "name": "Remote name"},
        }]

        result = self.api.resolve_github_gist_conflicts([])

        self.assertTrue(result["ok"], result)
        self.assertEqual(self.servers()[0]["name"], SERVER_NAME)


if __name__ == "__main__":
    unittest.main()
