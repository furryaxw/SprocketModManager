"""在 Node 里真实执行加载器页的渲染逻辑。

覆盖每张卡的构成：名字、已装/最新版本、状态芯片、动作按钮、页面链接，以及依赖 / 推荐 /
「提供」三块（可安装类型 → 安装目录），还有两个写操作的接口接线。没有 node 时自动跳过。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "fixtures" / "client_ui" / "render_modloaders_harness.js"
CLIENT_UI = Path(__file__).resolve().parent.parent / "sprocket_mod_manager" / "presentation" / "client_ui"
NODE = shutil.which("node")


def modloader(
        loader_id: str = "lavagang.melonloader",
        *,
        installed: bool = False,
        installed_version: str = "",
        latest_version: str = "0.7.3",
        update_available: bool = False,
        compatible: str = "compatible",
        files: int = 0,
) -> dict:
    return {
        "id": loader_id,
        "name": loader_id.rsplit(".", 1)[-1],
        "display_name": {"en": loader_id, "zh": loader_id},
        "description": {"en": "A mod loader.", "zh": "一个模组加载器。"},
        "repository": "LavaGang/MelonLoader",
        "page_url": "https://github.com/LavaGang/MelonLoader",
        "category": "utility",
        "tags": ["modloader"],
        "installed": installed,
        "installed_version": installed_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "compatible": compatible,
        "supply": [
            {"type": "melonloader:core", "directory": "{Sprocket}"},
            {"type": "melonloader:mod", "directory": "{Sprocket}/Mods"},
        ],
        "dependencies": [{"id": "furryaxw.sprocket-mod-api", "version": ">=0.4.0", "when": "*"}],
        "recommendations": ["furryaxw.cannon-sound-pool-fix"],
        "files": files,
    }


def environment(*, installed: bool = True) -> dict:
    return {
        "sprocket": {"state": "ok", "version": "0.2.53.2"},
        "loaders": {
            "lavagang.melonloader": {
                "installed": installed,
                "version": "0.7.3" if installed else None,
                "latest_version": "0.7.3",
                "used_version": "0.7.3",
            },
        },
        "environment": {"state": "ok", "entry": None, "loader": "", "table_source": "registry"},
        "revision": 1,
    }


def texts(node) -> list[str]:
    found = [node["text"]] if node.get("text") else []
    for child in node.get("children") or []:
        found.extend(texts(child))
    return found


def flatten(node) -> list[dict]:
    found = [node]
    for child in node.get("children") or []:
        found.extend(flatten(child))
    return found


def find(node, predicate):
    for candidate in flatten(node):
        if predicate(candidate):
            return candidate
    return None


@unittest.skipIf(NODE is None, "node is not available")
class ModloadersRenderHarnessTests(unittest.TestCase):
    def _render(self, **payload) -> dict:
        payload.setdefault("packages", [])
        payload.setdefault("environment", environment())
        with tempfile.TemporaryDirectory() as directory:
            payload_path = Path(directory) / "payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(HARNESS), str(CLIENT_UI), str(payload_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, (completed.stdout or "") + (completed.stderr or ""))
            return json.loads(completed.stdout or "{}")

    def test_a_card_shows_versions_chip_action_and_the_three_detail_blocks(self) -> None:
        result = self._render(modloaders=[modloader(
            installed=True, installed_version="0.7.2", latest_version="0.7.3",
            update_available=True, files=131,
        )])

        self.assertIsNone(result["error"])
        self.assertEqual(result["count"], "1 mod loaders")
        card = result["cards"][0]
        self.assertEqual(card["className"], "modloader-card")
        title = find(card, lambda node: node["className"] == "modloader-title")
        self.assertEqual(texts(title), ["lavagang.melonloader", "Update available"])
        chip = find(title, lambda node: node["className"].startswith("state-chip"))
        self.assertIn("update", chip["className"])
        versions = find(card, lambda node: node["className"] == "modloader-versions")
        self.assertIn("0.7.2", versions["text"])
        self.assertIn("0.7.3", versions["text"])
        self.assertIn("131 files", " ".join(texts(card)))
        self.assertEqual(
            [button["text"] for button in result["buttons"][0]],
            ["Repository", "Update", "Remove"],
        )

        sections = next(
            child for child in card["children"] if child["className"].startswith("detail-sections")
        )
        self.assertIsNotNone(find(sections, lambda node: node["text"] == "DEPENDENCIES"))
        self.assertIsNotNone(find(sections, lambda node: node["text"] == "RECOMMENDED MODS"))

    def test_the_supply_block_lists_each_type_with_its_install_directory(self) -> None:
        result = self._render(modloaders=[modloader()])

        card = result["cards"][0]
        block = find(card, lambda node: node["text"] == "PROVIDES")
        self.assertIsNotNone(block, "「提供」那一块要说清每种类型装到哪里")
        section = next(
            child for child in card["children"]
            if child["className"] == "dependency-section" and find(child, lambda node: node["text"] == "PROVIDES")
        )
        lines = [line for line in flatten(section) if line["className"] == "dependency-line"]
        self.assertEqual(
            [texts(line) for line in lines],
            [
                ["melonloader:core", "{Sprocket}"],
                ["melonloader:mod", "{Sprocket}/Mods"],
            ],
        )

    def test_dependencies_and_recommendations_are_rendered(self) -> None:
        result = self._render(modloaders=[modloader()])

        card = result["cards"][0]
        dependencies = find(card, lambda node: node["text"] == "DEPENDENCIES")
        self.assertIsNotNone(dependencies)
        self.assertIn("furryaxw.sprocket-mod-api", " ".join(texts(card)))
        self.assertIn(">=0.4.0", " ".join(texts(card)))
        self.assertIsNotNone(find(card, lambda node: node["text"] == "RECOMMENDED MODS"))
        self.assertIn("furryaxw.cannon-sound-pool-fix", " ".join(texts(card)))

    def test_an_uninstalled_loader_offers_install_and_an_incompatible_one_is_marked(self) -> None:
        result = self._render(modloaders=[modloader(compatible="incompatible")])

        chip = find(result["cards"][0], lambda node: node["className"].startswith("state-chip"))
        self.assertEqual(chip["text"], "Incompatible")
        self.assertEqual(result["buttons"][0][1]["text"], "Install")
        self.assertEqual(len(result["buttons"][0]), 2, "没装过就没有卸载按钮")

    def test_an_installed_up_to_date_loader_offers_reinstall_and_remove(self) -> None:
        result = self._render(modloaders=[modloader(installed=True, installed_version="0.7.3")])

        chip = find(result["cards"][0], lambda node: node["className"].startswith("state-chip"))
        self.assertEqual(chip["text"], "Installed")
        self.assertEqual([button["text"] for button in result["buttons"][0]], ["Repository", "Reinstall", "Remove"])

    def test_install_hands_the_loader_to_the_shared_install_flow(self) -> None:
        result = self._render(
            modloaders=[modloader(installed=True, installed_version="0.7.2", update_available=True)],
            click_primary=0,
        )

        calls = [entry["args"] for entry in result["apiCalls"] if entry.get("kind") == "call"]
        self.assertIn(
            ["beginInstall", "lavagang.melonloader"],
            calls,
            "加载器安装走模组那条路：解析 → 安装确认 → 入队，页面不自己调安装接口",
        )
        self.assertEqual([entry for entry in result["apiCalls"] if entry["kind"] == "error"], [])

    def test_reinstalling_a_loader_asks_for_a_plan_with_the_installed_version_included(self) -> None:
        """只有一个可安装版本的加载器（BepInEx）也要能重装：确认框不能被「版本多于一个」挡住。"""
        result = self._render(
            modloaders=[
                modloader(
                    "bepinex.bepinex-be",
                    installed=True,
                    installed_version="6.0.0-be.788",
                    latest_version="6.0.0-be.788",
                )
            ],
            click_primary=0,
        )

        entries = [
            entry
            for entry in result["apiCalls"]
            if entry.get("kind") == "call" and entry["args"][:1] == ["beginInstall"]
        ]
        self.assertEqual(len(entries), 1)
        self.assertTrue(
            entries[0]["includeInstalled"],
            "页面点安装时就要带 include_installed，后端才不会把「已装同一版」跳过",
        )

    def test_remove_confirms_then_calls_the_api(self) -> None:
        result = self._render(modloaders=[modloader(installed=True, installed_version="0.7.3")], click_remove=0)

        calls = [entry["args"] for entry in result["apiCalls"] if entry.get("kind") == "call"]
        self.assertIn(["remove_modloader", "lavagang.melonloader"], calls)

    def test_a_cancelled_remove_does_not_call_the_api(self) -> None:
        result = self._render(
            modloaders=[modloader(installed=True, installed_version="0.7.3")],
            click_remove=0,
            confirm=False,
        )

        calls = [entry["args"] for entry in result["apiCalls"] if entry.get("kind") == "call"]
        self.assertNotIn(["remove_modloader", "lavagang.melonloader"], calls)

    def test_the_sidebar_lists_installed_loaders_and_hides_the_link(self) -> None:
        result = self._render(modloaders=[modloader()])

        self.assertEqual(result["sidebar"]["loaders"], ["lavagang.melonloader 0.7.3"])
        self.assertTrue(result["sidebar"]["installHidden"])

    def test_the_sidebar_links_to_the_page_when_no_loader_is_installed(self) -> None:
        result = self._render(modloaders=[modloader()], environment=environment(installed=False))

        self.assertEqual(result["sidebar"]["loaders"], [])
        self.assertFalse(result["sidebar"]["installHidden"])

    def test_the_sidebar_catches_up_when_the_registry_loads(self) -> None:
        """启动先读一次环境（那时还没有注册表），注册表随目录加载后再读一次。

        两次读数之间 `revision` 不动，但加载器清单从空变成「装了 0.7.3」—— 左下角必须跟上。
        """
        before_registry = {
            "sprocket": {"state": "ok", "version": "0.2.53.2"},
            "loaders": {},
            "environment": {"state": "unknown", "entry": None, "loader": "", "table_source": "missing"},
            "revision": 0,
        }
        after_registry = environment()
        after_registry["revision"] = 0

        result = self._render(
            modloaders=[modloader(installed=True, installed_version="0.7.3")],
            environment=None,
            environment_sequence=[before_registry, after_registry],
        )

        self.assertIsNone(result["error"])
        self.assertEqual(result["sidebar"]["loaders"], ["lavagang.melonloader 0.7.3"])
        self.assertTrue(result["sidebar"]["installHidden"], "装了加载器就不再显示安装链接")

    def test_an_empty_catalog_says_so(self) -> None:
        result = self._render(modloaders=[])

        self.assertEqual(result["count"], "0 mod loaders")
        self.assertEqual(result["cards"][0]["className"], "empty-list")
        self.assertEqual(result["cards"][0]["children"][0]["text"], "The registry has no loader package")


if __name__ == "__main__":
    unittest.main()
