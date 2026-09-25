from __future__ import annotations

import unittest
from pathlib import Path

CLIENT_UI = Path(__file__).resolve().parent.parent / "sprocket_mod_manager" / "presentation" / "client_ui"


def client_javascript(root: Path = CLIENT_UI) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted((root / "js").glob("*.js")))


class LocalModsClientUiTests(unittest.TestCase):
    """客户端脚本要真正消费 `get_local_mods` / `toggle_mod`，而不是只把后端接口晾在那儿。"""

    def setUp(self) -> None:
        self.javascript = client_javascript()
        self.installs = (CLIENT_UI / "js" / "installs.js").read_text(encoding="utf-8")
        self.data = (CLIENT_UI / "js" / "data.js").read_text(encoding="utf-8")
        self.business = (CLIENT_UI / "js" / "business.js").read_text(encoding="utf-8")

    def test_installed_page_consumes_local_metadata(self) -> None:
        # 这一份读数由数据层推来、页面只读镜像（不在页面上从接口返回值另存一份）。
        self.assertIn("localMods: () => dataValue(\"installed\")?.local_mods || []", self.data)
        self.assertIn("const scanned = state.localMods || []", self.installs)
        self.assertIn("function renderScannedModRow(mod)", self.installs)
        self.assertIn("function renderLegacyModRow(item)", self.installs)
        self.assertIn("local?.display_name", self.installs)
        self.assertIn("mod.display_name", self.installs)
        self.assertIn("mod.disabled", self.installs)

    def test_recognized_mods_use_the_cached_registry_localization(self) -> None:
        self.assertIn('localized(mod.registry_display_name, "")', self.installs)

    def test_adoption_never_blocks_the_list(self) -> None:
        # 认领必须在 renderInstalled() 之后异步触发，而不是等它返回再渲染。
        render = self.business.index("renderInstalled();")
        claim = self.business.index("void claimExistingMods();")
        self.assertLess(render, claim, "the list renders before the network-bound claim starts")
        self.assertIn('callApi("adopt_existing")', self.installs)
        self.assertIn("let claimInFlight = false;", self.installs, "claims must not stack up")

    def test_installed_page_toggles_mods_through_the_api(self) -> None:
        self.assertIn('callApi("toggle_mod", path, Boolean(enabled))', self.installs)
        self.assertIn("function toggleLocalMod(path, enabled)", self.installs)
        self.assertIn('toggle.addEventListener("click", () => toggleLocalMod(local.path, local.disabled))', self.installs)
        self.assertIn('toast(tr(enabled ? "modEnabledRestart" : "modDisabledRestart", {name: result.toggled}))', self.installs)
        self.assertIn('setStatus("", "ready")', self.installs, "状态栏只更新健康状态，话已经由 toast 说过")

    def test_installed_rows_open_the_catalog_on_double_click_and_select_on_right_click(self) -> None:
        """双击跳到模组目录里这个包的位置，右键做多选；行内控件不被这两种点击接管。"""
        catalog = (CLIENT_UI / "js" / "catalog.js").read_text(encoding="utf-8")
        html = (CLIENT_UI / "index.html").read_text(encoding="utf-8")
        self.assertIn("function wireInstalledRow(row, item, key)", self.installs)
        self.assertIn('row.addEventListener("dblclick"', self.installs)
        self.assertIn("await focusPackage(id)", self.installs)
        self.assertIn('row.addEventListener("contextmenu"', self.installs)
        self.assertIn('callApi("open_mod_location", path)', self.installs, "目录里查无此包时的兜底")
        self.assertIn("async function focusPackage(packageId)", catalog)
        self.assertIn("state.selectedId = packageId", catalog)
        self.assertIn('data-i18n="installedRowHint"', html)

    def test_previous_unrecognized_shape_still_renders(self) -> None:
        self.assertIn("unrecognized: () => dataValue(\"installed\")?.unrecognized || []", self.data)
        self.assertIn("if (item.unrecognized)", self.installs)
        self.assertIn('tr("unrecognized")', self.installs)

    def test_installed_page_shows_required_dependencies(self) -> None:
        self.assertIn("mod.required_dependencies", self.installs)
        self.assertIn('tr("requiresLabel")', self.installs)
        self.assertIn('tr("incompatibleLabel")', self.installs)
        for key in ("requiresLabel", "incompatibleLabel", "localOnly"):
            self.assertGreaterEqual(
                self.javascript.count(f"{key}:"),
                2,
                f"{key} must be defined for both zh and en",
            )

    def test_installed_page_shows_missing_dependencies_from_dll_metadata(self) -> None:
        """依赖缺口是 DLL 元数据算出来的，列表里必须看得见（不是只在后端躺着）。"""
        self.assertIn("mod.missing_dependencies", self.installs)
        self.assertIn('tr("missingLabel")', self.installs)
        self.assertIn('tr("missingDepsCount", {count: missing})', self.installs)
        for key in ("missingLabel", "missingDepsCount"):
            self.assertGreaterEqual(
                self.javascript.count(f"{key}:"),
                2,
                f"{key} must be defined for both zh and en",
            )

    def test_new_labels_exist_in_both_languages(self) -> None:
        for key in (
            "enableMod",
            "disableMod",
            "requiresLabel",
            "incompatibleLabel",
            "localOnly",
            "modDisabledRestart",
            "modEnabledRestart",
            "restartRequired",
            "installedFilterIncompatible",
        ):
            self.assertGreaterEqual(
                self.javascript.count(f"{key}:"),
                2,
                f"{key} must be defined for both zh and en",
            )


if __name__ == "__main__":
    unittest.main()
