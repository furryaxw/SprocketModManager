"""Ed25519 detached signatures for private package manifests."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

SIGNATURE_ALGORITHM = "ed25519"
SIGNATURE_FORMAT = "detached-canonical-json"


def canonical_json(value: Any) -> bytes:
    """Serialize JSON deterministically; reject NaN/Infinity and non-JSON values."""
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        .encode("utf-8")
    )


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: object) -> bytes:
    text = str(value).strip()
    if not text or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for character in text):
        raise ValueError("signature encoding is invalid")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_key_bytes(public_key: Ed25519PublicKey) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def public_key_fingerprint(public_key: Ed25519PublicKey) -> str:
    return "sha256:" + hashlib.sha256(public_key_bytes(public_key)).hexdigest()


def public_key_from_identity(identity: object) -> Ed25519PublicKey:
    if (
        not isinstance(identity, dict)
        or identity.get("algorithm") != SIGNATURE_ALGORITHM
        or identity.get("encoding") != "base64url"
        or not str(identity.get("key_id", "")).strip()
    ):
        raise ValueError("developer server signing identity is invalid")
    raw = _b64url_decode(identity.get("public_key"))
    if len(raw) != 32:
        raise ValueError("developer server signing public key is invalid")
    public_key = Ed25519PublicKey.from_public_bytes(raw)
    if str(identity.get("fingerprint", "")) != public_key_fingerprint(public_key):
        raise ValueError("developer server signing key fingerprint does not match its public key")
    return public_key


def sign_detached(manifest: Any, private_key: Ed25519PrivateKey, *, key_id: str) -> dict[str, str]:
    key_id = str(key_id).strip()
    if not key_id:
        raise ValueError("signature key id is required")
    return {
        "format": SIGNATURE_FORMAT,
        "algorithm": SIGNATURE_ALGORITHM,
        "encoding": "base64url",
        "key_id": key_id,
        "signature": _b64url_encode(private_key.sign(canonical_json(manifest))),
    }


def verify_detached(manifest: Any, envelope: dict[str, Any], public_key: Ed25519PublicKey) -> None:
    if not isinstance(envelope, dict):
        raise ValueError("signature envelope is invalid")
    if envelope.get("format") != SIGNATURE_FORMAT or envelope.get("algorithm") != SIGNATURE_ALGORITHM:
        raise ValueError("signature format or algorithm is unsupported")
    if envelope.get("encoding") != "base64url" or not str(envelope.get("key_id", "")).strip():
        raise ValueError("signature envelope metadata is invalid")
    signature = _b64url_decode(envelope.get("signature"))
    if len(signature) != 64:
        raise ValueError("signature length is invalid")
    try:
        public_key.verify(signature, canonical_json(manifest))
    except Exception as exc:
        raise ValueError("manifest signature verification failed") from exc


def verify_rotation_declaration(
    declaration: dict[str, Any], previous_signature: dict[str, Any], next_signature: dict[str, Any],
    previous_key: Ed25519PublicKey, *, now: int,
) -> Ed25519PublicKey:
    """Verify a dual-signed Ed25519 rotation during its allowed overlap window."""
    required = {
        "schema_version", "server_id", "previous_key_id", "next_key_id",
        "next_public_key", "effective_at", "previous_key_expires_at",
    }
    if not isinstance(declaration, dict) or set(declaration) != required or declaration.get("schema_version") != 1:
        raise ValueError("key rotation declaration is invalid")
    effective = declaration.get("effective_at")
    expires = declaration.get("previous_key_expires_at")
    if (
        not str(declaration.get("server_id", "")).strip()
        or not str(declaration.get("previous_key_id", "")).strip()
        or not str(declaration.get("next_key_id", "")).strip()
        or declaration["previous_key_id"] == declaration["next_key_id"]
        or isinstance(effective, bool) or not isinstance(effective, int)
        or isinstance(expires, bool) or not isinstance(expires, int)
        or expires <= effective or expires <= now
        or expires - effective > 7 * 24 * 60 * 60
    ):
        raise ValueError("key rotation timing or identity is invalid")
    raw = _b64url_decode(declaration.get("next_public_key"))
    if len(raw) != 32:
        raise ValueError("next signing public key is invalid")
    next_key = Ed25519PublicKey.from_public_bytes(raw)
    if previous_signature.get("key_id") != declaration["previous_key_id"]:
        raise ValueError("previous key rotation signature is invalid")
    if next_signature.get("key_id") != declaration["next_key_id"]:
        raise ValueError("next key rotation signature is invalid")
    verify_detached(declaration, previous_signature, previous_key)
    verify_detached(declaration, next_signature, next_key)
    return next_key


def verify_key_status_snapshot(
    snapshot: object,
    signing_identity: object,
    *,
    server_id: str,
    now: int | None = None,
    max_ttl_seconds: int = 24 * 60 * 60,
) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("developer server key status is invalid")
    status = snapshot.get("status")
    signature = snapshot.get("signature")
    if not isinstance(status, dict) or not isinstance(signature, dict):
        raise ValueError("developer server key status is invalid")
    public_key = public_key_from_identity(signing_identity)
    key_id = str(signing_identity.get("key_id", ""))
    if signature.get("key_id") != key_id:
        raise ValueError("developer server key status signing key is unexpected")
    verify_detached(status, signature, public_key)
    current = int(time.time()) if now is None else now
    issued_at = status.get("issued_at")
    expires_at = status.get("expires_at")
    revoked = status.get("revoked_key_ids")
    if (
        status.get("schema_version") != 1
        or status.get("server_id") != server_id
        or not isinstance(issued_at, int) or isinstance(issued_at, bool)
        or not isinstance(expires_at, int) or isinstance(expires_at, bool)
        or issued_at > current + 300 or expires_at <= current
        or expires_at - issued_at > max_ttl_seconds
        or not isinstance(revoked, list)
        or not all(isinstance(item, str) and item for item in revoked)
    ):
        raise ValueError("developer server key status is expired or invalid")
    active = str(status.get("active_key_id", ""))
    if active != key_id or active in revoked:
        raise ValueError("developer server active signing key is revoked or inconsistent")
    return dict(status)
