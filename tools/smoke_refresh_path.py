"""只读探针：GUI 真正走的刷新路径（ClientApi.get_installed / load_catalog）在真实 DLL 上的耗时与重复解析。

- 只把真实安装的 DLL 复制到临时目录；**不读不写真实游戏目录**。
- 用子进程跑"冷/热"两次，模拟"关掉管理器再打开"。
- 同时统计 `read_dll_metadata` 调用次数：一次刷新里每个 DLL 只该解析一次（重复扫描会翻倍）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

REPO = Path(r"G:\Sprocket\sprocket-mod-system")
PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
REAL_GAME = Path(r"G:\Sprocket")


def child(game: Path, app_dir: Path) -> int:
    sys.path.insert(0, str(REPO))
    from sprocket_mod_manager.application import local_mods
    from sprocket_mod_manager.application.service import ModManagerService
    from sprocket_mod_manager.domain.registry import Registry
    from sprocket_mod_manager.infrastructure.config import ConfigStore
    from sprocket_mod_manager.infrastructure import dll_metadata
    from sprocket_mod_manager.presentation.web_gui import ClientApi

    ConfigStore(app_dir).save({"language": "zh", "game_path": str(game), "index_url": ""})
    service = ModManagerService(app_dir)
    service.registry = Registry([])
    api = ClientApi("probe", app_dir=app_dir, service_factory=lambda _app_dir: service)
    assert api is not None

    # 两个计数各有用途：cache 请求数 = "扫了几遍"，raw 解析数 = "真的解析了几个 DLL"。
    # 只数 raw 会被内存缓存掩盖（重复扫描第二次全部命中缓存，数字不变）。
    requests: list[str] = []
    parses: list[str] = []
    real_cached = local_mods.read_cached_metadata
    real_read = dll_metadata.read_dll_metadata

    def counting_cached(path):
        requests.append(str(path))
        return real_cached(path)

    def counting_read(path):
        parses.append(str(path))
        return real_read(path)

    timings: dict[str, float] = {}
    counts: dict[str, int] = {}
    parse_counts: dict[str, int] = {}
    try:
        with patch.object(local_mods, "read_cached_metadata", counting_cached), \
                patch.object(dll_metadata, "read_dll_metadata", counting_read):
            for name, call in (
                ("get_installed_cold", api.get_installed),
                ("get_installed_warm", api.get_installed),
                ("get_local_mods", api.get_local_mods),
            ):
                requests.clear()
                parses.clear()
                started = time.perf_counter()
                result = call()
                timings[name] = round((time.perf_counter() - started) * 1000, 1)
                counts[name] = len(requests)
                parse_counts[name] = len(parses)
                if name == "get_installed_cold":
                    payload = result
    finally:
        api.install_queue.close()

    print(json.dumps({
        "timings_ms": timings,
        "metadata_requests": counts,
        "dll_parses": parse_counts,
        "local_mods": len(payload.get("local_mods", [])),
        "installed": len(payload.get("installed", [])),
        "unrecognized": len(payload.get("unrecognized", [])),
        "summary": payload.get("summary") or payload.get("local_summary"),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        return child(Path(sys.argv[2]), Path(sys.argv[3]))

    with tempfile.TemporaryDirectory(prefix="sprocket-refresh-probe-") as directory:
        root = Path(directory)
        game = root / "game"
        app_dir = root / "app"
        game.mkdir()
        app_dir.mkdir()
        (game / "Sprocket.exe").write_bytes(b"stub")
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

        print(f"copied {copied} real DLLs into a temp game dir (the real install is untouched)\n")
        for label in ("cold run (fresh app dir)", "warm run (same app dir)"):
            completed = subprocess.run(
                [str(PYTHON), str(Path(__file__).resolve()), "--child", str(game), str(app_dir)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO), timeout=300,
            )
            print(f"== {label} ==")
            print((completed.stdout or "").strip() or (completed.stderr or "")[-1200:])
            print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
