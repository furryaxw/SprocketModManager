#!/usr/bin/env python3
"""Sprocket Mod Manager CLI and GUI entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sprocket_mod_manager.config import ConfigStore
from sprocket_mod_manager.errors import ModManagerError
from sprocket_mod_manager.models import PreparedPlan, ResolutionPlan
from sprocket_mod_manager.preparer import PlanPreparer
from sprocket_mod_manager.service import DEFAULT_INDEX_URL, ModManagerService, default_app_dir


APP_VERSION = "0.1.0"


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


def _load_service(args: argparse.Namespace) -> tuple[ModManagerService, dict]:
    app_dir = Path(args.app_dir).expanduser() if args.app_dir else default_app_dir()
    config_store = ConfigStore(app_dir)
    config = config_store.load()
    source = args.index_file or args.index or config.get("index_url") or DEFAULT_INDEX_URL
    service = ModManagerService(app_dir=app_dir)
    service.load_registry(Path(source) if args.index_file else source, refresh=args.refresh)
    return service, config


def _game_path(args: argparse.Namespace, config: dict) -> Path:
    value = args.game_path or config.get("game_path")
    if not value:
        raise ModManagerError("Sprocket game path is not configured; use --game-path")
    return Path(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sprocket Mod Manager")
    parser.add_argument("--version", action="version", version=APP_VERSION)
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
    remove = subparsers.add_parser("remove", help="remove a package and orphan dependencies")
    remove.add_argument("package")
    update = subparsers.add_parser("update", help="update one package or all requested packages")
    update.add_argument("package", nargs="?")
    return parser


def cli_main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        service, config = _load_service(args)
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
            packages = service.installed(_game_path(args, config))
            if args.json:
                print(json.dumps(packages, ensure_ascii=False, indent=2))
            else:
                for package_id, info in sorted(packages.items()):
                    marker = "requested" if info.get("requested") else "dependency"
                    print(f"{package_id}  {info.get('version')}  {marker}")
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
                print("All requested packages are up to date")
        return 0
    except ModManagerError as exc:
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
    if argv and argv[0] == "--cli":
        argv = argv[1:]
    if argv:
        return cli_main(argv)
    try:
        from sprocket_mod_manager.gui import run_gui
    except ImportError as exc:
        print(f"GUI dependencies are unavailable: {exc}", file=sys.stderr)
        return 1
    run_gui(APP_VERSION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
