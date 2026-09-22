"""只读烟雾测试：把真实安装的 DLL 复制到临时目录，用真 DLL 跑一遍管理器的三条路径并计时。

- 不读也不写真实游戏目录（只从 G:\Sprocket\{Mods,Plugins,UserLibs} 复制 DLL 出来）。
- 冷启动 vs 热缓存的耗时差就是"刷新缓慢"那条反馈的直接证据。
- 输出 JSON，便于贴进报告。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(r"G:\Sprocket\sprocket-mod-system")
PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
REAL_GAME = Path(r"G:\Sprocket")


def run(arguments: list[str], app_dir: Path, game: Path) -> tuple[int, float, str]:
    command = [
        str(PYTHON),
        str(REPO / "modman.py"),
        "--app-dir", str(app_dir),
        "--index-file", str(app_dir / "index.json"),
        "--game-path", str(game),
        "--json",
        *arguments,
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO), timeout=300
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    if completed.returncode != 0:
        print(f"command failed ({completed.returncode}): {' '.join(arguments)}", file=sys.stderr)
        print(completed.stderr[-2000:], file=sys.stderr)
    return completed.returncode, elapsed_ms, completed.stdout


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="sprocket-smoke-") as directory:
        root = Path(directory)
        game = root / "game"
        app_dir = root / "app"
        game.mkdir()
        app_dir.mkdir()
        (game / "Sprocket.exe").write_bytes(b"stub")
        (app_dir / "index.json").write_text(
            json.dumps({"schema_version": 1, "packages": []}), encoding="utf-8"
        )

        copied = 0
        for kind in ("Mods", "Plugins", "UserLibs"):
            source = REAL_GAME / kind
            if not source.is_dir():
                continue
            target = game / kind
            target.mkdir()
            for dll in source.glob("*.dll"):
                shutil.copyfile(dll, target / dll.name)
                copied += 1

        results: dict[str, object] = {"dlls_copied": copied, "game_dir": "temp copy of the real install"}

        code, cold_ms, cold_out = run(["local-mods"], app_dir, game)
        results["local_mods_cold_ms"] = round(cold_ms, 1)
        code, warm_ms, warm_out = run(["local-mods"], app_dir, game)
        results["local_mods_warm_ms"] = round(warm_ms, 1)
        code, installed_ms, _ = run(["installed"], app_dir, game)
        results["installed_ms"] = round(installed_ms, 1)
        code, verify_ms, verify_out = run(["verify"], app_dir, game)
        results["verify_ms"] = round(verify_ms, 1)

        if cold_out:
            payload = json.loads(cold_out)
            results["summary"] = payload["summary"]
            results["sample_rows"] = [
                {
                    "path": row["path"],
                    "display_name": row["display_name"],
                    "version": row["version"],
                    "kind": row["kind"],
                    "disabled": row["disabled"],
                    "registry_match": row["registry_match"],
                    "missing_dependencies": row["missing_dependencies"],
                }
                for row in payload["mods"][:6]
            ]
        if verify_out:
            results["verify"] = json.loads(verify_out)

        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
