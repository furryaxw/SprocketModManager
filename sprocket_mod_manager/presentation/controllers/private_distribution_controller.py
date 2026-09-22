from __future__ import annotations

import time
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from .base import ApiController
from ..api_support import release_data as _release_data, startup_trace as _startup_trace
from ...application.solver import DependencySolver
from ...domain.errors import ModManagerError
from ...domain.models import PreparedFile, ProgressCallback, RegistryPackage, ReleaseAsset, ReleaseInfo, ResolutionPlan
from ...domain.registry import Registry
from ...domain.semver import Version
from ...infrastructure.private_servers import (
    DeveloperServerClient,
    DeveloperServerError,
    GITHUB_OAUTH_CLIENT_ID,
    PrivatePackageManifest,
    github_current_user,
    github_device_poll,
    github_device_start,
    github_gist_sync,
    normalize_server_url,
)
from ...utilities.checksums import sha256_file
from ...utilities.package_paths import validate_target
from ...utilities.signatures import verify_key_status_snapshot


class PrivateDistributionController(ApiController):
    _SERVER_INFO_CACHE_SECONDS = 15
    _GITHUB_LOGIN_CHECK_SECONDS = 60

    def _cached_server_clients(self) -> dict[tuple[str, str, str], tuple[float, DeveloperServerClient, Any]]:
        cache = getattr(self, "_server_client_cache", None)
        if cache is None:
            cache = {}
            self._server_client_cache = cache
        return cache

    def _developer_server_entries(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        entries = self.config.get("developer_servers", [])
        if not isinstance(entries, list):
            return []
        cleaned: list[dict[str, Any]] = []
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            if item.get("deleted") is True and not include_deleted:
                continue
            cleaned.append(item)
        return cleaned

    def _server_session_token(self, entry: dict[str, Any]) -> str:
        return self.credentials.load(str(entry.get("server_id", "")))

    def _save_server_session_token(self, entry: dict[str, Any], token: str) -> None:
        target = self.credentials.save(str(entry["server_id"]), token)
        entry["session_credential"] = target

    def set_demo_github_login(self, github_user_id: str) -> dict[str, Any]:
        user_id = str(github_user_id).strip()
        if user_id and not user_id.isdigit():
            return self._failure(ValueError("demo GitHub user id must contain digits only"), code="github_login_failed")
        self.config["github_user_id"] = user_id
        try:
            self.config_store.save(self.config)
            return self._success(github_user_id=user_id)
        except OSError as exc:
            return self._failure(exc, code="github_login_failed")

    def _github_token(self) -> str:
        return self._github_access_token or self.credentials.load("github-access-token")

    def _clear_github_login(self) -> None:
        self._github_access_token = ""
        self.config["github_user_id"] = ""
        self._github_login_checked_token = ""
        self._github_login_checked_at = 0.0
        try:
            self.credentials.delete("github-access-token")
        except (OSError, ValueError):
            pass
        self.config_store.save(self.config)

    def _verify_github_login(self, *, force: bool = False) -> dict[str, Any]:
        token = self._github_token()
        if not token:
            # Demo servers intentionally use the configured numeric identity
            # without a GitHub OAuth credential, so there is nothing to verify.
            return self._success(
                logged_in=bool(self.config.get("github_user_id")),
                github_user_id=str(self.config.get("github_user_id", "") or ""),
            )
        checked_token = getattr(self, "_github_login_checked_token", "")
        checked_at = float(getattr(self, "_github_login_checked_at", 0.0))
        if not force and token == checked_token and (
                time.monotonic() - checked_at < self._GITHUB_LOGIN_CHECK_SECONDS
        ):
            return self._success(
                logged_in=True,
                github_user_id=str(self.config.get("github_user_id", "") or ""),
            )
        try:
            identity = github_current_user(token)
        except ValueError as exc:
            if str(exc) == "GitHub access token is invalid":
                self._clear_github_login()
                return self._failure(exc, code="github_login_expired")
            return self._failure(exc, code="github_login_check_failed")
        user_id = str(identity["id"])
        self._github_login_checked_token = token
        self._github_login_checked_at = time.monotonic()
        if self.config.get("github_user_id") != user_id:
            self.config["github_user_id"] = user_id
            self.config_store.save(self.config)
        return self._success(logged_in=True, github_user_id=user_id)

    def _reconnect_developer_servers(self) -> list[str]:
        token = self._github_token()
        if not token:
            return []
        refreshed: list[dict[str, Any]] = []
        connected: list[str] = []
        for raw in self._developer_server_entries():
            item = dict(raw)
            if item.get("url"):
                try:
                    exchanged = DeveloperServerClient(str(item["url"])).exchange_github_token(token)
                    self._save_server_session_token(item, str(exchanged["token"]))
                    connected.append(str(item["server_id"]))
                except (OSError, ValueError, TypeError):
                    pass
            refreshed.append(item)
        self.config["developer_servers"] = refreshed
        self.config_store.save(self.config)
        return connected

    def _background_github_refresh(self) -> None:
        _startup_trace("GitHub login refresh: entered")
        login = self._verify_github_login(force=True)
        if not login.get("ok"):
            _startup_trace(f"GitHub login refresh: returned ok=False code={login.get('code', '')}")
            return
        if not login.get("logged_in"):
            _startup_trace("GitHub login refresh: skipped without a saved token")
            return
        result = self.sync_github_gist()
        connected = self._reconnect_developer_servers()
        _startup_trace(
            f"GitHub login refresh: gist_ok={result.get('ok') is True} reconnected={len(connected)}",
        )

    def start_github_device_login(self) -> dict[str, Any]:
        try:
            device = github_device_start(GITHUB_OAUTH_CLIENT_ID)
            self._github_device = {
                "client_id": GITHUB_OAUTH_CLIENT_ID,
                "device_code": str(device["device_code"]),
                "interval": max(5, int(device.get("interval", 5))),
            }
            return self._success(
                user_code=str(device["user_code"]),
                verification_uri=str(device.get("verification_uri")),
                expires_in=int(device.get("expires_in", 900)),
                interval=self._github_device["interval"],
            )
        except (OSError, ValueError, TypeError) as exc:
            return self._failure(exc, code="github_login_failed")

    def poll_github_device_login(self) -> dict[str, Any]:
        if not self._github_device:
            return self._failure(ValueError("GitHub device login is not started"), code="github_login_failed")
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
                return self._failure(ValueError(str(result.get("error_description") or error)),
                                     code="github_login_failed")
            access_token = str(result.get("access_token", "")).strip()
            if not access_token:
                raise ValueError("GitHub did not return an access token")
            identity = github_current_user(access_token)
            user_id = str(identity["id"])
            self._github_device = None
            self._github_access_token = access_token
            self._github_login_checked_token = access_token
            self._github_login_checked_at = time.monotonic()
            try:
                self.credentials.save("github-access-token", access_token)
            except (OSError, ValueError):
                pass
            self.config["github_user_id"] = user_id
            self.config_store.save(self.config)
            synced = self.sync_github_gist()
            reconnected = self._reconnect_developer_servers()
            return self._success(
                logged_in=True,
                github_user_id=user_id,
                conflicts=synced.get("conflicts", []) if synced.get("ok") else [],
                reconnected_server_ids=reconnected,
            )
        except (OSError, ValueError, TypeError) as exc:
            return self._failure(exc, code="github_login_failed")

    def cancel_github_device_login(self) -> dict[str, Any]:
        self._github_device = None
        return self._success()

    def sync_github_gist(self) -> dict[str, Any]:
        token = self._github_token()
        if not token:
            return self._failure(ValueError("GitHub login is required"), code="gist_sync_requires_login")
        try:
            entries = self._developer_server_entries(include_deleted=True)
            gist_id, merged, conflicts = github_gist_sync(
                token,
                entries,
                str(self.config.get("github_gist_id", "") or ""),
                return_conflicts=True,
            )
            self._gist_conflicts = conflicts
            self.config["github_gist_id"] = gist_id
            self.config["developer_servers"] = merged
            self.config_store.save(self.config)
            return self._success(gist_id=gist_id, developer_servers=merged, conflicts=conflicts)
        except (OSError, ValueError, TypeError) as exc:
            return self._failure(exc, code="gist_sync_failed")

    def resolve_github_gist_conflicts(self, server_ids: list[str]) -> dict[str, Any]:
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
            self.config["developer_servers"] = list(by_id.values())
            self.config_store.save(self.config)
            result = self.sync_github_gist()
            if not result.get("ok"):
                return result
            return self._success(
                developer_servers=result.get("developer_servers", []),
                conflicts=result.get("conflicts", []),
            )
        except (OSError, ValueError, TypeError) as exc:
            return self._failure(exc, code="gist_conflict_resolution_failed")

    def _save_developer_servers(self, entries: list[dict[str, Any]]) -> None:
        self.config["developer_servers"] = entries
        self.config_store.save(self.config)

    def _trusted_server_client(
            self,
            entry: dict[str, Any],
            *,
            session_token: str = "",
    ) -> tuple[DeveloperServerClient, Any]:
        trusted_identity = entry.get("signing_identity")
        fingerprint = str(entry.get("public_key_fingerprint", ""))
        cache_key = (str(entry.get("server_id", "")), str(session_token), fingerprint)
        cached = self._cached_server_clients().get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < self._SERVER_INFO_CACHE_SECONDS:
            return cached[1], cached[2]
        client = DeveloperServerClient(
            str(entry["url"]),
            session_token=session_token,
            trusted_signing_identity=(
                dict(trusted_identity) if isinstance(trusted_identity, dict) else None
            ),
        )
        # Unsigned developer servers use the protocol path without identity
        # negotiation; signed servers validate identity before package access.
        if not isinstance(trusted_identity, dict) and not str(entry.get("public_key_fingerprint", "")):
            return client, None
        info = client.info()
        advertised = info.signing_identity
        advertised_fingerprint = (
            str(advertised.get("fingerprint", "")) if isinstance(advertised, dict) else ""
        )
        stored_fingerprint = str(entry.get("public_key_fingerprint", ""))
        changed = False
        if advertised is not None and trusted_identity is None:
            if not stored_fingerprint or stored_fingerprint != advertised_fingerprint:
                raise ValueError("developer server signing identity requires fingerprint confirmation")
            entry["signing_identity"] = dict(advertised)
            changed = True
        if client.rotation_applied:
            entry["signing_identity"] = dict(advertised)
            entry["public_key_fingerprint"] = advertised_fingerprint
            entry["updated_at"] = int(time.time())
            changed = True
        if changed:
            entries = self._developer_server_entries(include_deleted=True)
            updated = [
                dict(entry) if item.get("server_id") == entry.get("server_id") else item
                for item in entries
            ]
            self._save_developer_servers(updated)
        self._cached_server_clients()[cache_key] = (time.monotonic(), client, info)
        return client, info

    @staticmethod
    def _validate_cached_key_status(
        entry: dict[str, Any],
        snapshot: Any,
    ) -> None:
        identity = entry.get("signing_identity")
        stored_fingerprint = str(entry.get("public_key_fingerprint", ""))
        if not isinstance(identity, dict) and not stored_fingerprint:
            return
        if not isinstance(snapshot.signing_identity, dict):
            raise ValueError("cached developer server signing identity is missing")
        expected_fingerprint = (
            str(identity.get("fingerprint", "")) if isinstance(identity, dict) else stored_fingerprint
        )
        if snapshot.signing_identity.get("fingerprint") != expected_fingerprint:
            raise ValueError("cached developer server signing identity does not match")
        verify_key_status_snapshot(
            snapshot.key_status,
            snapshot.signing_identity,
            server_id=str(entry.get("server_id", "")),
        )

    @staticmethod
    def _private_package_data(
            entry: dict[str, Any],
            manifest: PrivatePackageManifest,
            *,
            cached: bool = False,
            synced_at: int | None = None,
    ) -> dict[str, Any]:
        package = manifest.package
        release = manifest.release
        archive = manifest.archive.to_dict() if manifest.archive is not None else None
        return {
            "id": f"{entry['server_id']}:{manifest.id}",
            "private_package_id": manifest.id,
            "name": package.name,
            "display_name": dict(package.display_name),
            "description": dict(package.description),
            "authors": list(package.authors),
            "repository": "",
            "repository_url": "",
            "license": package.license,
            "category": package.category,
            "tags": list(package.tags),
            "dependencies": [dict(item) for item in package.dependencies],
            "recommendations": list(package.recommendations),
            "featured": False,
            "private": True,
            "server_id": entry["server_id"],
            "server_name": entry["name"],
            "server_url": entry["url"],
            "release": _release_data(release),
            "install_assets": [archive["name"]] if archive else [],
            "archive": archive,
            "adoption_files": [item.to_dict() for item in manifest.files],
            "cached": cached,
            "synced_at": synced_at,
            "installed": None,
        }

    def _adopt_private_packages(self, packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            game_path = self._valid_game_path()
        except (OSError, ValueError):
            return []
        service = self._current_service()
        installer = service._installer_for(game_path)
        installed = installer.state_store.load()["packages"]
        adopted: list[dict[str, Any]] = []
        candidates: list[tuple[dict[str, Any], list[PreparedFile], list[ReleaseAsset]]] = []
        for data in packages:
            package_id = str(data.get("id", ""))
            if not package_id or package_id in installed or data.get("cached") is True:
                continue
            files: list[PreparedFile] = []
            assets: list[ReleaseAsset] = []
            valid = True
            for index, raw in enumerate(data.get("adoption_files", []), start=1):
                if not isinstance(raw, dict):
                    valid = False
                    break
                name = str(raw.get("name", "")).strip()
                relative = str(raw.get("target", "")).strip().replace("\\", "/")
                expected = str(raw.get("sha256", "")).strip().lower()
                try:
                    validated_target = validate_target(relative)
                except ModManagerError:
                    valid = False
                    break
                relative = validated_target.as_posix()
                target = game_path / Path(*validated_target.parts)
                if (
                        not name
                        or Path(name).name != name
                        or Path(relative).name != name
                        or not (relative.startswith("Mods/") or relative.startswith("UserLibs/"))
                        or Path(name).suffix.casefold() != ".dll"
                        or len(expected) != 64
                        or not target.is_file()
                ):
                    valid = False
                    break
                try:
                    actual = sha256_file(target)
                except OSError:
                    valid = False
                    break
                if actual != expected:
                    valid = False
                    break
                files.append(PreparedFile(package_id, target, name, relative, expected))
                assets.append(
                    ReleaseAsset(index, name, target.stat().st_size, str(data["server_url"]), f"sha256:{expected}"))
            if not valid or not files:
                continue
            candidates.append((data, files, assets))

        claimed_targets: dict[str, set[str]] = {}
        for data, files, _assets in candidates:
            package_id = str(data["id"])
            for file in files:
                claimed_targets.setdefault(file.target.casefold(), set()).add(package_id)
        ambiguous = {
            package_id
            for owners in claimed_targets.values()
            if len(owners) > 1
            for package_id in owners
        }

        for data, files, assets in candidates:
            package_id = str(data["id"])
            if package_id in ambiguous:
                continue
            try:
                release_data = data.get("release") or {}
                version = Version.parse(str(release_data.get("version", "")))
                release = ReleaseInfo(0, str(version), version, True, "", tuple(assets))
                package = RegistryPackage(
                    id=package_id,
                    name=str(data.get("name", package_id)),
                    authors=tuple(str(item) for item in data.get("authors", [])),
                    repository="",
                    license="Private distribution",
                    display_name=dict(data.get("display_name", {})),
                    description=dict(data.get("description", {})),
                    release={},
                    dependencies=(),
                    install={"scan_dlls": True, "exclude": [], "overrides": []},
                    category="other",
                    tags=("private",),
                )
                if installer.adopt(package, release, tuple(files), tuple(assets), (), game_path):
                    adopted.append({"id": package_id, "name": package.name, "version": str(version)})
                    installed[package_id] = {"version": str(version)}
            except (ModManagerError, OSError, ValueError):
                continue
        return adopted

    def _developer_servers_data(self, *, load_packages: bool = True) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for entry in self._developer_server_entries():
            data = {**entry, "status": "registered", "packages": []}
            user_id = str(self.config.get("github_user_id", "") or "")
            session_token = self._server_session_token(entry)
            identity = user_id or ("session" if session_token else "")
            if load_packages and identity:
                try:
                    client, info = self._trusted_server_client(entry, session_token=session_token)
                    entitlements = client.entitlements(user_id)
                    key_status = client.key_status_snapshot() if client.has_signing_identity else None
                    packages = client.packages(user_id)
                    states = {str(item.get("state", "")) for item in entitlements.get("grants", [])}
                    data["status"] = (
                        "active" if entitlements.get("permissions")
                        else "expired" if "expired" in states
                        else "revoked" if "revoked" in states
                        else "registered"
                    )
                    data["entitlements"] = entitlements
                    data["packages"] = [self._private_package_data(entry, item) for item in packages]
                    try:
                        self.private_catalog_cache.save(
                            str(entry["server_id"]), identity, packages, entitlements,
                            key_status=key_status,
                            signing_identity=info.signing_identity if info is not None else None,
                        )
                    except OSError:
                        pass
                except (OSError, ValueError) as exc:
                    message = str(exc)
                    data["status"] = (
                        "reauth_required"
                        if isinstance(exc, DeveloperServerError) and exc.code == "invalid_session"
                        else "offline"
                    )
                    data["error"] = message
                    snapshot = self.private_catalog_cache.load(str(entry.get("server_id", "")), identity)
                    if snapshot is not None:
                        try:
                            self._validate_cached_key_status(entry, snapshot)
                        except ValueError as cache_exc:
                            data["cache_error"] = str(cache_exc)
                        else:
                            data["cached"] = True
                            data["synced_at"] = snapshot.synced_at
                            data["entitlements"] = snapshot.entitlements
                            data["packages"] = [
                                self._private_package_data(
                                    entry, item, cached=True, synced_at=snapshot.synced_at
                                )
                                for item in snapshot.packages
                            ]
            result.append(data)
        return result

    def get_developer_servers(self) -> dict[str, Any]:
        self.config = self.config_store.load()
        login = self._verify_github_login()
        servers = self._developer_servers_data()
        packages = [package for server in servers for package in server.get("packages", [])]
        adopted = self._adopt_private_packages(packages)
        installed = self._installed()
        for package in packages:
            package["installed"] = self._installed_entry(installed.get(package["id"]))
        return self._success(
            servers=servers,
            packages=packages,
            adopted=adopted,
            github_login_expired=login.get("code") == "github_login_expired",
            github_user_id=str(self.config.get("github_user_id", "") or ""),
        )

    def add_developer_server(self, url: str, confirmed_fingerprint: str = "") -> dict[str, Any]:
        try:
            normalized = normalize_server_url(url)
            info = DeveloperServerClient(normalized).info()
            signing_identity = info.signing_identity
            fingerprint = str(signing_identity.get("fingerprint", "")) if isinstance(signing_identity, dict) else ""
            if fingerprint and str(confirmed_fingerprint).strip() != fingerprint:
                return self._success(
                    requires_confirmation=True,
                    server={"server_id": info.server_id, "name": info.name, "url": normalized},
                    trust_method=info.trust_method,
                    manual_transport=info.manual_transport,
                    signing_identity=signing_identity,
                )
            entries = self._developer_server_entries()
            if any(item.get("server_id") == info.server_id for item in entries):
                raise ValueError("developer server is already registered")
            entry = {
                "server_id": info.server_id,
                "name": info.name,
                "operator": info.operator,
                "url": normalized,
                "demo_auth": info.demo_auth,
                "public_key_fingerprint": fingerprint,
                "signing_identity": dict(signing_identity) if isinstance(signing_identity, dict) else None,
            }
            if self._github_token():
                exchanged = DeveloperServerClient(normalized).exchange_github_token(
                    self._github_token()
                )
                self._save_server_session_token(entry, str(exchanged["token"]))
                self.config["github_user_id"] = str(
                    exchanged.get("github_user_id") or self.config.get("github_user_id", "")
                )
            entries.append(entry)
            self._save_developer_servers(entries)
            if self._github_token():
                self.sync_github_gist()
            return self._success(server={**entry, "status": "registered", "packages": []})
        except DeveloperServerError as exc:
            if exc.status == 401 and exc.code in {
                "github_token_rejected", "github_identity_rejected"
            }:
                self._github_access_token = ""
                self.config["github_user_id"] = ""
                try:
                    self.credentials.delete("github-access-token")
                    self.config_store.save(self.config)
                except (OSError, ValueError):
                    pass
                return self._failure(
                    ValueError("GitHub login expired; sign in again"),
                    code="github_login_expired",
                )
            return self._failure(exc, code="developer_server_add_failed")
        except (OSError, ValueError) as exc:
            return self._failure(exc, code="developer_server_add_failed")

    def activate_developer_server(self, server_id: str, key: str) -> dict[str, Any]:
        try:
            login = self._verify_github_login()
            if not login.get("ok"):
                return login
            entries = self._developer_server_entries()
            entry = next((item for item in entries if item.get("server_id") == server_id), None)
            if entry is None:
                raise ValueError("developer server is not registered")
            github_user_id = str(self.config.get("github_user_id", "") or "").strip()
            session_token = self._server_session_token(entry)
            if not github_user_id and not session_token:
                raise ValueError("log in to GitHub before activating a key")
            client, info = self._trusted_server_client(entry, session_token=session_token)
            entitlements = client.redeem(key, github_user_id, request_id=uuid.uuid4().hex)
            client.invalidate_response_cache()
            key_status = client.key_status_snapshot() if client.has_signing_identity else None
            manifests = client.packages(github_user_id)
            packages = [self._private_package_data(entry, item) for item in manifests]
            try:
                self.private_catalog_cache.save(
                    str(entry["server_id"]), github_user_id or "session", manifests, entitlements,
                    key_status=key_status,
                    signing_identity=info.signing_identity if info is not None else None,
                )
            except OSError:
                pass
            return self._success(entitlements=entitlements, packages=packages)
        except (OSError, ValueError) as exc:
            return self._failure(exc, code="developer_server_activation_failed")

    def remove_developer_server(self, server_id: str) -> dict[str, Any]:
        entries = self._developer_server_entries(include_deleted=True)
        target = next((item for item in entries if item.get("server_id") == server_id), None)
        if target is None or target.get("deleted") is True:
            return self._failure(ValueError("developer server is not registered"),
                                 code="developer_server_remove_failed")
        target["deleted"] = True
        target["updated_at"] = int(time.time())
        self._save_developer_servers(entries)
        try:
            self.credentials.delete(server_id)
        except (OSError, ValueError):
            pass
        try:
            self.private_catalog_cache.remove(server_id)
        except OSError:
            pass
        if self._github_token():
            threading.Thread(
                target=self._background_gist_sync,
                name="sprocket-delete-gist-sync",
                daemon=True,
            ).start()
        return self._success(sync_pending=bool(self._github_token()))

    def logout_github(self) -> dict[str, Any]:
        self._github_device = None
        self._github_access_token = ""
        self.config["github_user_id"] = ""
        try:
            entries = self._developer_server_entries()
            for entry in entries:
                token = self._server_session_token(entry)
                if token:
                    try:
                        DeveloperServerClient(str(entry["url"]), session_token=token).revoke_session()
                    except (OSError, ValueError):
                        pass
                self.credentials.delete(str(entry.get("server_id", "")))
                entry.pop("session_credential", None)
            self.credentials.delete("github-access-token")
            self.config["developer_servers"] = entries
            self.config_store.save(self.config)
            return self._success()
        except (OSError, ValueError) as exc:
            return self._failure(exc, code="github_logout_failed")

    def _private_package_source(
            self,
            package_id: str,
    ) -> tuple[dict[str, Any], DeveloperServerClient, PrivatePackageManifest]:
        login = self._verify_github_login()
        if not login.get("ok"):
            raise DeveloperServerError(
                str(login.get("message", "GitHub login expired")),
                status=401,
                code="github_login_rejected",
            )
        entry = next(
            (
                item
                for item in self._developer_server_entries()
                if package_id.startswith(str(item.get("server_id", "")) + ":")
            ),
            None,
        )
        if entry is None:
            raise ValueError("private package server is not registered")
        remote_id = package_id[len(str(entry["server_id"])) + 1:]
        user_id = str(self.config.get("github_user_id", "") or "").strip()
        session_token = self._server_session_token(entry)
        if not user_id and not session_token:
            raise ValueError("log in to GitHub before accessing private packages")
        client, _info = self._trusted_server_client(entry, session_token=session_token)
        if client.has_signing_identity:
            client.key_status_snapshot()
        manifest = next(
            (item for item in client.packages(user_id) if item.id == remote_id),
            None,
        )
        if manifest is None:
            raise ValueError("private package is unavailable or permission has expired")
        return entry, client, manifest

    def _private_resolution(
            self,
            package_id: str,
    ) -> tuple[DeveloperServerClient, ResolutionPlan, dict[
        str, Callable[[ReleaseAsset, Path, ProgressCallback | None], Path]]]:
        entry, _client, _manifest = self._private_package_source(package_id)
        user_id = str(self.config.get("github_user_id", "") or "").strip()
        client = _client
        manifests = client.packages(user_id)
        private_versions = {f"{entry['server_id']}:{item.id}": item.version for item in manifests}
        local_ids = {item.id for item in manifests}
        private_packages = [item.namespaced_package(str(entry["server_id"]), local_ids) for item in manifests]
        service = self._current_service()
        public_packages = list(service.registry.packages) if service.registry is not None else []
        registry = Registry(public_packages + private_packages)
        plan = DependencySolver(registry, service.github).resolve(package_id)
        def download_private(
                asset: ReleaseAsset,
                destination: Path,
                progress: ProgressCallback | None,
        ) -> Path:
            if client.has_signing_identity:
                client.key_status_snapshot()
            return client.download_archive(
                asset.download_url,
                user_id,
                destination,
                version=private_versions.get(package_id, ""),
                expected_size=asset.size,
                progress=progress,
            )

        downloaders = {package.id: download_private for package in private_packages}
        return client, plan, downloaders

    @staticmethod
    def _private_plan_data(package_id: str, manifest: PrivatePackageManifest) -> dict[str, Any]:
        return {
            "id": package_id,
            "display_name": {"en": manifest.name, "zh": manifest.name},
            "name": manifest.name,
            "replaces_autotranslator": False,
            "packages": [{
                "id": package_id,
                "display_name": {"en": manifest.name},
                "name": manifest.name,
                "version": manifest.version,
                "tag": manifest.version,
                "assets": [manifest.archive.name] if manifest.archive is not None else [],
            }],
        }
