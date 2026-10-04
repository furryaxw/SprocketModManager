from __future__ import annotations

import base64
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from cryptography.hazmat.primitives import serialization

from .constants import MAX_ARCHIVE_BYTES, MAX_RESPONSE_BYTES, SUPPORTED_PROTOCOL_VERSION
from .models import DeveloperServerInfo, normalize_server_url
from ...utilities.signatures import (
    public_key_fingerprint,
    public_key_from_identity,
    verify_detached,
    verify_key_status_snapshot,
    verify_rotation_declaration,
)
from ...utilities.trust_negotiation import (
    negotiate_encoding,
    negotiate_manual_transport,
    negotiate_trust_method,
)


_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_DOWNLOAD_CHUNK_BYTES = 64 * 1024
# 协议里的包端点：条目里的 `download_url` 指的就是它，版本由客户端补。
_DOWNLOAD_ENDPOINT_PATTERN = re.compile(r"^/v1/packages/[^/]+/download/?$")


class _NoRedirect(HTTPRedirectHandler):
    """跳转自己处理：目标要先过 origin 判定，不能让 urlopen 静默跟随。"""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class DeveloperServerError(ValueError):
    """服务器答了但这次不成。

    `unreachable` 区分「根本连不上」和「连上了、服务器拒了」：只有前者是离线，
    后者要按服务器给的码说清楚（会话过期、工作区不存在、权限不足……）。
    """

    def __init__(
            self,
            message: str,
            *,
            status: int | None = None,
            code: str = "",
            unreachable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.unreachable = unreachable


class DeveloperServerClient:
    """一台开发者服务器的身份与会话。

    `info()` 判定服务器身份：协议版本、签名公钥、以及换钥声明是否成立。`key_status_snapshot()`
    校验签名状态快照的签名与时效。两者是下载与安装之前的信任前提，不缓存判定结果 ——
    缓存只用来省下 10 秒内的重复请求。
    """

    MAX_KEY_STATUS_TTL_SECONDS = 24 * 60 * 60
    RESPONSE_CACHE_SECONDS = 10
    MAX_DOWNLOAD_REDIRECTS = 3

    def __init__(
            self,
            base_url: str,
            *,
            timeout: int = 10,
            session_token: str = "",
            trusted_signing_identity: dict[str, Any] | None = None,
    ):
        self.base_url = normalize_server_url(base_url)
        self.timeout = timeout
        self.session_token = str(session_token).strip()
        self._signing_identity: dict[str, Any] | None = None
        self._info_loaded = False
        self._server_id = ""
        self._trusted_signing_identity = trusted_signing_identity
        self._download_origins: tuple[str, ...] = ()
        self.rotation_applied = False
        self._response_cache: dict[tuple[str, str], tuple[float, Any]] = {}

    def invalidate_response_cache(self) -> None:
        self._response_cache.clear()

    def _headers(self, accept: str = "application/json") -> dict[str, str]:
        """每个请求带会话：会话决定身份，作用域由包 id 的第一段表达。"""
        headers = {"Accept": accept, "User-Agent": "sprocket-mod-manager/private-test"}
        if self.session_token:
            headers["Authorization"] = f"Bearer {self.session_token}"
        return headers

    def _cached_response(self, kind: str, identity: str = "") -> Any:
        item = self._response_cache.get((kind, identity))
        if item is not None and time.monotonic() - item[0] < self.RESPONSE_CACHE_SECONDS:
            return item[1]
        return None

    def _cache_response(self, kind: str, value: Any, identity: str = "") -> Any:
        self._response_cache[(kind, identity)] = (time.monotonic(), value)
        return value

    @property
    def has_signing_identity(self) -> bool:
        return self._signing_identity is not None

    def _request(
            self,
            path: str,
            payload: dict[str, Any] | None = None,
            *,
            idempotency_key: str = "",
    ) -> dict[str, Any]:
        data = None
        headers = self._headers()
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_RESPONSE_BYTES:
                    raise ValueError("developer server response is too large")
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError("developer server response is too large")
        except HTTPError as exc:
            error: Any = None
            try:
                error = json.loads(exc.read(MAX_RESPONSE_BYTES).decode("utf-8"))
                message = error.get("message") or error.get("error", "")
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                message = ""
            raise DeveloperServerError(
                message or f"developer server returned HTTP {exc.code}",
                status=exc.code,
                code=str(error.get("code", "")) if isinstance(error, dict) else "",
            ) from exc
        except URLError as exc:
            raise DeveloperServerError(
                f"cannot connect to developer server: {exc.reason}", unreachable=True
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise DeveloperServerError(
                f"cannot read from developer server: {exc}", unreachable=True
            ) from exc
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("developer server returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("developer server response must be an object")
        return value

    def info(self) -> DeveloperServerInfo:
        value = self._request("/v1/server-info")
        protocol = int(value.get("protocol_version", 0))
        if protocol != SUPPORTED_PROTOCOL_VERSION:
            raise ValueError(f"unsupported developer server protocol: {protocol}")
        server_id = str(value.get("server_id", "")).strip()
        name = str(value.get("name", "")).strip()
        if not server_id or not name:
            raise ValueError("developer server identity is incomplete")
        self._server_id = server_id
        identity = value.get("signing_identity")
        rotation = value.get("signing_rotation")
        policy = value.get("identity_policy")
        if policy is None:
            policy = {}
        if not isinstance(policy, dict):
            raise ValueError("developer server identity policy is invalid")
        trust_method = negotiate_trust_method(
            policy.get("trust_methods"), server_preferred=policy.get("preferred_trust_method", "")
        )
        key_encoding = negotiate_encoding(
            policy.get("encodings"), server_preferred=policy.get("preferred_encoding", "")
        )
        manual_transport = ""
        if trust_method == "manual":
            manual_transport = negotiate_manual_transport(
                policy.get("manual_transports"),
                server_preferred=policy.get("preferred_manual_transport", ""),
            )
        if identity is not None:
            public_key = public_key_from_identity(identity)
            if self._trusted_signing_identity is not None:
                trusted_key = public_key_from_identity(self._trusted_signing_identity)
                trusted_fingerprint = public_key_fingerprint(trusted_key)
                advertised_fingerprint = public_key_fingerprint(public_key)
                if (
                    trusted_fingerprint != advertised_fingerprint
                    or self._trusted_signing_identity.get("key_id") != identity.get("key_id")
                ):
                    if not isinstance(rotation, dict):
                        raise ValueError("developer server signing identity changed without a valid rotation declaration")
                    declaration = rotation.get("declaration")
                    previous_signature = rotation.get("previous_signature")
                    next_signature = rotation.get("next_signature")
                    if not all(isinstance(item, dict) for item in (declaration, previous_signature, next_signature)):
                        raise ValueError("developer server signing rotation is invalid")
                    next_key = verify_rotation_declaration(
                        declaration,
                        previous_signature,
                        next_signature,
                        trusted_key,
                        now=int(time.time()),
                    )
                    now = int(time.time())
                    if (
                        declaration.get("server_id") != server_id
                        or declaration.get("previous_key_id") != self._trusted_signing_identity.get("key_id")
                        or declaration.get("next_key_id") != identity.get("key_id")
                        or declaration.get("effective_at", now + 1) > now
                        or public_key_fingerprint(next_key) != advertised_fingerprint
                    ):
                        raise ValueError("developer server signing rotation does not match its advertised identity")
                    self.rotation_applied = True
            self._signing_identity = {"key": public_key, "key_id": str(identity.get("key_id", ""))}
        elif self._trusted_signing_identity is not None:
            raise ValueError("developer server removed its trusted signing identity")
        raw_origins = value.get("download_origins")
        download_origins = (
            tuple(str(item).strip() for item in raw_origins if str(item).strip())
            if isinstance(raw_origins, list)
            else ()
        )
        self._download_origins = download_origins
        self._info_loaded = True
        return DeveloperServerInfo(
            server_id=server_id,
            name=name,
            operator=str(value.get("operator", "")).strip(),
            protocol_version=protocol,
            signing_identity=identity,
            trust_method=trust_method,
            key_encoding=key_encoding,
            manual_transport=manual_transport,
            signing_rotation=rotation if isinstance(rotation, dict) else None,
            download_origins=download_origins,
        )

    def exchange_github_token(self, access_token: str) -> dict[str, Any]:
        value = self._request("/v1/auth/github/exchange", {"access_token": str(access_token).strip()})
        token = str(value.get("token", "")).strip()
        if not token:
            raise ValueError("developer server did not return a session token")
        return value

    def revoke_session(self) -> None:
        if not self.session_token:
            return
        self._request("/v1/auth/session/revoke", {})

    def redeem(self, key: str, github_user_id: str, *, request_id: str = "") -> dict[str, Any]:
        idempotency_key = str(request_id).strip() or uuid.uuid4().hex
        return self._request(
            "/v1/keys/redeem",
            {"key": key} if self.session_token else {"key": key, "github_user_id": github_user_id},
            idempotency_key=idempotency_key,
        )

    def accept_invitation(self, token: str, *, request_id: str = "") -> dict[str, Any]:
        """Join a Team with an invitation code; the code itself is the credential."""
        idempotency_key = str(request_id).strip() or uuid.uuid4().hex
        return self._request(
            "/v1/invitations/accept",
            {"token": str(token).strip()},
            idempotency_key=idempotency_key,
        )

    def packages(self) -> list[dict[str, Any]]:
        """私有索引：我在此服务器上全部 Team 的包，逐条验签后摊平。

        条目就是公开 v3 条目加条目级 `signature`；Team 只决定归属，不改变形状。验签是下载与
        安装的信任前提，**任何一条不过就拒整份索引** —— 降级成「未验证但可用」等于把破坏签名
        变成一种 downgrade 手段。`info()` 声明的公钥是唯一的信任锚。
        """
        if not self._info_loaded:
            self.info()
        if self._signing_identity is None:
            raise ValueError("developer server package signing is not configured")
        cached = self._cached_response("packages")
        if cached is not None:
            return cached
        value = self._request("/v1/packages")
        teams = value.get("teams")
        if not isinstance(teams, list):
            raise ValueError("developer server package index is invalid")
        entries: list[dict[str, Any]] = []
        for team in teams:
            if not isinstance(team, dict) or not isinstance(team.get("packages"), list):
                raise ValueError("developer server package index is invalid")
            for entry in team["packages"]:
                entries.append(self._verified_entry(entry))
        return self._cache_response("packages", entries)

    def _verified_entry(self, entry: object) -> dict[str, Any]:
        """一条私有条目验签通过后的形状：签名摘掉，其余原样交给 Registry。"""
        if not isinstance(entry, dict):
            raise ValueError("developer server package entry is invalid")
        envelope = entry.get("signature")
        if not isinstance(envelope, dict):
            raise ValueError("developer server package entry is not signed")
        identity = self._signing_identity
        if identity is None:
            raise ValueError("developer server package signing is not configured")
        if envelope.get("key_id") != identity["key_id"]:
            raise ValueError("developer server package entry is signed by an unexpected key")
        manifest = {key: value for key, value in entry.items() if key != "signature"}
        verify_detached(manifest, envelope, identity["key"])
        return manifest

    def download(
            self,
            package_id: str,
            version: str,
            destination: Path,
            *,
            url: str = "",
            expected_size: int = 0,
            progress: Callable[[str], None] | None = None,
    ) -> int:
        """把一个包的某个版本流式落盘，返回写入的字节数。

        `url` 给定时用它（条目里的 `download_url` 指向服务端自己的包端点），否则按服务器自己的
        origin 拼。两种来源都要过 origin 判定。端点地址上还缺 `version` 时补上解析出来的那一版：
        服务器按版本取包，条目里的地址只说到哪个端点为谁取。

        归档上限 1 GiB，所以按块写盘而不是读进内存；失败时半截文件不留。
        `server-info` 的 `download_origins` 声明了允许把下载指向哪些源，服务器自身 origin 天然在内。
        """
        identifier = str(package_id).strip()
        release = str(version).strip()
        if not identifier or not release:
            raise ValueError("package id and version are required")
        if not self.session_token:
            raise ValueError("developer server session is required to download")
        target_url = self._with_version(
            str(url).strip() or (
                f"{self.base_url}/v1/packages/{quote(identifier, safe='')}/download"
                f"?version={quote(release, safe='')}"
            ),
            release,
        )
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if progress is not None:
            progress(f"downloading {identifier} {release}")
        try:
            with self._open_download(build_opener(_NoRedirect), target_url) as response:
                written = self._write_download(response, target)
            if expected_size and written != int(expected_size):
                raise DeveloperServerError(
                    f"developer server download size mismatch: expected {expected_size}, got {written}"
                )
            return written
        except DeveloperServerError:
            target.unlink(missing_ok=True)
            raise
        except HTTPError as exc:
            target.unlink(missing_ok=True)
            raise self._download_failure(exc) from exc
        except (URLError, TimeoutError, OSError) as exc:
            target.unlink(missing_ok=True)
            raise DeveloperServerError(
                f"cannot download from developer server: {exc}", unreachable=True
            ) from exc
        except BaseException:
            target.unlink(missing_ok=True)
            raise

    def _open_download(self, opener: Any, url: str) -> Any:
        """发下载请求；跳转自己跟着走，每一跳的目标都要落在信任的源里。

        会话只发给服务器自己的 origin：跳转目标是对象存储的签名直链，它自己带着签名，
        再捎上 `Authorization` 会被存储端当成两种鉴权同时出现而拒（400），也等于把会话令牌
        交给第三方。
        """
        current = url
        for _ in range(self.MAX_DOWNLOAD_REDIRECTS + 1):
            if not self._origin_allowed(current):
                raise DeveloperServerError(
                    f"developer server download points outside its allowed origins: {current}"
                )
            request = Request(current, headers=self._hop_headers(current))
            try:
                return opener.open(request, timeout=self.timeout)
            except HTTPError as exc:
                if exc.code not in _REDIRECT_CODES:
                    raise
                location = ""
                if exc.headers is not None:
                    location = str(exc.headers.get("Location", "")).strip()
                if not location:
                    raise DeveloperServerError(
                        "developer server download redirect has no location"
                    ) from exc
                current = urljoin(current, location)
        raise DeveloperServerError("developer server download redirected too many times")

    def _hop_headers(self, url: str) -> dict[str, str]:
        """这一跳该带的头：服务器自己的 origin 带会话，别的源只带裸请求。"""
        headers = self._headers("application/octet-stream")
        if not self._is_server_origin(url):
            headers.pop("Authorization", None)
        return headers

    def _is_server_origin(self, url: str) -> bool:
        parsed = urlparse(url)
        base = urlparse(self.base_url)
        return (
            f"{str(parsed.scheme).lower()}://{str(parsed.netloc).lower()}"
            == f"{str(base.scheme).lower()}://{str(base.netloc).lower()}"
        )

    def _origin_allowed(self, url: str) -> bool:
        """同源天然可信；跨源必须被 `download_origins` 显式允许。"""
        parsed = urlparse(url)
        scheme = str(parsed.scheme or "").lower()
        host = str(parsed.hostname or "").lower()
        if scheme not in {"http", "https"} or not host:
            return False
        if self._is_server_origin(url):
            return True
        origin = f"{scheme}://{str(parsed.netloc).lower()}"
        for allowed in self._download_origins:
            candidate = allowed.strip().lower().rstrip("/")
            if not candidate:
                continue
            if "://" in candidate:
                if candidate == origin:
                    return True
            elif scheme == "https" and candidate == host:
                return True
        return False

    @staticmethod
    def _with_version(url: str, release: str) -> str:
        """包端点上缺 `version` 就补上；别的地址一个字都不改。

        服务器下发的 `download_url` 写的是它自己的包端点（`/v1/packages/{id}/download`），
        取哪一版由客户端按解析出来的那版说 —— 两侧的跨实现 fixture 都是这个形状。
        已经带 `version` 的地址（服务器点名了某一版）与别的路径（对象存储、CDN 的直链或
        签名地址）原样用：往签名查询串上加参数会把签名弄坏。
        """
        parsed = urlparse(url)
        if not _DOWNLOAD_ENDPOINT_PATTERN.match(parsed.path):
            return url
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if any(key == "version" for key, _value in query):
            return url
        query.append(("version", release))
        return urlunparse(parsed._replace(query=urlencode(query)))

    @staticmethod
    def _write_download(response: Any, target: Path) -> int:
        written = 0
        with target.open("wb") as handle:
            while True:
                chunk = response.read(_DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_ARCHIVE_BYTES:
                    raise DeveloperServerError("developer server download exceeds the archive limit")
                handle.write(chunk)
        return written

    @staticmethod
    def _download_failure(exc: HTTPError) -> DeveloperServerError:
        """把 HTTP 失败翻成带原因的错，并点出失败的是哪一跳。

        跳转之后那一跳可能是对象存储的签名直链：它的错误体不是我们的 JSON 契约，
        不说清是哪一跳，看到的就只是一句「HTTP 400」。
        """
        code = ""
        message = ""
        try:
            body = json.loads(exc.read(MAX_RESPONSE_BYTES).decode("utf-8"))
            if isinstance(body, dict):
                code = str(body.get("code", ""))
                message = str(body.get("message") or body.get("error") or "")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, OSError):
            message = ""
        detail = message or f"developer server returned HTTP {exc.code}"
        hop = DeveloperServerClient._hop_label(str(getattr(exc, "url", "")))
        return DeveloperServerError(
            f"{detail} ({hop})" if hop else detail,
            status=exc.code,
            code=code,
        )

    @staticmethod
    def _hop_label(url: str) -> str:
        """那一跳的「源 + 路径」，不带查询串：签名直链的查询串是一次性凭据。"""
        parsed = urlparse(url)
        if not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    def key_status_snapshot(self) -> dict[str, Any]:
        if not self._info_loaded:
            self.info()
        if self._signing_identity is None:
            raise ValueError("developer server package signing is not configured")
        cached = self._cached_response("key-status")
        if cached is not None:
            return cached
        value = self._request("/v1/key-status")
        identity = {
            "algorithm": "ed25519",
            "encoding": "base64url",
            "key_id": self._signing_identity["key_id"],
            "public_key": base64.urlsafe_b64encode(
                self._signing_identity["key"].public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                )
            ).decode("ascii").rstrip("="),
            "fingerprint": public_key_fingerprint(self._signing_identity["key"]),
        }
        verify_key_status_snapshot(
            value,
            identity,
            server_id=self._server_id,
            max_ttl_seconds=self.MAX_KEY_STATUS_TTL_SECONDS,
        )
        return self._cache_response("key-status", value)

    def key_status(self) -> dict[str, Any]:
        return dict(self.key_status_snapshot()["status"])
