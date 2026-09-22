from __future__ import annotations

import hashlib
import base64
import json
import os
import uuid
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from cryptography.hazmat.primitives import serialization

from .constants import MAX_ARCHIVE_BYTES, MAX_RESPONSE_BYTES, SUPPORTED_PROTOCOL_VERSION
from .models import DeveloperServerInfo, PrivatePackageManifest, normalize_server_url
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


class DeveloperServerError(ValueError):
    def __init__(self, message: str, *, status: int | None = None, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class DeveloperServerClient:
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
        headers = {"Accept": "application/json", "User-Agent": "sprocket-mod-manager/private-test"}
        if self.session_token:
            headers["Authorization"] = f"Bearer {self.session_token}"
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
            raise ValueError(f"cannot connect to developer server: {exc.reason}") from exc
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("developer server returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("developer server response must be an object")
        return value

    def _identity_query(self, github_user_id: str) -> str:
        return "" if self.session_token else "?" + urlencode({"github_user_id": github_user_id})

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
            demo_auth=value.get("demo_auth") is True,
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

    def packages(self, github_user_id: str) -> tuple[PrivatePackageManifest, ...]:
        if not self._info_loaded:
            self.info()
        cached = self._cached_response("packages", str(github_user_id))
        if cached is not None:
            return cached
        value = self._request("/v1/packages")
        packages = value.get("packages", [])
        if not isinstance(packages, list):
            raise ValueError("developer server package list is invalid")
        manifests_list = []
        for item in packages:
            if self._signing_identity is not None:
                if not isinstance(item, dict) or not isinstance(item.get("signature"), dict):
                    raise ValueError("developer server package signature is missing")
                signature = item["signature"]
                signed_payload = dict(item)
                signed_payload.pop("signature", None)
                if signature.get("key_id") != self._signing_identity["key_id"]:
                    raise ValueError("developer server package signing key is unexpected")
                verify_detached(signed_payload, signature, self._signing_identity["key"])
            manifests_list.append(PrivatePackageManifest.from_dict(item))
        manifests = tuple(manifests_list)
        ids = [item.id for item in manifests]
        if len(ids) != len(set(ids)):
            raise ValueError("developer server package list contains duplicate ids")
        return self._cache_response("packages", manifests, str(github_user_id))

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

    def entitlements(self, github_user_id: str) -> dict[str, Any]:
        cached = self._cached_response("entitlements", str(github_user_id))
        if cached is not None:
            return cached
        value = self._request("/v1/entitlements")
        grants = value.get("grants", [])
        permissions = value.get("permissions", [])
        if (
                not isinstance(grants, list)
                or not all(isinstance(item, dict) for item in grants)
                or not isinstance(permissions, list)
                or not all(isinstance(item, str) for item in permissions)
        ):
            raise ValueError("developer server entitlement response is invalid")
        return self._cache_response("entitlements", value, str(github_user_id))

    def download_archive(
            self,
            download_path: str,
            github_user_id: str,
            destination: Path,
            *,
            version: str,
            expected_size: int,
            progress: Callable[[str], None] | None = None,
    ) -> Path:
        parsed_path = urlparse(download_path)
        if (
                parsed_path.scheme
                or parsed_path.netloc
                or parsed_path.query
                or parsed_path.fragment
                or not parsed_path.path.startswith("/v1/packages/")
                or not parsed_path.path.endswith("/download")
        ):
            raise ValueError("developer server returned an invalid package download path")
        if expected_size < 1 or expected_size > MAX_ARCHIVE_BYTES:
            raise ValueError("developer server returned an invalid package archive size")
        package_id = parsed_path.path[len("/v1/packages/"):-len("/download")].strip("/")
        if not package_id or not version.strip():
            raise ValueError("developer server download identity is incomplete")
        authorization = self._request(
            f"/v1/packages/{package_id}/download-url",
            {"version": version.strip()},
        )
        token = str(authorization.get("token", "")).strip()
        if not token:
            raise ValueError("developer server did not return a download token")
        request_url = self.base_url + parsed_path.path + "?" + urlencode({"version": version.strip(), "token": token})
        headers = {"Accept": "application/octet-stream", "User-Agent": "sprocket-mod-manager/private-test"}
        if self.session_token:
            headers["Authorization"] = f"Bearer {self.session_token}"
        request = Request(request_url, headers=headers)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        total = 0
        try:
            with urlopen(request, timeout=self.timeout) as response, temporary.open("wb") as output:
                final = urlparse(response.geturl())
                origin = urlparse(self.base_url)
                if (final.scheme, final.hostname, final.port) != (origin.scheme, origin.hostname, origin.port):
                    raise ValueError("developer server redirected the package download to another origin")
                length = response.headers.get("Content-Length")
                if length and int(length) != expected_size:
                    raise ValueError("developer server package size changed")
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > expected_size or total > MAX_ARCHIVE_BYTES:
                        raise ValueError("developer server package exceeds its declared size")
                    output.write(chunk)
                    if progress:
                        progress(f"Downloaded {total:,} of {expected_size:,} bytes")
            if total != expected_size:
                raise ValueError("developer server package is incomplete")
            os.replace(temporary, destination)
            return destination
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
            raise ValueError(f"cannot download from developer server: {exc.reason}") from exc
        finally:
            temporary.unlink(missing_ok=True)
