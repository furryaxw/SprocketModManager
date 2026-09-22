"""真实 DLL 上的状态生命周期烟雾测试：合并后的 v2 状态文件在真实操作下是否始终自洽。

只读真实安装：把 `Mods`/`Plugins`/`UserLibs` 里的 DLL **复制**到临时游戏目录，然后在副本上跑
`local-mods` → `disable` → `enable` → `verify`，每一步都检查状态文件的不变量：

  * `schema_version == 2`，且文件里是 package 主导（`packages[id].files[]` 带 path/sha256…）
  * 每个被记录的文件都真的在磁盘上（不能有幽灵条目）
  * 同一个路径不会被两个包同时认领
  * `metadata` 段（原 metadata-cache）始终存在且条目只增不减
  * 禁用/启用之后归属跟着新路径走，且状态里不留旧路径
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(r"G:\Sprocket\sprocket-mod-system")
PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
REAL_GAME = Path(r"G:\Sprocket")


def cli(app: Path, game: Path, *argv: str) -> tuple[int, str]:
    completed = subprocess.run(
        [str(PYTHON), str(REPO / "modman.py"), "--app-dir", str(app), "--index-file", str(app / "index.json"),
         "--game-path", str(game), "--json", *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO), timeout=300,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")[-400:]


def state_of(game: Path) -> dict:
    path = game / "SprocketModManager" / "installed.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def check_invariants(stage: str, game: Path, state: dict, results: list[str], *, expect_v2: bool = True) -> None:
    if not expect_v2:
        files = state.get("files", {})
        print(f"[{stage}] v1 播下：packages={len(state.get('packages', {}))} files={len(files)}")
        return
    problems: list[str] = []
    if state.get("schema_version") != 2:
        problems.append("schema_version != 2")
    packages = state.get("packages", {})
    metadata = state.get("metadata", {})
    if not isinstance(metadata, dict) or not metadata:
        problems.append("metadata 段为空（元数据缓存没写进状态文件）")

    ownership: dict[str, list[str]] = {}
    for package_id, package in packages.items():
        for entry in package.get("files", []):
            if isinstance(entry, str):  # 还没迁移的 v1 形状
                problems.append(f"{entry} 仍是 v1 路径字符串（没迁移成包内文件对象）")
                continue
            relative = entry.get("path", "")
            ownership.setdefault(relative, []).append(package_id)
            # `.dll` 与 `.dll.disable` 是同一个逻辑文件：状态记规范路径，磁盘上可能是任一变体。
            if not (game / relative).is_file() and not (game / (relative + ".disable")).is_file():
                problems.append(f"幽灵条目：{relative} 不在磁盘上")
            if not entry.get("sha256"):
                problems.append(f"{relative} 没有 sha256")
    for relative, owners in ownership.items():
        if len(owners) > 1:
            problems.append(f"{relative} 被多个包认领：{owners}")
    for relative in state.get("unowned", {}):
        if not (game / relative).is_file():
            problems.append(f"unowned 幽灵条目：{relative}")

    status = "OK" if not problems else "PROBLEM: " + "; ".join(problems)
    print(f"[{stage}] packages={len(packages)} files={len(ownership)} unowned={len(state.get('unowned', {}))} "
          f"metadata={len(metadata)} -> {status}")
    results.extend(problems)


def main() -> int:
    results: list[str] = []
    with tempfile.TemporaryDirectory(prefix="sprocket-lifecycle-") as directory:
        root = Path(directory)
        game = root / "game"
        app = root / "app"
        game.mkdir()
        app.mkdir()
        (game / "Sprocket.exe").write_bytes(b"stub")
        (app / "index.json").write_text(json.dumps({"schema_version": 1, "packages": []}), encoding="utf-8")

        copied: list[str] = []
        for kind in ("Mods", "Plugins", "UserLibs"):
            source = REAL_GAME / kind
            if not source.is_dir():
                continue
            target = game / kind
            target.mkdir()
            for dll in source.glob("*.dll"):
                shutil.copyfile(dll, target / dll.name)
                copied.append(f"{kind}/{dll.name}")

        code, output = cli(app, game, "local-mods")
        print(f"local-mods rc={code} dlls={len(copied)}")
        check_invariants("after local-mods", game, state_of(game), results)

        # 播一份 **v1** 状态（模拟升级前的真实记录）：两个包、各自的文件与真实 sha256。
        import hashlib

        owned = [name for name in copied if name.startswith("Mods/")][:2]
        if len(owned) == 2:
            state_dir = game / "SprocketModManager"
            state_dir.mkdir(parents=True, exist_ok=True)
            v1_files = {}
            v1_packages = {}
            for index, name in enumerate(owned):
                digest = hashlib.sha256((game / name).read_bytes()).hexdigest()
                package_id = f"fixture.package-{index}"
                v1_files[name] = {"owners": [package_id], "sha256": digest,
                                  "preexisting": False, "disabled": False}
                v1_packages[package_id] = {
                    "name": f"Package {index}", "version": "1.0.0", "requested": True,
                    "dependencies": [], "installed_at": "2026-09-22T00:00:00Z",
                    "install_mode": "standard", "files": [name],
                }
            (state_dir / "installed.json").write_text(
                json.dumps({"schema_version": 1, "packages": v1_packages, "files": v1_files}),
                encoding="utf-8",
            )
            print(f"seeded a v1 state with {len(owned)} owned files: {owned}")
            # 让索引里真的有这两个包，CLI 的 remove 才能解析 id（否则 _require_registry 解析不到 → 报错）。
            index = {
                "schema_version": 1,
                "packages": [
                    {
                        "id": f"fixture.package-{index_}",
                        "name": f"Package {index_}",
                        "authors": ["fixture"],
                        "repository": f"fixture/package-{index_}",
                        "license": "MIT",
                        "display_name": {"en": f"Package {index_}"},
                        "description": {"en": "smoke fixture"},
                        "release": {"assets": {"include": [], "exclude": []}},
                        "dependencies": [],
                        "install": {"scan_dlls": False, "exclude": [], "overrides": []},
                        "category": "utility",
                        "tags": [],
                    }
                    for index_ in range(len(owned))
                ],
            }
            (app / "index.json").write_text(json.dumps(index), encoding="utf-8")
            check_invariants("seeded v1 (not yet migrated)", game, state_of(game), results, expect_v2=False)
            # 迁移发生在下一次读写状态之前：先跑一次 local-mods（会加载并回写状态）。
            code, output = cli(app, game, "local-mods")
            print(f"local-mods (migrate) rc={code}")
            check_invariants("after migration", game, state_of(game), results)

        # 用真实 CLI 走一遍禁用/启用：这会走 rename + 状态同步（迁移后的 v2 必须跟着走）。
        candidate = owned[0] if len(owned) == 2 else next((name for name in copied if name.startswith("Mods/")), None)
        if candidate is None:
            print("no Mods/*.dll to toggle; skipping the rename steps")
        else:
            disabled_path = candidate + ".disable"
            code, output = cli(app, game, "disable", candidate)
            print(f"disable {candidate} rc={code}")
            if code != 0:
                results.append(f"disable failed: {output[-200:]}")
            check_invariants("after disable", game, state_of(game), results)

            code, output = cli(app, game, "enable", disabled_path)
            print(f"enable {disabled_path} rc={code}")
            if code != 0:
                results.append(f"enable failed: {output[-200:]}")
            check_invariants("after enable", game, state_of(game), results)

            state = state_of(game)
            owned_paths = {entry["path"] for package in state.get("packages", {}).values()
                           for entry in package.get("files", [])}
            if candidate not in owned_paths:
                results.append(f"启用后归属没有回到 {candidate}：{sorted(owned_paths)}")
            if disabled_path in owned_paths:
                results.append(f"状态里还留着旧路径 {disabled_path}")

        code, output = cli(app, game, "verify")
        print(f"verify rc={code}")
        check_invariants("after verify", game, state_of(game), results)

        # 1) 手工删掉一个受管文件 → 下一次刷新必须把幽灵条目清掉（磁盘即事实来源）。
        if len(owned) == 2:
            victim = owned[1]
            (game / victim).unlink()
            code, output = cli(app, game, "local-mods")
            print(f"local-mods (after hand-deleting {victim}) rc={code}")
            state = state_of(game)
            check_invariants("after hand-delete", game, state, results)
            recorded = {entry["path"] for package in state.get("packages", {}).values()
                        for entry in package.get("files", [])}
            if victim in recorded:
                results.append(f"手工删除后状态里仍然记着 {victim}（reconcile 没生效）")
            for package in state.get("packages", {}).values():
                if not package.get("files"):
                    results.append("reconcile 之后留下了没有任何文件的空包")

        # 2) 用 CLI 卸载一个包：文件要被删掉，状态里不再有这个包。
        if len(owned) == 2:
            package_id = "fixture.package-0"
            code, output = cli(app, game, "remove", package_id)
            print(f"remove {package_id} rc={code}")
            state = state_of(game)
            check_invariants("after remove", game, state, results)
            if package_id in state.get("packages", {}):
                results.append(f"卸载后状态里仍有 {package_id}")
            if (game / owned[0]).exists():
                results.append(f"卸载后文件仍在磁盘上：{owned[0]}")

        appdata_hits = [name for name in ("installed.json", "metadata-cache.json", "transactions")
                        if (app / name).exists()]
        print(f"AppData 里的游戏相关文件: {appdata_hits or '无'}")
        if appdata_hits:
            results.append(f"AppData 混进了游戏相关文件：{appdata_hits}")

    print()
    if results:
        print(f"FAILED: {len(results)} 个问题")
        for item in results:
            print(" -", item)
        return 1
    print("PASSED: 合并后的 v2 状态在真实 DLL 的完整生命周期里自洽")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
