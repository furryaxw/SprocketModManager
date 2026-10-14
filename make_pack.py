#!/usr/bin/env python3
"""Build a mod pack's release files from a pack directory.

The pack directory holds the manifest (`pack.json`) and the payload tree (`overrides/`). This writes
the two release assets — a deterministic zip and the manifest — plus the thin entry skeleton. Upload
both assets to the same release tag; the entry goes in through a pull request once.

```powershell
python make_pack.py --dir my-pack --out dist --id example.realism-pack --name RealismPack `
    --repo ExampleAuthor/RealismPack --author ExampleAuthor --version 1.0.0 --registry packages
```
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

from registry_core.pack_manifest import parse_pack_manifest

MANIFEST_NAME = "pack.json"
OVERRIDES_DIR = "overrides"
ENTRY_NAME = "entry.json"
# 固定时间戳、权限位与创建系统：同一份输入产出逐字节相同的 zip，重新打包不会因为元数据变化而不同。
ZIP_DATE = (2026, 1, 1, 0, 0, 0)
UNIX_CREATE_SYSTEM = 3
FILE_MODE = 0o644 << 16
DEFAULT_TAG_PATTERN = r"^v?([0-9]+\.[0-9]+\.[0-9]+)$"


def load_index_module():
    path = Path(__file__).with_name("gen-index.py")
    spec = importlib.util.spec_from_file_location("sprocket_gen_index", path)
    if not spec or not spec.loader:
        raise RuntimeError("cannot load gen-index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def zip_overrides(root: Path) -> bytes:
    """`overrides/` 打成一个确定性的 zip：名字排序、固定时间戳与权限位。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = UNIX_CREATE_SYSTEM
            info.external_attr = FILE_MODE
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def entry_skeleton(
        *,
        package_id: str,
        name: str,
        repository: str,
        authors: list[str],
        license_text: str,
        include: list[str],
        tag_pattern: str,
) -> dict:
    """条目骨架：只有身份、元数据与抓取规则，成员与落点规则在清单里。"""
    return {
        "$schema": "../../schemas/sprocket-mod.schema.json",
        "schema_version": 3,
        "id": package_id,
        "name": name,
        "kind": "pack",
        "category": "pack",
        "authors": authors,
        "repository": repository,
        "license": license_text,
        "display_name": {"en": name},
        "description": {"en": f"{name} — a set of mods plus the override files they need."},
        "release": {
            "include_prerelease": False,
            "version_pattern": tag_pattern,
            "assets": {"include": include, "exclude": ["*-source.zip", "*symbols*"]},
        },
        "tags": [],
    }


def validate_manifest(manifest: dict, index_module) -> None:
    """清单的成员与规则用注册表自己的校验器过一遍（能离线验的部分）。"""
    if not manifest.get("install"):
        return
    install = manifest["install"]
    if "files" in install:
        index_module.validate_file_rules(install["files"])
    if "payload" in install:
        index_module.validate_payload_rules(install["payload"])
    if "files" in install and "payload" in install:
        raise index_module.RegistryError("a pack must not mix install.files and install.payload")
    index_module.validate_replace_types(install, install.get("mode", "standard"))


def registered_ids(packages_dir: Path) -> set[str]:
    return {
        f"{path.parent.name}.{path.stem}"
        for path in packages_dir.glob("*/*.json")
        if path.is_file()
    }


def build(
        pack_dir: Path,
        out_dir: Path,
        *,
        package_id: str,
        name: str,
        repository: str,
        authors: list[str],
        license_text: str,
        version: str,
        tag_pattern: str = DEFAULT_TAG_PATTERN,
        packages_dir: Path | None = None,
) -> dict:
    manifest_path = pack_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SystemExit(f"missing manifest: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = parse_pack_manifest(manifest_bytes, source=str(manifest_path))
    index_module = load_index_module()
    validate_manifest({"install": manifest.install}, index_module)

    if packages_dir is not None:
        known = registered_ids(packages_dir)
        missing = sorted(
            {member["id"] for member in manifest.members if member["id"] not in known}
        )
        for member_id in missing:
            print(f"warning: member is not registered yet: {member_id}", file=sys.stderr)

    overrides = pack_dir / OVERRIDES_DIR
    payload = zip_overrides(overrides) if overrides.is_dir() else None
    payload_name = f"{name}-{version}.zip"
    include = [f"{name}-*.zip", MANIFEST_NAME] if payload is not None else [MANIFEST_NAME]

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / MANIFEST_NAME).write_bytes(manifest_bytes)
    if payload is not None:
        (out_dir / payload_name).write_bytes(payload)
    entry = entry_skeleton(
        package_id=package_id,
        name=name,
        repository=repository,
        authors=authors,
        license_text=license_text,
        include=include,
        tag_pattern=tag_pattern,
    )
    (out_dir / ENTRY_NAME).write_text(
        json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "manifest": manifest_bytes,
        "manifest_name": MANIFEST_NAME,
        "payload": payload,
        "payload_name": payload_name if payload is not None else "",
        "entry": entry,
    }


def report(result: dict, out_dir: Path) -> None:
    print(f"wrote {out_dir}:")
    for key in ("manifest_name", "payload_name"):
        name = result[key]
        if not name:
            continue
        content = result["manifest"] if key == "manifest_name" else result["payload"]
        digest = hashlib.sha256(content).hexdigest()
        print(f"  {name}  ({len(content)} bytes, sha256 {digest})")
    print(f"  {ENTRY_NAME}  (copy it into packages/<author>/<modid>.json)")
    print("上传时把清单与载荷放在同一个 release tag 下，条目走一次 Pull Request。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a mod pack's release files")
    parser.add_argument("--dir", default=".", help="pack directory (pack.json + overrides/)")
    parser.add_argument("--out", default="dist", help="output directory")
    parser.add_argument("--id", required=True, help="package id, e.g. example.realism-pack")
    parser.add_argument("--name", required=True, help="package name, e.g. RealismPack")
    parser.add_argument("--repo", required=True, help="GitHub repository, owner/name")
    parser.add_argument("--author", action="append", default=[], help="author (repeatable)")
    parser.add_argument("--license", default="MIT", help="SPDX license id")
    parser.add_argument("--version", default="1.0.0", help="release version, used in file names")
    parser.add_argument("--tag-pattern", default=DEFAULT_TAG_PATTERN, help="release tag regex")
    parser.add_argument(
        "--registry",
        default="",
        help="a registry packages/ directory to check member ids against",
    )
    args = parser.parse_args(argv)

    result = build(
        Path(args.dir),
        Path(args.out),
        package_id=args.id,
        name=args.name,
        repository=args.repo,
        authors=args.author or ["Unknown"],
        license_text=args.license,
        version=args.version,
        tag_pattern=args.tag_pattern,
        packages_dir=Path(args.registry) if args.registry else None,
    )
    report(result, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
