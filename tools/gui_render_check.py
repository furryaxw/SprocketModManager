"""无头渲染管理器客户端并截图（开发用预检，不是打包 WebView 验收）。

用途：改动 `presentation/client_ui/` 后，在没有 WebView 宿主的情况下也能看到真实排版，
检查芯片、按钮与依赖摘要是否被画出来。做法是复制 `client_ui` 到临时目录、注入一个假的
`pywebview` 桥（固定 payload），再用无头 Edge 截图。

```powershell
python tools/gui_render_check.py --out artifacts/gui-installed-page.png
```

**它替代不了**打包客户端里的人工交互验收：点击穿透、滚动、真实后端的异步时序仍需肉眼确认。
需要本机安装 Microsoft Edge；没有 Edge 时脚本会直接报错退出。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EDGE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)
CLIENT_UI = Path(__file__).resolve().parent.parent / "sprocket_mod_manager" / "presentation" / "client_ui"

INSTALLED_PAYLOAD = {
    "ok": True,
    "installed": [
        {
            "id": "furryaxw.sprocket-laser-rangefinder",
            "name": "SprocketLaserRangefinder",
            "version": "0.1.3",
            "requested": True,
            "dependencies": [],
        },
        {
            "id": "fixture.corrupted-mod",
            "name": "CorruptedMod",
            "version": "2.0.0",
            "requested": True,
            "dependencies": [],
            "corrupted": True,
        },
        {
            "id": "furryaxw.sprocket-mod-api",
            "name": "SprocketModAPI",
            "version": "0.4.0",
            "requested": True,
            "dependencies": [],
            "corrupted": False,
            "suppressed": True,
            "integrity": "suppressed",
            "files": ["Mods/SprocketModAPI.dll"],
        },
    ],
    "unrecognized": [],
    "local_mods": [
        {
            "path": "Mods/SprocketLaserRangefinder.dll",
            "name": "SprocketLaserRangefinder.dll",
            "display_name": "Sprocket Laser Rangefinder",
            "version": "0.1.3",
            "authors": ["furryAxw"],
            "kind": "Mods",
            "disabled": False,
            "declared_id": "furryaxw.sprocket-laser-rangefinder",
            "registry_id": "furryaxw.sprocket-laser-rangefinder",
            "registry_match": "declared-id",
            "registry_display_name": {"en": "Sprocket Laser Rangefinder", "zh": "Sprocket 激光测距仪"},
            "required_dependencies": ["SprocketModAPI"],
            "missing_dependencies": ["SprocketDepth"],
            "incompatible_assemblies": [],
            "installed_package_id": "furryaxw.sprocket-laser-rangefinder",
            "assembly_name": "SprocketLaserRangefinder",
            "sha256": "",
            "error": "",
        },
        {
            "path": "Mods/CannonSoundPoolFix.dll.disable",
            "name": "CannonSoundPoolFix.dll.disable",
            "display_name": "Cannon Sound Pool Fix",
            "version": "1.2.0",
            "authors": ["furryAxw"],
            "kind": "Mods",
            "disabled": True,
            "declared_id": "furryaxw.cannon-sound-pool-fix",
            "registry_id": "furryaxw.cannon-sound-pool-fix",
            "registry_match": "declared-id",
            "required_dependencies": [],
            "incompatible_assemblies": [],
            "installed_package_id": "",
            "assembly_name": "CannonSoundPoolFix",
            "sha256": "",
            "error": "",
        },
        {
            "path": "UserLibs/UniverseLib.ML.IL2CPP.Interop.dll",
            "name": "UniverseLib.ML.IL2CPP.Interop.dll",
            "display_name": "UniverseLib",
            "version": "1.6.2.0",
            "authors": ["Sinai", "yukieiji"],
            "kind": "UserLibs",
            "disabled": False,
            "declared_id": "",
            "registry_id": "",
            "registry_match": "",
            "required_dependencies": [],
            "incompatible_assemblies": [],
            "installed_package_id": "",
            "assembly_name": "UniverseLib.ML.IL2CPP.Interop",
            "sha256": "",
            "error": "",
        },
        {
            "path": "Mods/CorruptedMod.dll",
            "name": "CorruptedMod.dll",
            "display_name": "Corrupted Mod",
            "version": "2.0.0",
            "authors": ["furryAxw"],
            "kind": "Mods",
            "disabled": False,
            "declared_id": "fixture.corrupted-mod",
            "registry_id": "fixture.corrupted-mod",
            "registry_match": "declared-id",
            "registry_display_name": {"en": "Corrupted Mod", "zh": "损坏的模组"},
            "required_dependencies": [],
            "incompatible_assemblies": [],
            "installed_package_id": "fixture.corrupted-mod",
            "assembly_name": "CorruptedMod",
            "sha256": "",
            "error": "",
        },
        {
            "path": "Mods/SprocketModAPI.dll",
            "name": "SprocketModAPI.dll",
            "display_name": "Sprocket Mod API",
            "version": "0.4.0",
            "authors": ["furryAxw"],
            "kind": "Mods",
            "disabled": False,
            "declared_id": "furryaxw.sprocket-mod-api",
            "registry_id": "furryaxw.sprocket-mod-api",
            "registry_match": "declared-id",
            "registry_display_name": {"en": "Sprocket Mod API", "zh": "Sprocket Mod API"},
            "required_dependencies": [],
            "incompatible_assemblies": [],
            "installed_package_id": "furryaxw.sprocket-mod-api",
            "assembly_name": "SprocketModAPI",
            "sha256": "",
            "error": "",
        },
    ],
    "local_summary": {"total": 5, "disabled": 1, "registry_matched": 4, "unmanaged": 2, "unreadable": 0,
                      "missing_dependencies": 1},
    "has_any_mods": True,
}

CATALOG_PACKAGES = [
    {
        "id": "furryaxw.sprocket-laser-rangefinder",
        "name": "SprocketLaserRangefinder",
        "display_name": {"en": "Sprocket Laser Rangefinder", "zh": "Sprocket 激光测距仪"},
        "description": {"en": "Laser rangefinder for gunners.", "zh": "炮手的激光测距仪。"},
        "authors": ["furryAxw"],
        "repository": "furryAxw/SprocketLaserRangefinder",
        "repository_url": "https://github.com/furryAxw/SprocketLaserRangefinder",
        "license": "MIT",
        "category": "utility",
        "tags": ["optics"],
        "dependencies": [],
        "recommendations": [],
        "featured": True,
        "release": {"version": "0.1.3", "published_at": "2026-09-01T00:00:00Z", "prerelease": False, "assets": []},
        "install_assets": ["SprocketLaserRangefinder.dll"],
        "installed": {"name": "SprocketLaserRangefinder", "version": "0.1.3", "requested": True,
                      "dependencies": [], "corrupted": False},
    },
    {
        "id": "furryaxw.cannon-sound-pool-fix",
        "name": "CannonSoundPoolFix",
        "display_name": {"en": "Cannon Sound Pool Fix", "zh": "炮声池修复"},
        "description": {"en": "Stops cannon audio dropouts.", "zh": "修掉炮声断续。"},
        "authors": ["furryAxw"],
        "repository": "furryAxw/CannonSoundPoolFix",
        "repository_url": "https://github.com/furryAxw/CannonSoundPoolFix",
        "license": "GPL-3.0-only",
        "category": "audio",
        "tags": ["audio"],
        "dependencies": [],
        "recommendations": [],
        "featured": False,
        "release": {"version": "1.3.0", "published_at": "2026-09-10T00:00:00Z", "prerelease": False, "assets": []},
        "install_assets": ["CannonSoundPoolFix.dll"],
        "installed": {"name": "CannonSoundPoolFix", "version": "1.2.0", "requested": True,
                      "dependencies": [], "corrupted": False},
    },
    {
        "id": "fixture.new-package",
        "name": "NewPackage",
        "display_name": {"en": "Brand New Package", "zh": "全新模组包"},
        "description": {"en": "Not installed yet.", "zh": "尚未安装。"},
        "authors": ["fixture"],
        "repository": "fixture/NewPackage",
        "repository_url": "https://github.com/fixture/NewPackage",
        "license": "MIT",
        "category": "graphics",
        "tags": [],
        "dependencies": [],
        "recommendations": [],
        "featured": False,
        "release": {"version": "2.0.0", "published_at": "2026-09-12T00:00:00Z", "prerelease": False, "assets": []},
        "install_assets": ["NewPackage.dll"],
        "installed": None,
    },
]

STUB = """// 无头截图专用的假 pywebview 桥；只提供渲染页面所需的最小响应。
const INSTALLED = %(installed)s;
const CATALOG = %(catalog)s;
const BOOTSTRAP = {
    ok: true,
    version: "0.0.0-headless-precheck",
    settings: {
        language: "en", debug: false, game_path: "C:\\\\Sprocket", index_url: "",
        index_placeholder: "https://example.invalid/index.json",
        proxy_enabled: false, proxy_url: "", proxy_placeholder: "http://127.0.0.1:7890",
        github_proxy_enabled: false, github_proxy_url: "", github_proxy_placeholder: "http://127.0.0.1:7890",
        text_scale: 1.0,
    },
    links: {melonloader: "https://example.invalid", repository: "https://example.invalid", registry: "https://example.invalid"},
};
const METHODS = {
    bootstrap: async () => BOOTSTRAP,
    get_installed: async () => INSTALLED,
    verify_installed: async () => ({ok: true, checked: INSTALLED.installed.length, corrupted: ["Mods/CorruptedMod.dll"],
        missing: [], installed: INSTALLED.installed}),
    load_catalog: async () => ({ok: true, packages: CATALOG, installed: INSTALLED.installed,
        unrecognized: INSTALLED.unrecognized, local_mods: INSTALLED.local_mods,
        local_summary: INSTALLED.local_summary, has_any_mods: true, source: "headless pre-check"}),
    get_queue: async () => ({ok: true, entries: [], close_pending: false}),
    get_melonloader_status: async () => ({ok: true, installed: true, version: "0.7.3", latest: "0.7.3", page_url: ""}),
    get_settings: async () => ({ok: true, settings: BOOTSTRAP.settings}),
    get_manager_update: async () => ({ok: true, newer: false, page_url: ""}),
    login_status: async () => ({ok: true, logged_in: false}),
    get_developer_servers: async () => ({ok: true, servers: []}),
    startup_trace: async () => ({ok: true}),
    client_log: async () => ({ok: true}),
    find_game_path: async () => ({ok: true, path: ""}),
};
window.pywebview = {
    api: new Proxy(METHODS, {
        get(target, name) {
            if (name in target) return target[name];
            return async () => ({ok: true});
        },
    }),
};
// installs.js 会用到这两个来自 core.js/i18n.js 的辅助函数；这里给出等价实现，
// 让预检跑的是真实渲染逻辑，而不是让页面在 ReferenceError 上停住。
window.tr = (key, values = {}) => {
    const table = {
        detectedMods: `${values.count} detected mods`, noInstalled: "No mods detected",
        missingDepsCount: `${values.count} missing deps`, corrupted: "Corrupted", reinstall: "Reinstall",
        unrecognized: "Unrecognized", requested: "User-installed", dependency: "Installed dependency",
        enableMod: "Enable", disableMod: "Disable", disabledMod: "Disabled",
        suppressCorruption: "Mute warning", unsuppressCorruption: "Unmute warning", suppressed: "Suppressed",
        requiresLabel: "Requires", missingLabel: "Missing", incompatibleLabel: "Incompatible", localOnly: "Local only",
    };
    return table[key] !== undefined ? table[key] : key;
};
window.localized = (values, fallback = "") => {
    if (!values || typeof values !== "object") return fallback;
    const entries = Object.entries(values);
    if (!entries.length) return fallback;
    // 这个 stub 在 core.js 之前执行，所以运行期才读 state.language。
    const language = ((window.state && window.state.language) || "zh").toLowerCase();
    const exact = entries.find(([key]) => key.toLowerCase() === language);
    if (exact) return exact[1];
    const related = entries.find(([key]) => key.toLowerCase().split("-", 1)[0] === language);
    if (related) return related[1];
    const english = entries.find(([key]) => key.toLowerCase().split("-", 1)[0] === "en");
    return english ? english[1] : entries[0][1];
};
window.addEventListener("pywebviewready", () => window.setTimeout(() => window.showPage("installed"), 250));
window.setTimeout(() => {
    if (window.state) {
        window.state.language = "zh";
        window.state.languageMode = "zh";
    }
    window.showPage("installed");
}, 600);
"""


def find_edge() -> Path:
    for candidate in EDGE_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit("Microsoft Edge was not found; this pre-check needs it for headless rendering.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Headless client rendering pre-check")
    parser.add_argument("--out", default="artifacts/gui-installed-page.png", help="screenshot output path")
    parser.add_argument("--page", default="installed", help="page to show before the screenshot")
    parser.add_argument("--window-size", default="1400,1000")
    args = parser.parse_args(argv)

    # Edge 按**自己的**工作目录解析 --screenshot 的相对路径，所以这里必须先转成绝对路径，
    # 否则相对 --out 会静默写不出去。
    output = Path(args.out).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    edge = find_edge()

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        client = root / "client"
        shutil.copytree(CLIENT_UI, client)
        stub = STUB % {
            "installed": json.dumps(INSTALLED_PAYLOAD, ensure_ascii=False),
            "catalog": json.dumps(CATALOG_PACKAGES, ensure_ascii=False),
        }
        stub = stub.replace('window.showPage("installed")', f'window.showPage("{args.page}")')
        (client / "stub-api.js").write_text(stub, encoding="utf-8")

        index = client / "index.html"
        html = index.read_text(encoding="utf-8")
        if "<head>" not in html:
            raise SystemExit("index.html has no <head> to inject into")
        index.write_text(html.replace("<head>", '<head>\n<script src="stub-api.js"></script>', 1), encoding="utf-8")

        if output.exists():
            output.unlink()
        completed = subprocess.run(
            [
                str(edge),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                f"--user-data-dir={root / 'edge-profile'}",
                f"--window-size={args.window_size}",
                "--virtual-time-budget=8000",
                f"--screenshot={output}",
                index.as_uri(),
            ],
            capture_output=True,
            text=True,
            # Edge 在中文 Windows 上会输出非 UTF-8 字节；用替换式解码，避免读线程直接崩掉
            # 并把 stdout/stderr 变成 None。
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        if not output.is_file():
            print((completed.stdout or "")[-2000:], file=sys.stderr)
            print((completed.stderr or "")[-2000:], file=sys.stderr)
            raise SystemExit("headless Edge did not produce a screenshot")

    print(f"wrote {output} ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
