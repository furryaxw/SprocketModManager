"""开发者服务器：GitHub 登录、恢复 Gist、以及服务器列表的维护。

这里只放**身份与会话**这条线：GitHub Device Login、把服务器列表同步到恢复 Gist、以及登出。
服务器的增删刷新与私有包索引在 `private_distribution_controller`，它建在这条线之上。

GitHub 访问令牌与服务器会话都进凭据库（DPAPI），配置文件只留非秘密部分 ——
`ConfigStore.save` 会再剥一道，两处都不许出现令牌。
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse

from .base import ApiController
from ...application.data_hub import KEY_SERVERS
from ...application.private_packages import PrivatePackageSource
from ...domain.models import RegistryPackage
from ...infrastructure.private_servers import (
    GITHUB_OAUTH_CLIENT_ID,
    DeveloperServerClient,
    DeveloperServerError,
    GitHubLoginExpired,
    github_current_user,
    github_device_poll,
    github_device_start,
    github_gist_sync,
    github_token_refresh,
    normalize_server_url,
)

LOGGER = logging.getLogger(__name__)

GITHUB_TOKEN_NAME = "github-access-token"
# 刷新令牌单独放一份：`github-access-token` 这个名字与格式是已发布的，不动它。
GITHUB_REFRESH_TOKEN_NAME = "github-refresh-token"


class DeveloperServerController(ApiController):
    def __init__(self, api: Any):
        super().__init__(api)
        # Device Login 是"一次点击 + 几次轮询"的过程，状态只活在内存里：
        # 关掉界面就作废，不需要也不该落盘。
        self._github_device: dict[str, Any] | None = None
        self._github_access_token = ""
        self._github_login_checked_at = 0.0
        self._gist_conflicts: list[dict[str, Any]] = []

    # ---- GitHub 登录 ------------------------------------------------------

    def start_github_device_login(self) -> dict[str, Any]:
        try:
            device = github_device_start(GITHUB_OAUTH_CLIENT_ID)
            interval = max(5, int(device.get("interval", 5)))
            self._github_device = {
                "client_id": GITHUB_OAUTH_CLIENT_ID,
                "device_code": str(device["device_code"]),
                "interval": interval,
            }
            return self._success(
                user_code=str(device["user_code"]),
                verification_uri=str(device.get("verification_uri")),
                expires_in=int(device.get("expires_in", 900)),
                interval=interval,
            )
        except (OSError, ValueError, TypeError) as exc:
            return self._failure(exc, code="github_login_failed")

    def poll_github_device_login(self) -> dict[str, Any]:
        if not self._github_device:
            return self._failure(
                ValueError("GitHub device login is not started"), code="github_login_failed"
            )
        try:
            device = self._github_device
            result = github_device_poll(device["client_id"], device["device_code"])
            error = str(result.get("error", ""))
            if error in {"authorization_pending", "slow_down"}:
                if error == "slow_down":
                    device["interval"] = min(int(device.get("interval", 5)) + 5, 60)
                return self._success(pending=True, interval=int(device.get("interval", 5)))
            if error:
                self._github_device = None
                return self._failure(
                    ValueError(str(result.get("error_description") or error)),
                    code="github_login_failed",
                )
            access_token = str(result.get("access_token", "")).strip()
            if not access_token:
                raise ValueError("GitHub did not return an access token")
            identity = github_current_user(access_token)
            self._github_device = None
            self._remember_github_token(
                access_token,
                refresh_token=str(result.get("refresh_token", "")).strip(),
                user_id=str(identity["id"]),
            )
            # 换了账号，恢复索引里的服务器列表可能也变了：立刻对一次。
            synced = self.sync_github_gist()
            # 已经注册的服务器要换用新账号的会话 —— 旧会话属于上一个账号。
            reconnected = self._reconnect_servers()
            return self._success(
                logged_in=True,
                github_user_id=str(identity["id"]),
                conflicts=synced.get("conflicts", []) if synced.get("ok") else [],
                reconnected_server_ids=reconnected,
            )
        except (OSError, ValueError, TypeError) as exc:
            return self._failure(exc, code="github_login_failed")

    def cancel_github_device_login(self) -> dict[str, Any]:
        self._github_device = None
        return self._success()

    def logout_github(self) -> dict[str, Any]:
        """忘掉本机的 GitHub 身份：清令牌与用户号，但**不动**已注册的服务器。"""
        self._github_device = None
        self._forget_github_login()
        return self._success(logged_in=False)

    # ---- 恢复 Gist --------------------------------------------------------

    def sync_github_gist(self) -> dict[str, Any]:
        token = self._github_token()
        if not token:
            return self._failure(
                ValueError("GitHub login is required"), code="gist_sync_requires_login"
            )
        try:
            entries = self._developer_server_entries(include_deleted=True)
            gist_id, merged, conflicts = github_gist_sync(
                token,
                entries,
                str(self.config.get("github_gist_id", "") or ""),
                return_conflicts=True,
            )
            self._gist_conflicts = conflicts
            with self._config_lock:
                self.config["github_gist_id"] = gist_id
                self.config["developer_servers"] = merged
                self.config_store.save(self.config)
            return self._success(gist_id=gist_id, developer_servers=merged, conflicts=conflicts)
        except (OSError, ValueError, TypeError) as exc:
            return self._failure(exc, code="gist_sync_failed")

    def resolve_github_gist_conflicts(self, server_ids: list[str]) -> dict[str, Any]:
        """按用户勾选采纳远端那份身份；只对**本次冲突**里的 id 生效。"""
        selected = {str(item).strip() for item in server_ids if str(item).strip()}
        if not selected:
            return self._success(conflicts=[])
        try:
            entries = self._developer_server_entries()
            by_id = {str(item.get("server_id")): item for item in entries}
            for conflict in self._gist_conflicts:
                server_id = str(conflict.get("server_id", ""))
                remote = conflict.get("remote")
                if server_id in selected and isinstance(remote, dict):
                    by_id[server_id] = dict(remote)
            with self._config_lock:
                self.config["developer_servers"] = list(by_id.values())
                self.config_store.save(self.config)
            result = self.sync_github_gist()
            if not result.get("ok"):
                # 采纳已经落盘，报成功是如实的：没同步上去只说明远端还是旧的，下次登录会再对一次。
                return self._success(
                    developer_servers=list(by_id.values()),
                    conflicts=[],
                    sync_error=str(result.get("message", "")),
                )
            return self._success(
                developer_servers=result.get("developer_servers", []),
                conflicts=result.get("conflicts", []),
            )
        except (OSError, ValueError, TypeError) as exc:
            return self._failure(exc, code="gist_conflict_resolution_failed")

    # ---- 服务器列表 -------------------------------------------------------

    def servers_payload(self) -> dict[str, Any]:
        """开发者服务器那一份读数：服务器、它们的私有包、以及 GitHub 登录状态。

        既给数据层当 `KEY_SERVERS` 的刷新器，也供 `get_developer_servers` 自己调。
        单台连不上只降级它自己 —— 一台服务器离线不该让整个私有来源看起来是空的。
        """
        self.config = self.config_store.load()
        source = self._private_source()
        servers: list[dict[str, Any]] = []
        packages: list[dict[str, Any]] = []
        identity = self._github_user_id()
        for entry in self._developer_server_entries():
            server_id = str(entry.get("server_id", ""))
            data: dict[str, Any] = {**entry, "status": "registered", "packages": []}
            if identity:
                try:
                    entries = self._server_packages(entry)
                    source.learn(server_id, entries)
                    data["status"] = "active"
                    data["packages"] = [
                        {
                            **item,
                            "private": True,
                            "server_id": server_id,
                            "server_name": str(entry.get("name", "")),
                            "server_url": str(entry.get("url", "")),
                        }
                        for item in self._catalog_entries(entries, str(entry.get("url", "")))
                    ]
                except DeveloperServerError as exc:
                    data["status"] = (
                        "reauth_required" if exc.code == "invalid_session" else "offline"
                    )
                    data["error"] = str(exc)
                except (OSError, ValueError, TypeError) as exc:
                    data["status"] = "offline"
                    data["error"] = str(exc)
            servers.append(data)
            packages.extend(data["packages"])
        return {
            "servers": servers,
            "packages": packages,
            "github_login_expired": False,
            "github_user_id": identity,
        }

    def get_developer_servers(self) -> dict[str, Any]:
        """刷新开发者服务器读数。读数归数据层 —— 这条只回 ack。"""
        try:
            payload = self.servers_payload()
        except (OSError, ValueError, TypeError) as exc:
            return self._failure(exc, code="developer_servers_failed")
        self.data.publish(KEY_SERVERS, payload)
        return self._success()

    def add_developer_server(
            self, url: str, confirmed_fingerprint: str = ""
    ) -> dict[str, Any]:
        """加一台服务器：先探明它是谁，签名身份要指纹确认过才收下。

        第一次调用只做探测，回 `requires_confirmation` 与它自报的身份，由界面把指纹摆给用户核对；
        用户确认后再带指纹调一次。没核对就收下，等于谁都能冒充那台服务器往游戏目录里塞包。
        """
        try:
            normalized = normalize_server_url(url)
            client = DeveloperServerClient(normalized)
            info = client.info()
            advertised = info.signing_identity
            if isinstance(advertised, dict):
                fingerprint = str(advertised.get("fingerprint", ""))
                if str(confirmed_fingerprint).strip() != fingerprint:
                    return self._success(
                        requires_confirmation=True,
                        server={"server_id": info.server_id, "name": info.name, "url": normalized},
                        signing_identity=dict(advertised),
                        trust_method=info.trust_method,
                        manual_transport=info.manual_transport,
                    )
            with self._config_lock:
                entries = self._developer_server_entries(include_deleted=True)
                existing = next(
                    (item for item in entries if item.get("server_id") == info.server_id), None
                )
                entry: dict[str, Any] = {
                    "server_id": info.server_id,
                    "url": normalized,
                    "name": info.name,
                    "updated_at": int(time.time()),
                }
                if isinstance(advertised, dict):
                    entry["signing_identity"] = dict(advertised)
                    entry["public_key_fingerprint"] = str(advertised.get("fingerprint", ""))
                entry.pop("deleted", None)
                entries = (
                    [
                        entry if item.get("server_id") == info.server_id else item
                        for item in entries
                    ]
                    if existing is not None
                    else [*entries, entry]
                )
                config = self.config_store.load()
                config["developer_servers"] = entries
                self.config = config
                self.config_store.save(self.config)
            self.sync_github_gist()
            return self._success(server_id=info.server_id, name=info.name, added=existing is None)
        except (OSError, ValueError, TypeError, DeveloperServerError) as exc:
            return self._failure(exc, code="developer_server_add_failed")

    def remove_developer_server(self, server_id: str) -> dict[str, Any]:
        """移除一台服务器：本地条目打删除标记（不是直接抹掉），再同步一次恢复索引。

        留标记是为了让别的机器也能看到"这台被移除了"；直接抹掉的话，下次同步会把它
        从远端又拉回来。
        """
        target = str(server_id).strip()
        try:
            with self._config_lock:
                entries = self._developer_server_entries(include_deleted=True)
                found = False
                updated: list[dict[str, Any]] = []
                for item in entries:
                    if item.get("server_id") == target:
                        found = True
                        updated.append({
                            **item,
                            "deleted": True,
                            "updated_at": int(time.time()),
                        })
                    else:
                        updated.append(item)
                if not found:
                    return self._failure(
                        ValueError(f"unknown developer server: {target}"),
                        code="developer_server_remove_failed",
                    )
                config = self.config_store.load()
                config["developer_servers"] = updated
                self.config = config
                self.config_store.save(self.config)
            self._forget_server_runtime(target)
            self.sync_github_gist()
            return self._success(server_id=target, removed=True)
        except (OSError, ValueError, TypeError) as exc:
            return self._failure(exc, code="developer_server_remove_failed")

    def refresh_developer_server(self, server_id: str) -> dict[str, Any]:
        """重连一台服务器并重取它的私有索引；返回它下发了多少个包。"""
        target = str(server_id).strip()
        entry = next(
            (item for item in self._developer_server_entries() if item.get("server_id") == target),
            None,
        )
        if entry is None:
            return self._failure(
                ValueError(f"unknown developer server: {target}"),
                code="developer_server_refresh_failed",
            )
        try:
            client = self._connected_client(entry, reauth=True)
            packages = client.packages()
            source = self._private_source()
            source.learn(target, packages)
            return self._success(server_id=target, packages=len(packages))
        except (OSError, ValueError, TypeError, DeveloperServerError) as exc:
            return self._failure(exc, code=self._auth_code(exc, "developer_server_refresh_failed"))

    def activate_developer_server(self, server_id: str, key: str) -> dict[str, Any]:
        """用激活 Key 换取这台服务器上某个 Team 的权限分配。"""
        target = str(server_id).strip()
        entry = next(
            (item for item in self._developer_server_entries() if item.get("server_id") == target),
            None,
        )
        if entry is None:
            return self._failure(
                ValueError(f"unknown developer server: {target}"),
                code="developer_server_activation_failed",
            )
        try:
            client = self._connected_client(entry)
            result = client.redeem(str(key).strip(), self._github_user_id())
            return self._success(server_id=target, team_id=result.get("team_id", ""))
        except (OSError, ValueError, TypeError, DeveloperServerError) as exc:
            return self._failure(exc, code=self._auth_code(exc, "developer_server_activation_failed"))

    def accept_developer_server_invitation(self, server_id: str, token: str) -> dict[str, Any]:
        """用邀请码加入服务器上的某个 Team；邀请码本身就是凭据。"""
        target = str(server_id).strip()
        entry = next(
            (item for item in self._developer_server_entries() if item.get("server_id") == target),
            None,
        )
        if entry is None:
            return self._failure(
                ValueError(f"unknown developer server: {target}"),
                code="developer_server_invitation_failed",
            )
        try:
            client = self._connected_client(entry)
            result = client.accept_invitation(str(token).strip())
            return self._success(server_id=target, team_id=result.get("team_id", ""))
        except (OSError, ValueError, TypeError, DeveloperServerError) as exc:
            return self._failure(exc, code=self._auth_code(exc, "developer_server_invitation_failed"))

    # ---- 内部 -------------------------------------------------------------

    def _github_user_id(self) -> str:
        return str(self.config.get("github_user_id", "") or "")

    def _server_session_name(self, server_id: str) -> str:
        """凭据库里的名字：**就是裸 server_id**。

        凭据名不许含 `/` 或 `\\`（`CredentialStore._target` 直接拒），加前缀会把整条
        「连服务器」的路弄成 `invalid credential target`。这里与 `github-access-token`
        同一套命名，也读得到旧版本按裸 id 存下的会话。
        """
        return str(server_id).strip()

    def _trusted_client(
            self,
            entry: dict[str, Any],
            *,
            session_token: str = "",
    ) -> DeveloperServerClient:
        """建一个认这台服务器的 client，并核对它自报的签名身份与指纹。"""
        identity = entry.get("signing_identity")
        stored_fingerprint = str(entry.get("public_key_fingerprint", ""))
        client = DeveloperServerClient(
            str(entry.get("url", "")),
            session_token=session_token,
            trusted_signing_identity=(dict(identity) if isinstance(identity, dict) else None),
        )
        info = client.info()
        advertised = info.signing_identity
        if advertised is None:
            if stored_fingerprint or isinstance(identity, dict):
                raise ValueError("developer server removed its trusted signing identity")
            return client
        advertised_fingerprint = str(advertised.get("fingerprint", ""))
        if stored_fingerprint and stored_fingerprint != advertised_fingerprint and not client.rotation_applied:
            raise ValueError("developer server signing identity changed without a valid rotation declaration")
        return client

    def _connected_client(
            self,
            entry: dict[str, Any],
            *,
            reauth: bool = False,
    ) -> DeveloperServerClient:
        """拿到一台已登录的服务器 client：有会话就用，没有（或要求重连）就用 GitHub 令牌换一份。

        `reauth` 是「重连」的语义：丢掉已存的那份会话重新兑换。会话会在服务端过期，
        光靠存着的那一份，过期的服务器就永远回不来。

        连上之后要交给私有来源：归属只认「谁下发的」，来源里没有这台 client 就记不了归属，
        它下发的包也就路由不回去。
        """
        server_id = str(entry.get("server_id", ""))
        token = ""
        if not reauth:
            try:
                token = str(self.credentials.load(self._server_session_name(server_id)) or "")
            except (OSError, ValueError) as exc:
                LOGGER.warning("cannot read the stored server session error=%s", exc)
        if token:
            client = self._trusted_client(entry, session_token=token)
        else:
            github_token = self._github_token()
            if not github_token:
                raise ValueError("GitHub login is required before connecting to a developer server")
            client = self._trusted_client(entry)
            value = self._session_from_github(client, github_token)
            session = str(value.get("token", "")).strip()
            if not session:
                raise ValueError("developer server did not return a session token")
            client.session_token = session
            self.credentials.save(self._server_session_name(server_id), session)
        if client.rotation_applied:
            self._persist_rotation(entry, client)
        self._private_source().register(server_id, client)
        return client

    def _session_from_github(
            self, client: DeveloperServerClient, github_token: str
    ) -> dict[str, Any]:
        """用 GitHub 令牌换一份服务器会话；GitHub 自己判它无效时先换一张再试一次。

        `github_token_rejected` 是 GitHub 说这张访问令牌不认 —— 本机存着的已经作废，
        再送几次也只会拿到同一句。`github_token_forbidden`（限流、scope 不足）另说：
        令牌本身可能还有效，照原样报出去，存着的东西一个都不动。
        """
        try:
            return client.exchange_github_token(github_token)
        except DeveloperServerError as exc:
            if exc.code != "github_token_rejected":
                raise
        return client.exchange_github_token(self._renew_github_token())

    def _renew_github_token(self) -> str:
        """拿刷新令牌换一张访问令牌；没有可用的刷新令牌就只能重新登录。

        刷新令牌是一次性的，换回来的那一张必须落盘 —— 落不下等于把这次刷新丢了。
        """
        refresh_token = self._github_refresh_token()
        if not refresh_token:
            self._forget_github_login()
            raise GitHubLoginExpired("GitHub login is no longer usable; sign in to GitHub again")
        try:
            value = github_token_refresh(GITHUB_OAUTH_CLIENT_ID, refresh_token)
        except GitHubLoginExpired:
            self._forget_github_login()
            raise
        access_token = str(value.get("access_token", "")).strip()
        self._remember_github_token(
            access_token,
            refresh_token=str(value.get("refresh_token", "")).strip() or refresh_token,
            user_id=self._github_user_id(),
        )
        return access_token

    def _github_refresh_token(self) -> str:
        try:
            return str(self.credentials.load(GITHUB_REFRESH_TOKEN_NAME) or "").strip()
        except (OSError, ValueError) as exc:
            LOGGER.warning("cannot read the stored GitHub refresh token error=%s", exc)
            return ""

    def _forget_github_login(self) -> None:
        """撤掉本机的 GitHub 身份：两份凭据与用户号一起清。

        留着 `github_user_id` 会摆出一个「已登录」的样子，而它背后已经没有令牌了。
        """
        self._github_access_token = ""
        self._store_credential(GITHUB_TOKEN_NAME, "")
        self._store_credential(GITHUB_REFRESH_TOKEN_NAME, "")
        with self._config_lock:
            config = self.config_store.load()
            config["github_user_id"] = ""
            self.config = config
            self.config_store.save(self.config)

    def _store_credential(self, name: str, value: str) -> None:
        """写一份凭据；空值就是「这份不该在」，删掉它。

        写不下只记一条日志：内存里那份这次还能用，重启后需要重新登录。
        """
        try:
            if str(value).strip():
                self.credentials.save(name, value)
            else:
                self.credentials.delete(name)
        except (OSError, ValueError) as exc:
            LOGGER.warning("cannot write the stored GitHub credential name=%s error=%s", name, exc)

    @staticmethod
    def _auth_code(exc: Exception, fallback: str) -> str:
        """登录不能用了要报到界面认得的码上：`github_login_expired` 才有「重新登录」那句文案。"""
        return "github_login_expired" if isinstance(exc, GitHubLoginExpired) else fallback

    def _reconnect_servers(self) -> list[str]:
        """给每台服务器换一份新会话；返回换成功的 server_id。

        单台换不成就跳过它：一台服务器的会话换不了，不该挡住别的服务器。
        """
        if not self._github_token():
            return []
        connected: list[str] = []
        for entry in self._developer_server_entries():
            server_id = str(entry.get("server_id", ""))
            if not server_id or not entry.get("url"):
                continue
            try:
                self._connected_client(entry, reauth=True)
            except (OSError, ValueError, TypeError, DeveloperServerError) as exc:
                LOGGER.warning("cannot reconnect developer server server=%s error=%s", server_id, exc)
                continue
            connected.append(server_id)
        return connected

    @staticmethod
    def _with_private_source(item: dict[str, Any], server_url: str) -> dict[str, Any]:
        """给私有条目补上它的来源声明。

        服务端把抓取规则那条 `release` 剪掉了。没有它，解析下载地址会走「必须落在 github.com」
        那条分支（条目带 `repository` 时），或者干脆不校验（不带时）；补上外来源与主机，
        白名单才对得上，下载那一步的主机判定也才有依据。
        """
        host = str(urlparse(server_url).hostname or "").strip()
        if not host:
            return item
        release = dict(item.get("release") or {})
        release["source"] = {"type": "external", "hosts": [host]}
        return {**item, "release": release}

    def _catalog_entries(
            self, entries: list[dict[str, Any]], server_url: str = ""
    ) -> list[dict[str, Any]]:
        """把服务器下发的条目加工成**目录形状**。

        界面判「有没有可装的版本」看的是 `release` 与 `install_assets`；把条目原样递过去，
        每一条都会显示「无可用版本」——原始条目只有 `releases`。

        解析不了的条目**也交出去**，带上原因：静默丢掉会让界面显示成「这台服务器上没有包」，
        而事实是「有包，但它读不出来」。这两种情况要能分辨。
        """
        packages: list[RegistryPackage] = []
        unreadable: list[tuple[dict[str, Any], str]] = []
        for item in entries:
            try:
                packages.append(
                    RegistryPackage.from_dict(self._with_private_source(item, server_url))
                )
            except Exception as exc:  # noqa: BLE001 - 一条坏条目只影响它自己
                LOGGER.warning(
                    "private entry is not a package id=%s error=%s", item.get("id"), exc
                )
                unreadable.append((item, str(exc)))
        service = self.service
        catalog = (
            self.api._catalog_controller.entries_for(packages, service)
            if packages and service is not None
            else []
        )
        catalog.extend(self._unreadable_entry(item, reason) for item, reason in unreadable)
        return catalog

    @staticmethod
    def _unreadable_entry(item: dict[str, Any], reason: str) -> dict[str, Any]:
        """一条读不出目录读数的私有条目：仍列出来，把原因摆在 `issues` 里。"""
        package_id = str(item.get("id", ""))
        return {
            "id": package_id,
            "name": str(item.get("name", "")) or package_id,
            "display_name": dict(item.get("display_name") or {}),
            "description": dict(item.get("description") or {}),
            "authors": list(item.get("authors") or []),
            "repository": "",
            "repository_url": "",
            "license": str(item.get("license", "")),
            "category": str(item.get("category", "")),
            "tags": list(item.get("tags") or []),
            "release": None,
            "releases": [],
            "install_assets": [],
            "install_target": "",
            "issues": [reason],
            "available": False,
        }

    def _server_packages(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """取这台服务器的私有包；存着的会话过期时换一份新的再试一次。

        会话在服务端会过期，而 GitHub 令牌通常还在 —— 能自愈就别让用户去手动重连。
        """
        try:
            return self._connected_client(entry).packages()
        except DeveloperServerError as exc:
            if exc.code != "invalid_session":
                raise
            LOGGER.info(
                "developer server session expired; re-authenticating server=%s",
                entry.get("server_id"),
            )
            try:
                return self._connected_client(entry, reauth=True).packages()
            except (OSError, ValueError, TypeError):
                # 换不了新的（GitHub 令牌也没了）：如实说这条会话要重新登录，而不是笼统的离线。
                raise exc

    def _persist_rotation(self, entry: dict[str, Any], client: DeveloperServerClient) -> None:
        """服务器换了签名钥且声明成立：把新的身份与指纹落到配置里。"""
        info = client.info()
        advertised = info.signing_identity
        if not isinstance(advertised, dict):
            return
        with self._config_lock:
            entries = self._developer_server_entries(include_deleted=True)
            updated = [
                {
                    **item,
                    "signing_identity": dict(advertised),
                    "public_key_fingerprint": str(advertised.get("fingerprint", "")),
                    "updated_at": int(time.time()),
                }
                if item.get("server_id") == entry.get("server_id")
                else item
                for item in entries
            ]
            config = self.config_store.load()
            config["developer_servers"] = updated
            self.config = config
            self.config_store.save(self.config)

    def _forget_server_runtime(self, server_id: str) -> None:
        """这台服务器的会话与它下发的包归属一起撤掉，免得留下指向空会话的归属。"""
        try:
            self.credentials.delete(self._server_session_name(server_id))
        except (OSError, ValueError) as exc:
            LOGGER.warning("cannot clear the stored server session error=%s", exc)
        self._private_source().forget(server_id)

    def _private_source(self) -> PrivatePackageSource:
        """本机的私有包来源；第一次用到时才建，并挂到 service 上供安装计划使用。"""
        source = getattr(self, "_private_packages", None)
        if source is None:
            source = PrivatePackageSource()
            self._private_packages = source
        service = self.service
        if service is not None and service.private_assets is not source:
            service.private_assets = source
        return source

    def _github_token(self) -> str:
        if self._github_access_token:
            return self._github_access_token
        try:
            self._github_access_token = str(self.credentials.load(GITHUB_TOKEN_NAME) or "")
        except (OSError, ValueError) as exc:
            LOGGER.warning("cannot read the stored GitHub token error=%s", exc)
            self._github_access_token = ""
        return self._github_access_token

    def _remember_github_token(
            self, access_token: str, *, refresh_token: str, user_id: str
    ) -> None:
        """记下这一次登录：访问令牌与刷新令牌都进凭据库。

        刷新令牌为空（OAuth App 没开令牌过期）就把上一份登录留下的那张擦掉 —— 它属于上一次登录。
        """
        self._github_access_token = access_token
        self._store_credential(GITHUB_TOKEN_NAME, access_token)
        self._store_credential(GITHUB_REFRESH_TOKEN_NAME, refresh_token)
        with self._config_lock:
            config = self.config_store.load()
            config["github_user_id"] = user_id
            self.config = config
            self.config_store.save(self.config)
        self._github_login_checked_at = time.monotonic()

    def _developer_server_entries(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        entries = [
            dict(item)
            for item in (self.config.get("developer_servers") or [])
            if isinstance(item, dict)
        ]
        if include_deleted:
            return entries
        return [item for item in entries if item.get("deleted") is not True]
