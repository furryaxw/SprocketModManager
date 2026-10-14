"""整合包 fixture：造一套自足的测试整合包（zip + 条目 + 本地索引），并能离线跑完整流程。

产物（默认 `artifacts/pack-fixture/`，已被 gitignore）：

    pack-1.0.0.zip / pack-2.0.0.zip   整合包两个版本的载荷：根部 pack.json（成员清单）+ override 文件
    standard-pack.zip       标准模式那条包的载荷（用来演示"标准模式不能覆盖别人的文件"）
    loader.zip              基础运行时载荷（`install.payload` 那条线）
    member-a.zip / member-b.zip   成员模组的载荷
    entries/*.json          可直接抄进注册表的条目（pack 那条是 `kind: "pack"`）
    index/                  本地索引目录：管理器「设置 → 索引目录」指到这里就能看到这个包
    README.md               每个文件是什么、怎么用、要能在管理器里真装上还缺什么

`--run` 用**真实的服务与安装器**（只把下载换成从上面那几个 zip 读）在临时游戏目录里跑一遍，
逐项打印结果：计划 → 装包 → 覆盖模组资源 → 卸包/更新后的还原 → 自己装的成员留下 → 标准模式被拒。

要能在管理器里点「安装」，包的 zip 必须放在 github.com 的 release 上（下载只认 `github.com/<repo>/
releases/download/...`）：把三个 zip 传到一个仓库的 release，再把 `entries/*.json` 里的 `repository`
与 `release.assets.include` 改成那个仓库即可。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from dataclasses import replace  # noqa: F401  (kept out of the way: fixture entries are plain dicts)
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sprocket_mod_manager.application.planning import ChangePlanner, ChangeRequest  # noqa: E402
from sprocket_mod_manager.application.service import ModManagerService  # noqa: E402
from sprocket_mod_manager.domain.compatibility import CapabilityEnvironment  # noqa: E402
from sprocket_mod_manager.domain.errors import ModManagerError  # noqa: E402
from sprocket_mod_manager.domain.models import MODLOADER_KIND, PACK_KIND  # noqa: E402
from sprocket_mod_manager.domain.registry import Registry  # noqa: E402
from sprocket_mod_manager.infrastructure.manager_paths import state_file_path  # noqa: E402
from sprocket_mod_manager.infrastructure.state import StateStore  # noqa: E402

REPOSITORY = "example/sprocket-pack-fixture"
GAME_SUPPLY = {
    "sprocket:root": "{Sprocket}",
    "sprocket:userdata": "{Documents}/My Games/Sprocket",
    "sprocket:blueprints": "{Documents}/My Games/Sprocket/Factions/Default/Blueprints",
    "sprocket:streamingassets": "{Sprocket}/Sprocket_Data/StreamingAssets",
}
LOADER = "example.loader"
MEMBER_A = "example.member-a"
MEMBER_B = "example.member-b"
PACK = "example.realism-pack"
STANDARD_PACK = "example.standard-pack"
MEMBERS_ONLY = "example.members-only-pack"

RESOURCE = "Sprocket_Data/StreamingAssets/Parts/foo.json"
EXTRA = "Sprocket_Data/StreamingAssets/Parts/pack-extra.json"
MEMBER_A_RESOURCE = "Sprocket_Data/StreamingAssets/Parts/member-a.json"
MOD_BYTES = b'{"part": "the mod\'s own resource"}'
MEMBER_A_RESOURCE_BYTES = b'{"part": "member A resource"}'
PACK_V1 = b'{"part": "pack override v1"}'
PACK_V2 = b'{"part": "pack override v2"}'

# 固定时间戳：同一份输入产出逐字节相同的 zip，fixture 可复现。
ZIP_DATE = (2026, 1, 1, 0, 0, 0)


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()


def manifest_document(*members: tuple[str, str], install: dict | None = None) -> bytes:
    """整合包的清单：成员与落点规则都在这里，作为 release 里的独立 JSON 资产发布。"""
    document: dict = {
        "schema_version": 1,
        "members": [{"id": item, "version": version, "when": "*"} for item, version in members],
    }
    if install is not None:
        document["install"] = install
    return json.dumps(document, indent=2).encode("utf-8")


def pack_block(asset_name: str, content: bytes, manifest: dict) -> dict:
    """索引构建写进 release 记录的那份清单（客户端与站点读的是它）。"""
    return {
        "asset_id": int(hashlib.sha256(asset_name.encode("utf-8")).hexdigest()[:8], 16),
        "asset_name": asset_name,
        "members": list(manifest.get("members") or []),
        "install": dict(manifest.get("install") or {}),
    }


def asset_record(name: str, content: bytes) -> dict:
    return {
        "id": int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16),
        "name": name,
        "size": len(content),
        "download_url": f"https://github.com/{REPOSITORY}/releases/download/v1.0.0/{name}",
        "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
    }


def release_record(
        assets: dict[str, bytes],
        version: str = "1.0.0",
        *,
        pack: dict | None = None,
) -> dict:
    records = []
    for name, content in assets.items():
        asset = asset_record(name, content)
        asset["download_url"] = (
            f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}"
        )
        records.append(asset)
    record = {
        "id": 1,
        "tag": f"v{version}",
        "version": version,
        "prerelease": False,
        "published_at": "2026-01-01T00:00:00Z",
        "page_url": f"https://github.com/{REPOSITORY}/releases/tag/v{version}",
        "assets": records,
    }
    if pack is not None:
        record["pack"] = pack
    return record


def merged_pack(entry_document: dict, releases: list[dict]) -> dict:
    """索引构建干的那件事：把每一版清单并进条目，成员与规则都带 `when: "=该版本"`。

    条目自己写的 `members`/`install` 是基线（清单取不到时还在）；`mode`/`exclude`/`replace` 不是
    按版本生效的字段，取最新一版清单的值。
    """
    document = dict(entry_document)
    members: list[dict] = list(document.get("members") or [])
    install = dict(document.get("install") or {})
    files: list[dict] = list(install.get("files") or [])
    for record in releases:
        manifest = record.get("pack")
        if not isinstance(manifest, dict):
            continue
        when = f"={record['version']}"
        members.extend(
            {"id": member["id"], "version": member["version"], "when": when}
            for member in manifest.get("members") or []
        )
        for rule in (manifest.get("install") or {}).get("files") or []:
            files.append({**{key: value for key, value in rule.items() if key != "when"}, "when": when})
        for field in ("mode", "exclude", "replace"):
            if field in (manifest.get("install") or {}) and field not in install:
                install[field] = manifest["install"][field]
    if members:
        document["members"] = members
    install["files"] = files
    document["install"] = install
    return document


def entry(
        package_id: str,
        name: str,
        *,
        kind: str = "modfile",
        category: str = "utility",
        install: dict | None = None,
        members: list[dict] | None = None,
        dependencies: list[dict] | None = None,
        supply: dict[str, str] | None = None,
        asset_name: str,
        asset_names: list[str] | None = None,
        version: str = "1.0.0",
) -> dict:
    document: dict = {
        "schema_version": 3,
        "id": package_id,
        "name": name,
        "kind": kind,
        "category": category,
        "authors": ["fixture"],
        "repository": REPOSITORY,
        "license": "MIT",
        "display_name": {"en": name, "zh": f"{name}（fixture）"},
        "description": {"en": "Pack fixture entry.", "zh": "整合包 fixture 条目。"},
        "release": {
            "version_pattern": "^v?([0-9]+\\.[0-9]+\\.[0-9]+)$",
            "assets": {"include": asset_names or [asset_name]},
        },
        "dependencies": dependencies or [],
    }
    if install is not None:
        document["install"] = install
    if members is not None:
        document["members"] = members
    if supply is not None:
        document["supply"] = supply
    return document


def build_fixture(out: Path) -> dict:
    """写出全部 fixture 文件，返回后面跑流程要用的原始数据。"""
    out.mkdir(parents=True, exist_ok=True)
    (out / "entries").mkdir(exist_ok=True)
    (out / "index").mkdir(exist_ok=True)

    loader_zip = zip_bytes({"version.dll": b"pretend loader proxy", "Mods/.keep": b""})
    member_a_zip = zip_bytes({
        "MemberA.dll": b"pretend member A",
        "Parts/foo.json": MOD_BYTES,
        "Parts/member-a.json": MEMBER_A_RESOURCE_BYTES,
    })
    member_b_zip = zip_bytes({"MemberB.dll": b"pretend member B"})
    pack_v1_zip = zip_bytes({
        "Parts/foo.json": PACK_V1,
        "Parts/pack-extra.json": b'{"part": "pack only"}',
    })
    pack_v2_zip = zip_bytes({
        "Parts/foo.json": PACK_V2,
        "Parts/pack-extra.json": b'{"part": "pack only v2"}',
    })
    standard_zip = zip_bytes({"Parts/foo.json": PACK_V1})

    pack_rules = {
        "mode": "patch",
        "files": [{
            "match": "Parts/**",
            "type": "sprocket:streamingassets",
            "layout": "tree",
            "subpath": "Parts",
        }],
        "exclude": [],
    }
    standard_rules = {
        "files": [{
            "match": "Parts/**",
            "type": "sprocket:streamingassets",
            "layout": "tree",
            "subpath": "Parts",
        }],
        "exclude": [],
    }
    manifests = {
        "pack-1.0.0.json": manifest_document(
            (MEMBER_A, "=1.0.0"), (MEMBER_B, ">=1.0.0"), install=pack_rules
        ),
        "pack-2.0.0.json": manifest_document(
            (MEMBER_A, "=1.0.0"), (MEMBER_B, ">=1.0.0"), install=pack_rules
        ),
        "standard-pack.json": manifest_document((MEMBER_A, "=1.0.0"), install=standard_rules),
        "members-only.json": manifest_document((MEMBER_B, ">=1.0.0")),
    }

    zips = {
        "loader.zip": loader_zip,
        "member-a.zip": member_a_zip,
        "member-b.zip": member_b_zip,
        "pack-1.0.0.zip": pack_v1_zip,
        "pack-2.0.0.zip": pack_v2_zip,
        "standard-pack.zip": standard_zip,
    }
    for name, content in {**zips, **manifests}.items():
        (out / name).write_bytes(content)

    entries = {
        f"{LOADER}.json": entry(
            LOADER, "FixtureLoader",
            kind=MODLOADER_KIND,
            install={"payload": [{"match": "**", "target": "{Sprocket}", "layout": "tree"}]},
            supply={"melonloader:mod": "{Sprocket}/Mods"},
            asset_name="loader.zip",
        ),
        f"{MEMBER_A}.json": entry(
            MEMBER_A, "MemberA",
            install={
                "files": [
                    {"match": "**/*.dll", "type": "melonloader:mod"},
                    {"match": "Parts/**", "type": "sprocket:streamingassets", "layout": "tree", "subpath": "Parts"},
                ],
                "scan_dlls": False,
                "exclude": [],
            },
            dependencies=[{"id": LOADER, "version": "*", "when": "*"}],
            asset_name="member-a.zip",
        ),
        f"{MEMBER_B}.json": entry(
            MEMBER_B, "MemberB",
            install={"files": [{"match": "**/*.dll", "type": "melonloader:mod"}], "scan_dlls": False, "exclude": []},
            dependencies=[{"id": LOADER, "version": "*", "when": "*"}],
            asset_name="member-b.zip",
        ),
        # 整合包条目只有身份与抓取规则：成员与落点规则在 release 的清单资产里。
        f"{PACK}.json": entry(
            PACK, "RealismPack",
            kind=PACK_KIND,
            category="pack",
            asset_name="pack-*.zip",
            asset_names=["pack-*.zip", "pack-*.json"],
        ),
        f"{STANDARD_PACK}.json": entry(
            STANDARD_PACK, "StandardPack",
            kind=PACK_KIND,
            category="pack",
            asset_name="standard-pack.zip",
            asset_names=["standard-pack.zip", "standard-pack.json"],
        ),
        f"{MEMBERS_ONLY}.json": entry(
            MEMBERS_ONLY, "MembersOnlyPack",
            kind=PACK_KIND,
            category="pack",
            asset_name="members-only.json",
        ),
    }
    for name, document in entries.items():
        (out / "entries" / name).write_text(
            json.dumps(document, ensure_ascii=False, indent=4) + "\n", encoding="utf-8"
        )

    def pack_release(version: str, payload: dict[str, bytes], manifest_name: str) -> dict:
        manifest = json.loads(manifests[manifest_name].decode("utf-8"))
        return release_record(
            {**payload, manifest_name: manifests[manifest_name]},
            version,
            pack=pack_block(manifest_name, manifests[manifest_name], manifest),
        )

    pack_releases = [
        pack_release("2.0.0", {"pack-2.0.0.zip": pack_v2_zip}, "pack-2.0.0.json"),
        pack_release("1.0.0", {"pack-1.0.0.zip": pack_v1_zip}, "pack-1.0.0.json"),
    ]
    standard_releases = [pack_release("1.0.0", {"standard-pack.zip": standard_zip}, "standard-pack.json")]
    # 纯成员包：release 里只有清单，一个载荷都没有。
    members_only_releases = [pack_release("1.0.0", {}, "members-only.json")]

    packages = [
        {
            **entries[f"{LOADER}.json"],
            "meta_url": f"packages/fixture/{LOADER}.json",
            "releases": [release_record({"loader.zip": loader_zip})],
        },
        {
            **entries[f"{MEMBER_A}.json"],
            "meta_url": f"packages/fixture/{MEMBER_A}.json",
            "releases": [release_record({"member-a.zip": member_a_zip})],
        },
        {
            **entries[f"{MEMBER_B}.json"],
            "meta_url": f"packages/fixture/{MEMBER_B}.json",
            "releases": [release_record({"member-b.zip": member_b_zip})],
        },
        {
            **merged_pack(entries[f"{PACK}.json"], pack_releases),
            "meta_url": f"packages/fixture/{PACK}.json",
            "releases": pack_releases,
        },
        {
            **merged_pack(entries[f"{STANDARD_PACK}.json"], standard_releases),
            "meta_url": f"packages/fixture/{STANDARD_PACK}.json",
            "releases": standard_releases,
        },
        {
            **merged_pack(entries[f"{MEMBERS_ONLY}.json"], members_only_releases),
            "meta_url": f"packages/fixture/{MEMBERS_ONLY}.json",
            "releases": members_only_releases,
        },
    ]
    for package in packages:
        package.pop("$schema", None)
    index = {
        "packages.json": {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00Z",
            "packages": packages,
        },
        "environment.json": {
            "schema_version": 1,
            "game": {"id": "hamish.sprocket", "name": "Sprocket", "supply": GAME_SUPPLY},
            "providers": {"schema_version": 2, "entries": []},
        },
        "diagnosis.json": {},
    }
    for name, document in index.items():
        (out / "index" / name).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    (out / "README.md").write_text(README_TEXT, encoding="utf-8")
    return {"zips": zips, "manifests": manifests, "index": index, "entries": entries}


README_TEXT = """# 整合包 fixture

由 `tools/pack_fixture.py` 生成（`artifacts/` 被 gitignore，这里的东西不入库）。

| 文件 | 是什么 |
| --- | --- |
| `pack-1.0.0.json` / `pack-2.0.0.json` | 整合包的清单：成员 + 落点规则（release 里的独立 JSON 资产） |
| `pack-1.0.0.zip` / `pack-2.0.0.zip` | 两个版本的载荷：只有 `Parts/` 下的 override 文件，清单不在 zip 里 |
| `standard-pack.json` / `standard-pack.zip` | 标准模式那条包（演示"标准模式不能覆盖别人的文件"） |
| `members-only.json` | 纯成员包：release 里只有清单，一个载荷都没有 |
| `loader.zip` | 基础运行时载荷（`install.payload` 那条线，落地 `version.dll` 与 `Mods/`） |
| `member-a.zip` / `member-b.zip` | 成员模组载荷（`MemberX.dll` → `Mods/`，A 还带一个 StreamingAssets 资源） |
| `entries/*.json` | 可直接抄进注册表的条目；三条整合包只有身份与抓取规则（成员与规则在清单里） |
| `index/` | 本地索引：清单已按版本并进条目（成员与规则带 `when: "=版本"`），「设置 → 索引目录」指到这里就能在「整合包」页看到它 |

覆盖到的功能：`kind: pack` 的独立页面与计划、成员（`=1.0.0` 精确 + `>=1.0.0` 区间）、清单资产带来的
落点规则、override 文件（游戏供给的 `sprocket:streamingassets`）、成员隐含的加载器依赖、patch 模式的
覆盖与还原、成员随包装卸、标准模式包被拒、纯成员包（release 里没有载荷）。

**要能在管理器里真装**：下载只认 `github.com/<repo>/releases/download/...`，所以把 zip 与 json 传到一个
仓库的同一个 release（tag 与 `version_pattern` 对得上），然后把 `entries/*.json` 里的 `repository` 与
`release.assets.include` 改成那个仓库。本地索引只能看到页面与计划，装到下载那一步会因为地址不可达而失败。

离线跑一遍完整流程（真实服务与安装器，只把下载换成读这些 zip）：

    .\\.venv\\Scripts\\python.exe tools/pack_fixture.py --run
"""


class FakeHttp:
    """只实现安装链用到的下载：按资产名写出 fixture 里的字节。"""

    def __init__(self, zips: dict[str, bytes]):
        self.zips = zips
        self.downloaded: list[str] = []

    def download(self, asset, destination: Path, *, progress=None, hosts=None):
        self.downloaded.append(asset.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.zips[asset.name])
        return destination


def report(results: list[tuple[str, bool, str]]) -> int:
    width = max(len(name) for name, _ok, _note in results)
    failed = 0
    for name, ok, note in results:
        failed += 0 if ok else 1
        print(f"[{'OK' if ok else 'FAIL'}] {name.ljust(width)}  {note}")
    print(f"\n{len(results) - failed}/{len(results)} 项通过")
    return 1 if failed else 0


def run(zips: dict[str, bytes], index: dict) -> int:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    game = root / "game"
    (game / "Sprocket_Data" / "StreamingAssets").mkdir(parents=True)
    (game / "Sprocket.exe").write_bytes(b"")

    service = ModManagerService(root / "app")
    service.registry = Registry.from_dict({
        "schema_version": index["packages.json"]["schema_version"],
        "packages": index["packages.json"]["packages"],
        "game": index["environment.json"]["game"],
        "providers": index["environment.json"]["providers"],
        "diagnosis": {},
    })
    http = FakeHttp(zips)
    service.http = http
    results: list[tuple[str, bool, str]] = []

    def read(relative: str) -> bytes | None:
        path = game / relative
        return path.read_bytes() if path.is_file() else None

    def state() -> dict:
        return StateStore(state_file_path(game)).load()

    def planner() -> ChangePlanner:
        return ChangePlanner(
            registry=service.registry,
            github=service.github,
            environment=CapabilityEnvironment(),
            state=service.installed(game),
            modloaders=service.modloader_ids(),
        )

    with patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False):
        # 1 计划：包一行 + 成员各自一行，成员归到包名下
        payload = planner().plan(ChangeRequest(package_ids=(PACK,)))
        members = payload.get("pack_members", {}).get(PACK, [])
        lines = {line["id"]: line for line in payload["lines"]}
        results.append((
            "计划页：包与成员",
            members == [MEMBER_A, MEMBER_B] and lines[MEMBER_A]["member_of"] == [PACK],
            f"pack_members={members}，成员行 member_of={lines[MEMBER_A]['member_of']}",
        ))

        # 2 装成员 A（模拟"模组先装、产生自己的资源文件"）
        service.install(MEMBER_A, game)
        results.append((
            "模组先装出资源文件",
            read(RESOURCE) == MOD_BYTES and read(MEMBER_A_RESOURCE) == MEMBER_A_RESOURCE_BYTES,
            f"{RESOURCE} 与 {MEMBER_A_RESOURCE} 都归 {MEMBER_A} 所有",
        ))

        # 3 装包（先装 1.0.0）：成员进来、override 覆盖模组的资源
        service.install(PACK, game, version_range="=1.0.0")
        installed = state()["packages"]
        results.append((
            "装包：成员 + override 落盘",
            read(RESOURCE) == PACK_V1
            and read(EXTRA) is not None
            and read("Mods/MemberA.dll") == b"pretend member A"
            and read("version.dll") == b"pretend loader proxy",
            f"override={read(RESOURCE)!r}，成员 DLL 与加载器载荷都在",
        ))
        results.append((
            "装包：包 requested、成员不算用户装的",
            installed[PACK]["requested"] and not installed[MEMBER_B]["requested"],
            f"{PACK}.requested={installed[PACK]['requested']}，"
            f"{MEMBER_B}.requested={installed[MEMBER_B]['requested']}",
        ))

        # 4 更新包（v1 → v2）后再卸载：还原点仍是模组那份
        service.install(PACK, game, version_range="=2.0.0")
        results.append((
            "更新包：内容换成 v2",
            read(RESOURCE) == PACK_V2 and read(EXTRA) == b'{"part": "pack only v2"}',
            f"override={read(RESOURCE)!r}，pack-extra={read(EXTRA)!r}",
        ))
        removed, _warnings = service.remove(PACK, game)
        results.append((
            "卸包：成员跟着走、模组资源还原",
            read(RESOURCE) == MOD_BYTES and read(EXTRA) is None and read("Mods/MemberB.dll") is None,
            f"还原={read(RESOURCE)!r}，pack-extra={read(EXTRA)!r}，"
            f"member-b.dll={read('Mods/MemberB.dll')!r}（removed={len(removed)}）",
        ))
        results.append((
            "自己装的模组留下",
            (game / "Mods" / "MemberA.dll").is_file(),
            f"{MEMBER_A} 的 DLL 还在",
        ))

        # 5 标准模式的包去覆盖模组的资源：直接拒绝
        standard = service.registry.get(STANDARD_PACK)
        try:
            service.install(STANDARD_PACK, game)
            refused, note = False, "居然装上了"
        except ModManagerError as exc:
            refused, note = "cannot be replaced" in str(exc), str(exc)
        results.append((
            "标准模式的包不能覆盖别人的文件",
            refused,
            note,
        ))

        # 6 纯成员包：release 里只有清单，一个载荷都没有，照样装得上
        service.install(MEMBERS_ONLY, game)
        installed = state()["packages"]
        results.append((
            "纯成员包：只有清单也能装",
            installed[MEMBERS_ONLY]["dependencies"] == [MEMBER_B]
            and read("Mods/MemberB.dll") == b"pretend member B",
            f"dependencies={installed[MEMBERS_ONLY]['dependencies']}，MemberB.dll 在场",
        ))
        service.remove(MEMBERS_ONLY, game)
        results.append((
            "纯成员包：卸包带走成员",
            read("Mods/MemberB.dll") is None,
            f"MemberB.dll={read('Mods/MemberB.dll')!r}",
        ))

    print(f"（下载 {len(http.downloaded)} 次，全部来自 fixture zip）")
    return report(results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the mod-pack fixture and optionally run the lifecycle")
    parser.add_argument("--out", default="artifacts/pack-fixture", help="fixture output directory")
    parser.add_argument("--run", action="store_true", help="run the lifecycle offline against the built fixture")
    args = parser.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    fixture = build_fixture(out)
    print(
        f"wrote {out}（{len(fixture['zips'])} 个 zip、{len(fixture['manifests'])} 份清单、"
        f"{len(fixture['entries'])} 个条目、本地索引 index/）"
    )
    if not args.run:
        print("加 --run 离线跑一遍完整流程（真实服务与安装器，只把下载换成读这些 zip）")
        return 0
    return run(fixture["zips"], fixture["index"])


if __name__ == "__main__":
    raise SystemExit(main())
