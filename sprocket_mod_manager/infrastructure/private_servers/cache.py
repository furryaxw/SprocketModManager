from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .models import PrivateCatalogSnapshot, PrivatePackageManifest

PRIVATE_CACHE_VERSION = 3


class PrivateCatalogCache:
    def __init__(self, app_dir: Path):
        self.path = app_dir / "cache" / "private-servers.json"

    def load(self, server_id: str, github_user_id: str) -> PrivateCatalogSnapshot | None:
        try:
            root = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(root, dict) or root.get("version") != PRIVATE_CACHE_VERSION:
                return None
            raw = root.get("servers", {}).get(server_id)
            if not isinstance(raw, dict) or raw.get("github_user_id") != github_user_id:
                return None
            synced_at = raw.get("synced_at")
            if isinstance(synced_at, bool) or not isinstance(synced_at, int) or synced_at < 1:
                return None
            packages = raw.get("packages")
            entitlements = raw.get("entitlements")
            if not isinstance(packages, list) or not isinstance(entitlements, dict):
                return None
            return PrivateCatalogSnapshot(
                synced_at,
                tuple(PrivatePackageManifest.from_dict(item) for item in packages),
                dict(entitlements),
                dict(raw["key_status"]) if isinstance(raw.get("key_status"), dict) else None,
                dict(raw["signing_identity"]) if isinstance(raw.get("signing_identity"), dict) else None,
            )
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            return None

    def save(
            self,
            server_id: str,
            github_user_id: str,
            packages: tuple[PrivatePackageManifest, ...],
            entitlements: dict[str, Any],
            *,
            synced_at: int | None = None,
            key_status: dict[str, Any] | None = None,
            signing_identity: dict[str, Any] | None = None,
    ) -> None:
        root: dict[str, Any] = {"version": PRIVATE_CACHE_VERSION, "servers": {}}
        try:
            existing = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and existing.get("version") == PRIVATE_CACHE_VERSION:
                root = existing
        except (OSError, json.JSONDecodeError):
            pass
        servers = root.setdefault("servers", {})
        if not isinstance(servers, dict):
            servers = {}
            root["servers"] = servers
        sanitized_packages = []
        for package in packages:
            wire = package.to_dict()
            for release in wire.get("releases", []):
                if isinstance(release, dict):
                    release["assets"] = []
            sanitized_packages.append(wire)
        sanitized_grants = []
        for grant in entitlements.get("grants", []):
            if not isinstance(grant, dict):
                continue
            sanitized_grants.append({
                field: grant[field]
                for field in ("id", "permissions", "expires_at", "active", "state")
                if field in grant
            })
        sanitized_entitlements = {
            field: entitlements[field]
            for field in ("github_user_id", "server_time", "permissions")
            if field in entitlements
        }
        sanitized_entitlements["grants"] = sanitized_grants
        servers[server_id] = {
            "github_user_id": github_user_id,
            "synced_at": int(time.time()) if synced_at is None else synced_at,
            "packages": sanitized_packages,
            "entitlements": sanitized_entitlements,
            "key_status": key_status,
            "signing_identity": signing_identity,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(root, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def remove(self, server_id: str) -> None:
        try:
            root = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        servers = root.get("servers") if isinstance(root, dict) else None
        if not isinstance(servers, dict) or server_id not in servers:
            return
        del servers[server_id]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(root, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
