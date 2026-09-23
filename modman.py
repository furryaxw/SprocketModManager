#!/usr/bin/env python3
"""Sprocket Mod Manager CLI and GUI entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from sprocket_mod_manager.infrastructure.config import ConfigStore
from sprocket_mod_manager.domain.errors import ModManagerError
from sprocket_mod_manager.domain.models import PreparedPlan, ResolutionPlan
from sprocket_mod_manager.application.preparer import PlanPreparer
from sprocket_mod_manager.application.local_mods import scan_local_mods, summarize
from sprocket_mod_manager.application.service import ModManagerService, default_app_dir
from sprocket_mod_manager.infrastructure.app_logging import configure_logging
from sprocket_mod_manager.infrastructure.defaults import DEFAULT_INDEX_URL
from sprocket_mod_manager.infrastructure.mod_toggle import apply_enabled, canonical_relative, resolve_mod_path
from sprocket_mod_manager.infrastructure.manager_paths import state_file_path
from sprocket_mod_manager.infrastructure.state import StateStore
from sprocket_mod_manager.infrastructure.suppression_store import store_for
from sprocket_mod_manager.application.integrity import suppression_key, suppression_keys
from sprocket_mod_manager.infrastructure.self_update import (
    SELF_UPDATE_FLAG,
    cleanup_staged,
    frozen_executable,
    self_update_mode,
)

APP_VERSION = "0.5.1"
LOGGER = logging.getLogger(__name__)


def run_self_update_child(argv: list[str]) -> int:
    """自更新的换壳子进程：等旧进程退出，用自己把它换掉，再启动新的它。"""
    app_dir = default_app_dir()
    if "--app-dir" in argv:
        index = argv.index("--app-dir")
        if index + 1 < len(argv):
            app_dir = Path(argv[index + 1]).expanduser()
    configure_logging(app_dir, debug=False, console=False)
    LOGGER.info("self-update child starting argv=%s", argv)
    return self_update_mode(argv)


def _prepared_dict(prepared: PreparedPlan) -> dict:
    return {
        "root": prepared.resolution.root_id,
        "packages": [
            {
                "id": item.resolved.package.id,
                "version": str(item.resolved.release.version),
                "assets": [
                    {
                        "name": asset.asset.name,
                        "sha256": asset.sha256,
                        "publisher_verified": asset.publisher_verified,
                    }
                    for asset in item.assets
                ],
                "files": [
                    {"source": file.source_name, "target": file.target, "sha256": file.sha256}
                    for file in item.files
                ],
                "ignored": item.ignored_files,
            }
            for item in prepared.packages
        ],
    }


def _print_plan(service: ModManagerService, plan: ResolutionPlan) -> None:
    print(f"Install plan for {plan.root_id}:")
    for item in plan.packages:
        assets = service.github.install_assets(item.package, item.release)
        names = ", ".join(asset.name for asset in assets)
        print(f"  {item.package.id} {item.release.tag} [{names}]")


def _load_service(args: argparse.Namespace) -> tuple[ModManagerService, dict, ConfigStore]:
    app_dir = Path(args.app_dir).expanduser() if args.app_dir else default_app_dir()
    config_store = ConfigStore(app_dir)
    config = config_store.load()
    source = args.index_file or args.index or config.get("index_url") or DEFAULT_INDEX_URL
    service = ModManagerService(app_dir=app_dir)
    service.load_registry(Path(source) if args.index_file else source, refresh=args.refresh)
    return service, config, config_store


def _game_path(args: argparse.Namespace, config: dict) -> Path:
    value = args.game_path or config.get("game_path")
    if not value:
        raise ModManagerError("Sprocket game path is not configured; use --game-path")
    return Path(value)


def _suppressed_entries(store: ConfigStore, config: dict) -> list[str]:
    """抑制名单：存在游戏目录的 `SprocketModManager/suppression.json`。"""
    value = config.get("game_path")
    if not isinstance(value, str) or not value.strip():
        return []
    return store_for(Path(value).expanduser()).load()


def _local_mods(
        service: ModManagerService,
        game_path: Path,
        *,
        hashes: bool = False,
        suppressed: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """本地 DLL 清单：静态元数据 + Registry 匹配 + 安装记录归属 + 禁用状态。"""
    installed = service.installed(game_path, suppressed=suppressed or ())
    managed = {
        relative.replace("\\", "/").casefold(): package_id
        for package_id, info in installed.items()
        for relative in info.get("files", ())
        if isinstance(relative, str)
    }
    registry = service.registry
    packages = registry.packages if registry is not None else ()
    mods = scan_local_mods(game_path, managed, packages, compute_hashes=hashes)
    return [mod.as_dict() for mod in mods], summarize(mods)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sprocket Mod Manager")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    parser.add_argument("--debug", action="store_true", help="enable verbose diagnostic logging")
    parser.add_argument("--app-dir", help="manager data directory")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--index", help="HTTPS registry index URL")
    source.add_argument("--index-file", help="local registry index path")
    parser.add_argument("--game-path", help="Sprocket installation directory")
    parser.add_argument("--refresh", action="store_true", help="bypass short-lived API caches")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("packages", help="list registered packages")
    plan = subparsers.add_parser("plan", help="resolve an install plan")
    plan.add_argument("package")
    plan.add_argument("--range", default="*")
    plan.add_argument("--scan", action="store_true", help="download, verify, and classify assets")
    install = subparsers.add_parser("install", help="install or update a package")
    install.add_argument("package")
    install.add_argument("--range", default="*")
    subparsers.add_parser("installed", help="list managed packages")
    subparsers.add_parser("verify", help="re-hash managed files and report corrupted/modified/missing ones")
    suppress = subparsers.add_parser("suppress", help="stop reporting integrity problems for one managed file")
    suppress.add_argument("path", help="managed file path, e.g. Mods/SprocketModAPI.dll")
    unsuppress = subparsers.add_parser("unsuppress", help="report integrity problems for that file again")
    unsuppress.add_argument("path")
    local_mods = subparsers.add_parser("local-mods", help="list local DLLs with static metadata, registry match and state")
    local_mods.add_argument("--hashes", action="store_true", help="also compute SHA-256 for every DLL (slower)")
    disable = subparsers.add_parser("disable", help="rename a mod DLL to .dll.disable (restart Sprocket to apply)")
    disable.add_argument("path", help="path inside the game directory, e.g. Mods/Example.dll")
    enable = subparsers.add_parser("enable", help="rename a .dll.disable file back to .dll")
    enable.add_argument("path")
    remove = subparsers.add_parser("remove", help="remove a package and orphan dependencies")
    remove.add_argument("package")
    update = subparsers.add_parser("update", help="update one package or all requested packages")
    update.add_argument("package", nargs="?")
    return parser


def _suppression_owner(game_path: Path | None, relative: str) -> str:
    """这个受管文件属于哪个包（只读游戏目录里的 installed.json，不联网）。"""
    if game_path is None:
        return ""
    try:
        state = StateStore(state_file_path(game_path)).load()
    except (ModManagerError, OSError, ValueError):
        return ""
    for package_id, info in (state.get("packages") or {}).items():
        if not isinstance(info, dict):
            continue
        for item in info.get("files") or ():
            if isinstance(item, str) and canonical_relative(item) == relative:
                return str(package_id)
    return ""


def _same_suppression(entry: str, key: str, relative: str) -> bool:
    """`entry` 与 `key` 是否是同一把键（同一个文件的抑制键只有一种写法）。"""
    text = str(entry).strip()
    return bool(text) and suppression_keys([text]) == suppression_keys([key])


def _prune_suppressions(store: ConfigStore, service: ModManagerService, config: dict) -> None:
    """把已失效的抑制条目从名单里删掉（包记录没了、路径也没了）。"""
    stale = set(service.stale_suppressions())
    if not stale:
        return
    value = config.get("game_path")
    if not isinstance(value, str) or not value.strip():
        return
    game_path = Path(value).expanduser()
    entries = store_for(game_path).load()
    kept = [item for item in entries if item.strip() not in stale]
    if len(kept) == len(entries):
        return
    store_for(game_path).save(kept)


def _set_suppressed(args: argparse.Namespace) -> int:
    """把某个文件加入/移出「抑制损坏提示」名单（写游戏目录的 `SprocketModManager/suppression.json`）。

    键跟着**身份**走（`<package id>:<文件名>`），无归属的本地文件才用规范路径；
    抑制只影响提示，判定本身永远是实时算的。命令不加载 Registry。
    """
    app_dir = Path(args.app_dir).expanduser() if args.app_dir else default_app_dir()
    store = ConfigStore(app_dir)
    config = store.load()
    relative = canonical_relative(str(args.path or "").strip())
    if not relative:
        raise ModManagerError("需要给出要抑制的文件路径，例如 Mods/SprocketModAPI.dll")

    game_path = _game_path(args, config)
    key = suppression_key(relative, _suppression_owner(game_path, relative))

    entries = store_for(game_path).load()
    kept = [item for item in entries if not _same_suppression(item, key, relative)]
    if args.command == "suppress":
        kept.append(key)

    suppression_store = store_for(game_path)
    suppression_store.save(kept)
    saved = suppression_store.load()
    if args.json:
        print(json.dumps({"suppressed": saved}, ensure_ascii=False, indent=2))
    else:
        verb = "suppressed" if args.command == "suppress" else "unsuppressed"
        print(f"{verb}: {key}")
    return 0


def cli_main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    LOGGER.info("CLI command started command=%s debug=%s", args.command, args.debug)
    try:
        if args.command in ("suppress", "unsuppress"):
            return _set_suppressed(args)
        service, config, config_store = _load_service(args)
        registry = service.registry
        assert registry is not None
        if args.command == "packages":
            rows = []
            for package in registry.packages:
                releases = service.github.releases(package, refresh=args.refresh)
                latest = releases[0] if releases else None
                rows.append(
                    {
                        "id": package.id,
                        "name": package.name,
                        "latest": str(latest.version) if latest else None,
                        "repository": package.repository,
                    }
                )
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    print(f"{row['id']}  {row['latest'] or '-'}  {row['repository']}")
        elif args.command == "plan":
            plan = service.resolve(args.package, args.range)
            if args.scan:
                prepared = service.prepare(plan, progress=None)
                try:
                    result = _prepared_dict(prepared)
                finally:
                    PlanPreparer.discard(prepared)
            else:
                result = plan
            if args.json:
                print(json.dumps(result if isinstance(result, dict) else _plan_for(service, result), ensure_ascii=False, indent=2))
            elif isinstance(result, dict):
                for item in result["packages"]:
                    print(f"{item['id']} {item['version']}")
                    for file in item["files"]:
                        print(f"  {file['source']} -> {file['target']}")
            else:
                _print_plan(service, result)
        elif args.command == "install":
            game_path = _game_path(args, config)
            plan = service.resolve(args.package, args.range)
            _print_plan(service, plan)
            installed_plan, warnings = service.install(
                args.package,
                game_path,
                version_range=args.range,
                progress=lambda message: print(message),
            )
            print(f"Installed {installed_plan.root_id}")
            for warning in warnings:
                print(f"warning: {warning}")
        elif args.command == "installed":
            packages = service.installed(_game_path(args, config), suppressed=_suppressed_entries(config_store, config))
            _prune_suppressions(config_store, service, config)
            if args.json:
                print(json.dumps(packages, ensure_ascii=False, indent=2))
            else:
                for package_id, info in sorted(packages.items()):
                    marker = "requested" if info.get("requested") else "dependency"
                    integrity = info.get("integrity") or "-"
                    print(f"{package_id}  {info.get('version')}  {marker}  integrity={integrity}")
        elif args.command == "verify":
            result = service.verify_installed(
                _game_path(args, config),
                suppressed=_suppressed_entries(config_store, config),
            )
            _prune_suppressions(config_store, service, config)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"checked {result['checked']} files")
                for relative in result["corrupted"]:
                    print(f"corrupted: {relative}")
                for relative in result.get("modified", []):
                    print(f"modified: {relative}")
                for relative in result["missing"]:
                    print(f"missing: {relative}")
                if not result["corrupted"] and not result.get("modified") and not result["missing"]:
                    print("all managed files still match a published release and the install record")
        elif args.command == "local-mods":
            game_path = _game_path(args, config)
            rows, summary = _local_mods(
                service,
                game_path,
                hashes=bool(getattr(args, "hashes", False)),
                suppressed=_suppressed_entries(config_store, config),
            )
            _prune_suppressions(config_store, service, config)
            if args.json:
                print(json.dumps({"mods": rows, "summary": summary}, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    state = "disabled" if row["disabled"] else "loaded"
                    missing = row.get("missing_dependencies") or []
                    print(
                        f"{row['path']}  {row['display_name']}  {row['version'] or '-'}  "
                        f"{row['kind']}  {state}  registry={row['registry_id'] or '-'}"
                        + (f"  missing={','.join(missing)}" if missing else "")
                    )
                print(
                    "total={total} disabled={disabled} registry_matched={registry_matched} "
                    "unmanaged={unmanaged} unreadable={unreadable} missing_dependencies={missing_dependencies}".format(**summary)
                )
        elif args.command in {"disable", "enable"}:
            game_path = _game_path(args, config)
            # 只给基本名就行：文件当前是 `.dll` 还是 `.dll.disable` 由解析器判断，且幂等。
            target = resolve_mod_path(game_path, args.path)
            new_path = apply_enabled(target, args.command == "enable")
            relative = new_path.relative_to(game_path).as_posix()
            # 和 GUI 的 toggle_mod 一样同步安装记录：否则状态里留着旧路径，
            # 下一次刷新就会出现"包显示已安装但文件不存在"（幽灵条目）。
            try:
                service.rename_managed_file(
                    game_path,
                    target.relative_to(game_path).as_posix(),
                    relative,
                    disabled=args.command == "disable",
                )
            except ModManagerError as exc:
                LOGGER.warning("could not sync installed state after renaming %s: %s", target.name, exc)
            print(f"{relative}  (restart Sprocket to apply)")
        elif args.command == "remove":
            removed, warnings = service.remove(args.package, _game_path(args, config))
            print("Removed: " + ", ".join(removed))
            for warning in warnings:
                print(f"warning: {warning}")
        elif args.command == "update":
            game_path = _game_path(args, config)
            installed = service.installed(game_path)
            if args.package:
                targets = [registry.resolve_identifier(args.package).id]
            else:
                targets = [package_id for package_id, info in installed.items() if info.get("requested")]
            changed = 0
            for package_id in targets:
                plan = service.resolve(package_id)
                latest = plan.by_id()[package_id].release.version
                current = installed.get(package_id, {}).get("version")
                if current == str(latest):
                    continue
                service.install(package_id, game_path, progress=lambda message: print(message))
                print(f"Updated {package_id}: {current or '-'} -> {latest}")
                changed += 1
            if not changed:
                print("No updates available")
        return 0
    except ModManagerError as exc:
        LOGGER.warning("CLI command failed command=%s error=%s", args.command, exc)
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _plan_for(service: ModManagerService, plan: ResolutionPlan) -> dict:
    data = {
        "root": plan.root_id,
        "packages": [],
    }
    for item in plan.packages:
        data["packages"].append(
            {
                "id": item.package.id,
                "name": item.package.name,
                "version": str(item.release.version),
                "tag": item.release.tag,
                "dependencies": list(item.dependency_ids),
                "assets": [
                    {
                        "id": asset.id,
                        "name": asset.name,
                        "size": asset.size,
                        "digest": asset.digest,
                    }
                    for asset in service.github.install_assets(item.package, item.release)
                ],
            }
        )
    return data


def main() -> int:
    argv = sys.argv[1:]
    # 换壳子进程：参数是裸路径（不是子命令），必须在 argparse 之前拦下。
    if argv and argv[0] == SELF_UPDATE_FLAG:
        return run_self_update_child(argv)
    debug_flag = "--debug" in argv
    cli_marker = "--cli" in argv
    cli_args = [argument for argument in argv if argument != "--cli"]
    is_cli = cli_marker or any(argument != "--debug" for argument in cli_args)
    app_dir = default_app_dir()
    if "--app-dir" in cli_args:
        index = cli_args.index("--app-dir")
        if index + 1 < len(cli_args):
            app_dir = Path(cli_args[index + 1]).expanduser()
    else:
        inline_app_dir = next(
            (argument.partition("=")[2] for argument in cli_args if argument.startswith("--app-dir=")),
            "",
        )
        if inline_app_dir:
            app_dir = Path(inline_app_dir).expanduser()
    config_debug = ConfigStore(app_dir).load().get("debug") is True
    debug = debug_flag or config_debug
    configure_logging(app_dir, debug=debug, console=is_cli)
    executable = frozen_executable()
    if executable is not None:
        cleanup_staged(executable)
    LOGGER.info(
        "Sprocket Mod Manager %s starting mode=%s debug=%s debug_flag=%s config_debug=%s app_dir=%s",
        APP_VERSION,
        "cli" if is_cli else "gui",
        debug,
        debug_flag,
        config_debug,
        app_dir,
    )
    if is_cli:
        return cli_main(cli_args)
    try:
        from sprocket_mod_manager.presentation.webview_app import run_gui
    except ImportError as exc:
        LOGGER.exception("GUI dependencies are unavailable")
        print(f"GUI dependencies are unavailable: {exc}", file=sys.stderr)
        return 1
    try:
        run_gui(APP_VERSION, debug=debug, debug_override=debug_flag)
    except Exception:
        LOGGER.exception("GUI terminated with an unhandled error")
        raise
    LOGGER.info("Sprocket Mod Manager stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
