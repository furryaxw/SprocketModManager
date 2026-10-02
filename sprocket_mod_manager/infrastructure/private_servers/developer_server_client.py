from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import serialization

from .constants import MAX_RESPONSE_BYTES, SUPPORTED_PROTOCOL_VERSION
from .models import DeveloperServerInfo, normalize_server_url
from ...utilities.signatures import (
    public_key_fingerprint,
    public_key_from_identity,
    verify_key_status_snapshot,
    verify_rotation_declaration,
)
from ...utilities.trust_negotiation import (
    negotiate_encoding,
    negotiate_manual_transport,
    negotiate_trust_method,
)


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
