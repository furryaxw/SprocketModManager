from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .constants import MAX_ARCHIVE_BYTES
from ...domain.models import RegistryPackage, ReleaseInfo
from ...utilities.urls import is_loopback_host

_PACKAGE_ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def normalize_server_url(value: object) -> str:
    text = str(value or "").strip().rstrip("/")
    parsed = urlparse(text)
    is_https = parsed.scheme == "https" and bool(parsed.hostname)
    is_loopback_http = parsed.scheme == "http" and is_loopback_host(parsed.hostname)
    if not (is_https or is_loopback_http):
        raise ValueError("developer server must use HTTPS, except for loopback test servers")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("developer server URL must not contain credentials, path, query or fragment")
    return text


@dataclass(frozen=True)
class DeveloperServerInfo:
    server_id: str
    name: str
    operator: str
    protocol_version: int
    demo_auth: bool
    signing_identity: dict[str, Any] | None = None
    trust_method: str = ""
    key_encoding: str = ""
    manual_transport: str = ""
    signing_rotation: dict[str, Any] | None = None


@dataclass(frozen=True)
class PrivateArchive:
    name: str
    size: int
    sha256: str
    download_path: str

    @classmethod
    def from_dict(cls, raw: object, package_id: str) -> "PrivateArchive":
        if not isinstance(raw, dict):
            raise ValueError("private package archive metadata is invalid")
        name = str(raw.get("name", "")).strip()
        digest_value = str(raw.get("digest", "")).strip().lower()
        algorithm, separator, digest = digest_value.partition(":")
        download_path = str(raw.get("download_path", "")).strip()
        size = raw.get("size")
        if (
                not name
                or Path(name).name != name
                or Path(name).suffix.casefold() not in {".zip", ".dll"}
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size < 1
                or size > MAX_ARCHIVE_BYTES
                or separator != ":"
                or algorithm != "sha256"
                or not _SHA256_RE.fullmatch(digest)
                or download_path != f"/v1/packages/{package_id}/download"
        ):
            raise ValueError("private package archive metadata is invalid")
        return cls(name, size, digest, download_path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "digest": f"sha256:{self.sha256}",
            "download_path": self.download_path,
        }


@dataclass(frozen=True)
class PrivateFile:
    name: str
    target: str
    sha256: str

    @classmethod
    def from_dict(cls, raw: object) -> "PrivateFile":
        if not isinstance(raw, dict):
            raise ValueError("private package file manifest is invalid")
        name = str(raw.get("name", "")).strip()
        target = str(raw.get("target", "")).strip().replace("\\", "/")
        digest = str(raw.get("sha256", "")).strip().lower()
        parts = target.split("/")
        if (
                not name
                or Path(name).name != name
                or Path(name).suffix.casefold() != ".dll"
                or len(parts) != 2
                or parts[0] not in {"Mods", "UserLibs"}
                or parts[1] != name
                or any(part in {"", ".", ".."} for part in parts)
                or not _SHA256_RE.fullmatch(digest)
        ):
            raise ValueError("private package file manifest is invalid")
        return cls(name, "/".join(parts), digest)

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "target": self.target, "sha256": self.sha256}


@dataclass(frozen=True)
class PrivatePackageManifest:
    package: RegistryPackage
    archives: tuple[PrivateArchive, ...]
    files: tuple[PrivateFile, ...]
    wire: dict[str, Any]

    @property
    def id(self) -> str:
        return self.package.id

    @property
    def name(self) -> str:
        return self.package.name

    @property
    def version(self) -> str:
        releases = self.package.releases or ()
        return str(releases[0].version) if releases else ""

    @property
    def release(self) -> ReleaseInfo | None:
        releases = self.package.releases or ()
        return releases[0] if releases else None

    @property
    def archive(self) -> PrivateArchive | None:
        return self.archives[0] if self.archives else None

    @classmethod
    def from_dict(cls, raw: object) -> "PrivatePackageManifest":
        if not isinstance(raw, dict):
            raise ValueError("developer server package list is invalid")
        package_id = str(raw.get("id", "")).strip()
        if (
                raw.get("schema_version") != 1
                or not _PACKAGE_ID_RE.fullmatch(package_id)
                or any(field in raw for field in ("repository", "release", "featured", "required_permission"))
        ):
            raise ValueError("private package manifest is invalid")
        raw_files = raw.get("files", [])
        if not isinstance(raw_files, list):
            raise ValueError("private package file manifest is invalid")
        files = tuple(PrivateFile.from_dict(item) for item in raw_files)
        targets = [item.target.casefold() for item in files]
        if len(targets) != len(set(targets)):
            raise ValueError("private package manifest contains conflicting targets")

        raw_releases = raw.get("releases")
        if not isinstance(raw_releases, list) or len(raw_releases) > 1:
            raise ValueError("private package releases are invalid")
        normalized_releases: list[dict[str, Any]] = []
        archives: list[PrivateArchive] = []
        for raw_release in raw_releases:
            if not isinstance(raw_release, dict):
                raise ValueError("private package release is invalid")
            raw_assets = raw_release.get("assets")
            if not isinstance(raw_assets, list) or len(raw_assets) > 1:
                raise ValueError("private package release assets are invalid")
            normalized_release = dict(raw_release)
            if raw_assets:
                archive = PrivateArchive.from_dict(raw_assets[0], package_id)
                archives.append(archive)
                normalized_asset = dict(raw_assets[0])
                normalized_asset["download_url"] = normalized_asset.pop("download_path")
                normalized_release["assets"] = [normalized_asset]
            else:
                normalized_release["assets"] = []
            normalized_releases.append(normalized_release)

        normalized = dict(raw)
        normalized.pop("schema_version", None)
        normalized.pop("files", None)
        normalized["repository"] = ""
        normalized["release"] = {
            "include_prerelease": True,
            "version_pattern": "^(.+)$",
            "assets": {"include": ["*"], "exclude": []},
        }
        normalized["releases"] = normalized_releases
        normalized["featured"] = False
        try:
            package = RegistryPackage.from_dict(normalized)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"private package registry metadata is invalid: {exc}") from exc
        return cls(package, tuple(archives), files, json.loads(json.dumps(raw)))

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.wire))

    def namespaced_package(self, server_id: str, local_ids: set[str]) -> RegistryPackage:
        def namespace_relationships(items: tuple[dict[str, str], ...]) -> tuple[dict[str, str], ...]:
            return tuple(
                {
                    **item,
                    "id": f"{server_id}:{item['id']}" if item.get("id") in local_ids else item["id"],
                }
                for item in items
            )

        return replace(
            self.package,
            id=f"{server_id}:{self.package.id}",
            dependencies=namespace_relationships(self.package.dependencies),
            recommendations=tuple(
                f"{server_id}:{item}" if item in local_ids else item
                for item in self.package.recommendations
            ),
        )


@dataclass(frozen=True)
class PrivateCatalogSnapshot:
    synced_at: int
    packages: tuple[PrivatePackageManifest, ...]
    entitlements: dict[str, Any]
    key_status: dict[str, Any] | None = None
    signing_identity: dict[str, Any] | None = None
