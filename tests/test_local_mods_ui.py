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
        self.assertIn("const scanned = (state.localMods || []).map", self.installs)
        # 一个包一行：记录各成一行，扫描到的文件按 `installed_package_id` 挂到它名下，没有归属的各自一行。
        self.assertIn("const byPackage = new Map();", self.installs)
        self.assertIn("installedPackageRow(record, byPackage.get(String(record.id)) || [])", self.installs)
        self.assertIn("if (!mod.installed_package_id) rows.push(mod);", self.installs)
        self.assertIn(
            "container.append(installedRowRecord(item) ? renderPackageRow(item) : renderScannedModRow(item));",
            self.installs,
        )
        self.assertIn("function renderPackageRow(item)", self.installs)
        self.assertIn("function renderScannedModRow(mod)", self.installs)
        # 名字取扫描结果那份：包行先看它名下第一个文件，无归属的行就是它自己。
        self.assertIn("local?.display_name", self.installs)
        self.assertIn("mod.display_name", self.installs)
        self.assertIn("mod.disabled", self.installs)
        # 记录带来的字段同样要落在行上：损坏芯片、可重装、文件清单（行左侧箭头展开）。
        self.assertIn("appendIntegrityChip(actions, record)", self.installs)
        self.assertIn("Boolean(record.corrupted)", self.installs)
        self.assertIn("const expander = rowExpander(item, key);", self.installs)
        self.assertIn("list.hidden = !expandedRows().has(key);", self.installs)
        self.assertIn('expander.addEventListener("click", (event) => {', self.installs)

    def test_recognized_mods_use_the_cached_registry_localization(self) -> None:
        # 两种行都先问缓存里的注册表条目：包行按包 id，无归属的行按扫描时匹配到的 id。
        self.assertIn('registryPackageLabel(mod.registry_id || mod.declared_id)', self.installs)
        self.assertIn("registryPackageLabel(item.id)", self.installs)
        self.assertIn('localized(mod.registry_display_name, "")', self.installs)
        self.assertIn('localized(local?.registry_display_name, "")', self.installs)

    def test_adoption_never_blocks_the_list(self) -> None:
        # 认领必须在 renderInstalled() 之后异步触发，而不是等它返回再渲染。
        catalog = (CLIENT_UI / "js" / "catalog.js").read_text(encoding="utf-8")
        finish = catalog.split("function finishCatalogLoad")[1].split("\n}")[0]
        self.assertLess(
            finish.index("renderInstalled();"),
            finish.index("void claimExistingMods();"),
            "the list renders before the network-bound claim starts",
        )
        self.assertIn('callApi("adopt_existing")', self.installs)
        self.assertIn("let claimInFlight = false;", self.installs, "claims must not stack up")

    def test_installed_page_toggles_mods_through_the_api(self) -> None:
        self.assertIn('callApi("toggle_mod", path, Boolean(enabled))', self.installs)
        self.assertIn("function toggleLocalMod(path, enabled)", self.installs)
        self.assertIn('toggle.addEventListener("click", () => toggleLocalMod(local.path, local.disabled))', self.installs)
        self.assertIn('toggle.addEventListener("click", () => toggleLocalMod(mod.path, Boolean(mod.disabled)))', self.installs)
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


class InstalledRenderReactionTests(unittest.TestCase):
    """「哪份读数变了就重画哪一块」写在 `business.js`：页面读到的 key 变了就必须重画那一页。"""

    def setUp(self) -> None:
        self.business = (
            Path(__file__).resolve().parent.parent
            / "sprocket_mod_manager" / "presentation" / "client_ui" / "js" / "business.js"
        ).read_text(encoding="utf-8")

    def watcher(self, name: str) -> str:
        return self.business.split(f"function {name}(")[1].split("\n}")[0]

    def test_every_reading_the_installed_page_renders_from_re_renders_it(self) -> None:
        """行上的按钮与 tooltip 依赖这些读数：队列（忙不忙）、目录与私有服务器（重装来源）、
        环境与加载器（兼容性那一句话）。少订阅哪一个，界面就会停在旧画面上等一次刷新。"""
        for name in (
            "watchInstalledData",
            "watchQueueData",
            "watchServersData",
            "watchEnvironmentData",
            "watchLoadersData",
        ):
            with self.subTest(watcher=name):
                self.assertIn("renderInstalled();", self.watcher(name))


if __name__ == "__main__":
    unittest.main()
