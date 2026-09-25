"""目录页拿到的兼容判定，以及「默认装哪个版本」。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.application.service import ModManagerService
from sprocket_mod_manager.infrastructure.config import ConfigStore
from sprocket_mod_manager.presentation.web_gui import ClientApi

PACKAGE = {
    "id": "test.mod",
    "name": "TestMod",
    "display_name": {"en": "Test Mod", "zh": "测试模组"},
    "description": {"en": "", "zh": ""},
    "authors": ["someone"],
    "repository": "test/repo",
    "license": "MIT",
    "category": "utility",
    "tags": [],
    "dependencies": [],
    "recommendations": [],
    "install": {},
    "release": {
        "include_prerelease": False,
        "version_pattern": r"^v?(\d+\.\d+\.\d+)$",
        "assets": {"include": ["TestMod.dll"], "exclude": []},
    },
    "releases": [
        {
            "id": 2,
            "tag": "v1.1.0",
            "version": "1.1.0",
            "prerelease": False,
            "published_at": "2026-09-02T00:00:00Z",
            "page_url": "https://github.com/test/repo/releases/tag/v1.1.0",
            "assets": [
                {
                    "id": 21,
                    "name": "TestMod.dll",
                    "size": 10,
                    "download_url": "https://github.com/test/repo/releases/download/v1.1.0/TestMod.dll",
                }
            ],
            "dependencies": [{"id": "hamish.sprocket", "version": ">=0.2.54.0"}],
            "compatibility": {"source": "declared"},
        },
        {
            "id": 1,
            "tag": "v1.0.0",
            "version": "1.0.0",
            "prerelease": False,
            "published_at": "2026-09-01T00:00:00Z",
            "page_url": "https://github.com/test/repo/releases/tag/v1.0.0",
            "assets": [
                {
                    "id": 11,
                    "name": "TestMod.dll",
                    "size": 10,
                    "download_url": "https://github.com/test/repo/releases/download/v1.0.0/TestMod.dll",
                }
            ],
            "dependencies": [{"id": "hamish.sprocket", "version": ">=0.2.53.0 <0.2.54.0"}],
            "compatibility": {"source": "declared"},
        },
    ],
}


def game_dir(root: Path, version: str = "0.2.53.2") -> Path:
    game = root / "game"
    (game / "Sprocket_Data").mkdir(parents=True, exist_ok=True)
    (game / "Sprocket.exe").touch()
    (game / "Sprocket_Data" / "globalgamemanagers").write_bytes(
        b"2022.3.62f2\x00\x08\x00\x00\x00" + version.encode() + b"\x01\x00\x00\x00"
    )
    return game


def index_file(root: Path, *, sprocket_range: str = "<0.2.54.0", packages: list | None = None) -> Path:
    payload = {
        "schema_version": 1,
        "game": {"id": "hamish.sprocket", "name": "Sprocket"},
        "providers": {
            "schema_version": 2,
            "entries": [{"loader": "lavagang.melonloader", "version": ">=0.7.0 <0.8.0", "sprocket": sprocket_range}],
        },
        "generated_at": "2026-09-25T00:00:00Z",
        "packages": [PACKAGE] if packages is None else packages,
    }
    path = root / "index.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def embedded(
        package_id: str,
        *,
        version: str,
        sprocket_range: str,
        dependencies=(),
        when: str = "*",
        category: str = "utility",
) -> dict:
    """造一个带内嵌 release 的包（release 级 dependencies 写环境轴，包级写模组依赖）。"""
    payload = json.loads(json.dumps(PACKAGE))
    payload["id"] = package_id
    payload["name"] = package_id
    payload["display_name"] = {"en": package_id}
    payload["category"] = category
    payload["dependencies"] = [dict(item, when=when) for item in dependencies]
    payload["releases"] = [
        {
            "id": abs(hash(package_id + version)) % 100000,
            "tag": f"v{version}",
            "version": version,
            "prerelease": False,
            "published_at": "2026-09-02T00:00:00Z",
            "page_url": f"https://github.com/test/repo/releases/tag/v{version}",
            "assets": [
                {
                    "id": 1,
                    "name": "TestMod.dll",
                    "size": 10,
                    "download_url": f"https://github.com/test/repo/releases/download/v{version}/TestMod.dll",
                }
            ],
            "dependencies": [{"id": "hamish.sprocket", "version": sprocket_range}],
            "compatibility": {"source": "declared"},
        }
    ]
    return payload


def catalog_packages(api: ClientApi) -> list:
    """目录读数（`packages`）归数据层：测试从那里读，不从接口返回值拿。"""
    return (api.data.get("catalog") or {}).get("packages") or []


class CatalogVerdictTests(unittest.TestCase):
    def _api(self, root: Path, **kwargs) -> ClientApi:
        app_dir = root / "app"
        game = game_dir(root)
        ConfigStore(app_dir).save(
            {"language": "zh", "game_path": str(game), "index_url": str(index_file(root, **kwargs))}
        )
        service = ModManagerService(app_dir)
        return ClientApi("test", app_dir=app_dir, service_factory=lambda _app_dir: service)

    def _close(self, api: ClientApi) -> None:
        api._environment_monitor.stop()
        api.install_queue.close()
        api.data.close()

    def test_the_catalog_payload_only_carries_the_catalog(self) -> None:
        """目录读数归数据层：`load_catalog` 只回 ack，不在返回值里夹带任何一份读数。"""
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory))
            try:
                payload = api.load_catalog()
                packages = catalog_packages(api)
            finally:
                self._close(api)

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["count"], len(packages))
        self.assertTrue(packages, "目录读数在数据层里")
        for key in ("packages", "installed", "unrecognized", "local_mods", "local_summary", "has_any_mods"):
            self.assertNotIn(key, payload, f"目录读数不该再带 {key}")

    def test_every_release_carries_its_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory))
            try:
                api.load_catalog()
                packages = catalog_packages(api)
            finally:
                self._close(api)

        package = packages[0]
        self.assertEqual(package["release"]["version"], "1.1.0")
        self.assertEqual(package["release"]["verdict"], "incompatible")
        self.assertEqual(
            [(item["version"], item["verdict"]) for item in package["releases"]],
            [("1.1.0", "incompatible"), ("1.0.0", "compatible")],
        )
        self.assertEqual(package["releases"][1]["compatibility"], {"source": "declared"})

    def test_the_detail_axes_agree_with_the_sidebar(self) -> None:
        """详情各轴里的「本机」值必须能在侧栏那份读数里找到出处：两边读的是同一份环境。

        侧栏报 Sprocket 0.2.55.5 而详情轴写着别的版本，正是"两个目录的读数被拼在一起"的样子。
        """
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory))
            try:
                api.load_catalog()
                package = catalog_packages(api)[0]
                sidebar = api.get_environment()
            finally:
                self._close(api)

        axes = package["releases"][0]["axes"]
        self.assertIn(
            sidebar["sprocket"]["version"],
            {axis["local"] for axis in axes},
            f"游戏轴要报侧栏那个版本：{axes}",
        )
        # 注册表里有加载器包时，加载器那几条轴也走这段：它们的本机值同样得在侧栏里有出处。
        reported = {sidebar["sprocket"]["version"]} | {
            str(info.get("used_version") or info.get("version") or "")
            for info in (sidebar["loaders"] or {}).values()
        }
        for axis in axes:
            if not axis["local"]:
                continue
            self.assertIn(axis["local"], reported, f"{axis['id']} 的本机值在侧栏找不到出处")

    def test_a_conflicting_environment_caps_the_verdict_at_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "app"
            game = game_dir(root, "0.2.54.2")
            ConfigStore(app_dir).save(
                {"language": "zh", "game_path": str(game), "index_url": str(index_file(root))}
            )
            service = ModManagerService(app_dir)
            api = ClientApi("test", app_dir=app_dir, service_factory=lambda _app_dir: service)
            try:
                api._environment_monitor.note_latest_loaders({"lavagang.melonloader": "0.7.3"})
                api.load_catalog()
                packages = catalog_packages(api)
            finally:
                self._close(api)

        package = packages[0]
        self.assertEqual(
            [item["verdict"] for item in package["releases"]],
            ["unknown", "incompatible"],
            "1.1.0 声明支持这个游戏，但 0.7.3 的加载器还跟不上 → 黄；1.0.0 不支持这个游戏 → 红",
        )

    def test_the_default_install_picks_the_highest_compatible_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory))
            try:
                api.load_catalog()
                plan = api.plan_install(["test.mod"])
            finally:
                self._close(api)

        self.assertTrue(plan["ok"], plan)
        root = plan["plans"][0]["packages"][0]
        self.assertEqual(root["version"], "1.0.0", "1.1.0 不兼容就该退到 1.0.0")

    def test_an_explicitly_requested_version_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory))
            try:
                api.load_catalog()
                plan = api.plan_install(["test.mod"], {"test.mod": "1.1.0"})
            finally:
                self._close(api)

        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["plans"][0]["packages"][0]["version"], "1.1.0")


class EnvironmentChainTests(unittest.TestCase):
    """整条链都要能跑：依赖里那一环不兼容本机环境时，计划就该失败并说清原因。"""

    def _api(self, root: Path, packages: list) -> ClientApi:
        app_dir = root / "app"
        game = game_dir(root)
        ConfigStore(app_dir).save(
            {
                "language": "zh",
                "game_path": str(game),
                "index_url": str(index_file(root, packages=packages)),
            }
        )
        service = ModManagerService(app_dir)
        return ClientApi("test", app_dir=app_dir, service_factory=lambda _app_dir: service)

    def test_a_translation_package_is_marked_not_applicable(self) -> None:
        packages = [
            embedded(
                "test.translation",
                version="1.0.0",
                sprocket_range=">=0.2.54.0",
                category="translation",
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), packages)
            try:
                api.load_catalog()
                catalog = catalog_packages(api)
            finally:
                api._environment_monitor.stop()
                api.install_queue.close()
                api.data.close()

        entry = catalog[0]
        self.assertEqual(entry["release"]["verdict"], "not_applicable")
        self.assertEqual([item["verdict"] for item in entry["releases"]], ["not_applicable"])

    def test_a_dependency_that_needs_another_game_version_fails_the_plan(self) -> None:
        packages = [
            embedded(
                "test.root",
                version="1.0.0",
                sprocket_range=">=0.2.53.0 <0.2.54.0",
                dependencies=[{"id": "test.dep", "version": "*"}],
            ),
            embedded("test.dep", version="2.0.0", sprocket_range=">=0.2.54.0"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), packages)
            try:
                api.load_catalog()
                plan = api.plan_install(["test.root"])
            finally:
                api._environment_monitor.stop()
                api.install_queue.close()

        self.assertFalse(plan["ok"], plan)
        message = plan["message"]
        self.assertIn("test.dep", message)
        self.assertIn("2.0.0 requires another environment", message)
        self.assertIn("Sprocket 0.2.53.2", message)

    def test_the_same_chain_resolves_when_the_dependency_fits(self) -> None:
        packages = [
            embedded(
                "test.root",
                version="1.0.0",
                sprocket_range=">=0.2.53.0 <0.2.54.0",
                dependencies=[{"id": "test.dep", "version": "*"}],
            ),
            embedded("test.dep", version="2.0.0", sprocket_range="=0.2.53.2"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), packages)
            try:
                api.load_catalog()
                plan = api.plan_install(["test.root"])
            finally:
                api._environment_monitor.stop()
                api.install_queue.close()

        self.assertTrue(plan["ok"], plan)
        versions = {item["id"]: item["version"] for item in plan["plans"][0]["packages"]}
        self.assertEqual(versions, {"test.root": "1.0.0", "test.dep": "2.0.0"})

    def test_an_explicitly_picked_version_still_installs_when_it_is_red(self) -> None:
        # 界面上红色版本也允许选（只是不阻止），所以点名安装时不能在解析阶段被环境拦掉。
        packages = [
            embedded(
                "test.root",
                version="1.0.0",
                sprocket_range=">=0.2.54.0",
                dependencies=[{"id": "test.dep", "version": "*"}],
            ),
            embedded("test.dep", version="2.0.0", sprocket_range="=0.2.53.2"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), packages)
            try:
                api.load_catalog()
                plan = api.plan_install(["test.root"], {"test.root": "1.0.0"})
            finally:
                api._environment_monitor.stop()
                api.install_queue.close()

        self.assertTrue(plan["ok"], plan)
        versions = {item["id"]: item["version"] for item in plan["plans"][0]["packages"]}
        self.assertEqual(versions, {"test.root": "1.0.0", "test.dep": "2.0.0"})


if __name__ == "__main__":
    unittest.main()
