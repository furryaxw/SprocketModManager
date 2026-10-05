"""「错误修复」页的 API：一次诊断把本机事实与最新日志跑成两级报告，且不碰任何文件。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_adoption import package as mod_package  # noqa: E402
from test_environment import (  # noqa: E402
    FIXTURE_MOD,
    LOADER_ID,
    _loader_table,
    detected_melonloader,
    game_dir_with_version,
    loader_package,
    unity_payload,
)
from sprocket_mod_manager.application import diagnosis  # noqa: E402
from sprocket_mod_manager.application.data_hub import KEY_DIAGNOSIS  # noqa: E402
from sprocket_mod_manager.application.diagnosis import (  # noqa: E402
    LogSpec,
    build_facts,
    run_diagnosis,
)
from sprocket_mod_manager.domain.compatibility import CapabilityEnvironment  # noqa: E402
from sprocket_mod_manager.domain.registry import Registry  # noqa: E402
from sprocket_mod_manager.infrastructure.app_logging import manager_log_path  # noqa: E402
from sprocket_mod_manager.infrastructure.config import ConfigStore  # noqa: E402
from sprocket_mod_manager.presentation.web_gui import ClientApi  # noqa: E402

# 用例自带规则包：诊断只跑这里的三条规则，用例不读注册表那侧的 diagnosis.json。
RULE_PACK = {
    "schema_version": 1,
    "pack_version": 1,
    "entries": [
        {
            "id": "no-loader-installed",
            "check": "loader_missing",
            "bucket": "optional",
            "level": 2,
            "title": {"zh": "还没有装加载器", "en": "No loader is installed"},
        },
        {
            "id": "mod-hook-signature-missing",
            "match": {
                "sources": ["loader_log"],
                "pattern": "Could not find any iCall with the signature '([^']+)'",
                "min_count": 1,
            },
            "bucket": "optional",
            "level": 3,
            "title": {
                "zh": "某个模组的运行时挂钩没接上",
                "en": "A mod hook could not be attached",
            },
            "tutorial": {
                "en": [
                    "Open the Installed page and find the mod that reported this",
                    "Choose Reinstall to move to the latest release",
                ]
            },
            "go_to": {"page": "installed"},
        },
        {
            "id": "unity-online-services-unreachable",
            "match": {
                "sources": ["unity_log", "loader_log"],
                "pattern": "Could not resolve host: [^ ]*unity3d\\.com",
                "min_count": 1,
            },
            "bucket": "optional",
            "level": 5,
            "title": {
                "zh": "Unity 的在线服务连不上",
                "en": "Unity online services are unreachable",
            },
        },
    ],
}

ICALL_LINE = (
    "[07:55:36.342] [WARNING] [UnityExplorer] [UniverseLib] "
    "Could not find any iCall with the signature 'UnityEngine.AssetBundle::LoadAsset_Internal_Injected'!"
)
CURL_LINE = "Curl error 6: Could not resolve host: cdp.cloud.unity3d.com"
COMPANY = "HD"
PRODUCT = "Sprocket"


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class DiagnosisApiTests(unittest.TestCase):
    """从 `run_diagnosis` 拿到的报告：两级分栏、证据行号、以及一个字节都不写。"""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.local_low = self.root / "LocalLow"
        # Unity 日志的位置从 `USERPROFILE` 推出来；测试把它按到自己的临时目录上。
        patcher = patch(
            "sprocket_mod_manager.application.unity_logs.local_low_root",
            return_value=self.local_low,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _game(self, *, version: str = "0.2.53.2", app_info: bool = True) -> Path:
        game = game_dir_with_version(self.root, unity_payload(version))
        if app_info:
            (game / "Sprocket_Data" / "app.info").write_text(
                f"{COMPANY}\n{PRODUCT}\n", encoding="utf-8"
            )
        return game

    def _client(self, game: Path, *, pack: dict | None = None) -> ClientApi:
        app_dir = self.root / "app"
        ConfigStore(app_dir).save({"language": "zh", "game_path": str(game), "index_url": ""})
        # 打包运行时这份日志总在；测试里自己造一份，管理器那一栏才有东西可读。
        manager_log = manager_log_path(app_dir)
        manager_log.parent.mkdir(parents=True, exist_ok=True)
        manager_log.write_text("[00:00:00.000] manager ready\n", encoding="utf-8")
        api = ClientApi("test", app_dir=app_dir)
        api.service.registry = Registry(
            [loader_package()],
            _loader_table(),
            diagnosis=RULE_PACK if pack is None else pack,
        )
        self.addCleanup(api.install_queue.close)
        self.addCleanup(api._environment_monitor.stop)
        return api

    def _player_log(self, text: str) -> Path:
        directory = self.local_low / COMPANY / PRODUCT
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "Player.log"
        path.write_text(text, encoding="utf-8")
        return path

    def _loader_log(self, game: Path, text: str) -> Path:
        path = game / "MelonLoader" / "Latest.log"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_signature_in_the_loader_log_becomes_a_finding(self) -> None:
        game = self._game()
        detected_melonloader(game)
        self._loader_log(game, f"fine\n{ICALL_LINE}\nfine\n")
        api = self._client(game)

        report = api.run_diagnosis()["report"]

        finding = next(
            item for item in report["optional"] if item["rule"] == "mod-hook-signature-missing"
        )
        self.assertEqual(
            finding["params"]["group1"], "UnityEngine.AssetBundle::LoadAsset_Internal_Injected"
        )
        self.assertEqual(finding["params"]["count"], "1")
        self.assertEqual(finding["log_line"]["source"], "MelonLoader/Latest.log")
        self.assertEqual(finding["log_line"]["number"], 2, "证据要能翻到文件里的那一行")
        self.assertIn(ICALL_LINE, finding["log_line"]["text"])

    def test_the_report_is_pushed_while_it_is_being_built(self) -> None:
        """现状走数据层：扫描一开始推一份，收尾再推一份 —— 界面不用等这条调用返回。"""
        game = self._game()
        detected_melonloader(game)
        self._loader_log(game, f"{ICALL_LINE}\n")
        api = self._client(game)

        report = api.run_diagnosis()["report"]

        self.assertEqual(api.data.get(KEY_DIAGNOSIS), report, "数据层最后那份就是返回值")
        self.assertFalse(report["running"], "收尾那份说明扫完了")
        self.assertGreaterEqual(api.data.revision(KEY_DIAGNOSIS), 2, "扫描期间至少还推过一次")

    def test_the_unity_log_is_located_through_app_info(self) -> None:
        game = self._game()
        detected_melonloader(game)
        player_log = self._player_log(f"{CURL_LINE}\n{CURL_LINE}\n")
        api = self._client(game)

        report = api.run_diagnosis()["report"]

        finding = next(
            item for item in report["optional"] if item["rule"] == "unity-online-services-unreachable"
        )
        self.assertEqual(finding["params"]["count"], "2")
        self.assertEqual(finding["log_line"]["source"], str(player_log))

    def test_a_game_without_app_info_offers_no_unity_log(self) -> None:
        game = self._game(app_info=False)
        detected_melonloader(game)
        self._player_log(CURL_LINE)
        api = self._client(game)

        report = api.run_diagnosis()["report"]

        self.assertNotIn("unity_log", {source["role"] for source in report["sources"]})
        self.assertFalse(
            [item for item in report["optional"] if item["rule"] == "unity-online-services-unreachable"]
        )

    def test_a_rule_whose_log_is_absent_is_reported_as_unjudged(self) -> None:
        game = self._game()
        api = self._client(game)

        report = api.run_diagnosis()["report"]

        reasons = {item["rule"]: item["reason"] for item in report["unjudged"]}
        self.assertEqual(reasons["unity-online-services-unreachable"], "log_missing")

    def test_a_missing_pack_is_reported_instead_of_silence(self) -> None:
        game = self._game()
        detected_melonloader(game)
        api = self._client(game, pack={"schema_version": 1, "pack_version": 0, "entries": []})

        report = api.run_diagnosis()["report"]

        self.assertEqual(report["pack_source"], "missing")
        self.assertEqual(report["rules_total"], 0)
        self.assertEqual(report["required"], [])
        self.assertIn({"rule": "", "reason": "pack_missing"}, report["unjudged"])

    def test_each_bucket_is_sorted_by_level(self) -> None:
        game = self._game(version="0.2.54.2")
        detected_melonloader(game)
        api = self._client(game)

        report = api.run_diagnosis()["report"]

        for bucket in ("required", "optional"):
            levels = [item["level"] for item in report[bucket]]
            self.assertEqual(levels, sorted(levels), bucket)

    def test_a_loader_installed_outside_the_manager_is_not_reported_as_missing(self) -> None:
        """磁盘上检测到运行时就算装上了：管理器之外装的加载器不能被报成「还没有装」。"""
        game = self._game()
        detected_melonloader(game)
        api = self._client(game)

        report = api.run_diagnosis()["report"]

        rules = [item["rule"] for item in report["required"] + report["optional"]]
        self.assertNotIn("no-loader-installed", rules)

    def test_a_game_without_any_loader_at_all_is_reported(self) -> None:
        game = self._game()
        api = self._client(game)

        report = api.run_diagnosis()["report"]

        rules = [item["rule"] for item in report["optional"]]
        self.assertIn("no-loader-installed", rules)

    def test_the_pack_version_travels_with_the_report(self) -> None:
        game = self._game()
        api = self._client(game)

        report = api.run_diagnosis()["report"]

        self.assertEqual(report["pack_source"], "registry")
        self.assertEqual(report["pack_version"], RULE_PACK["pack_version"])
        self.assertEqual(report["rules_total"], len(RULE_PACK["entries"]))
        self.assertGreater(report["lines_read"], 0, "管理器自己那份日志总是读得到")

    def test_a_finding_carries_both_languages_and_an_in_app_target(self) -> None:
        game = self._game()
        detected_melonloader(game)
        self._loader_log(game, f"{ICALL_LINE}\n")
        api = self._client(game)

        finding = next(
            item
            for item in api.run_diagnosis()["report"]["optional"]
            if item["rule"] == "mod-hook-signature-missing"
        )

        self.assertEqual(finding["title"]["zh"], "某个模组的运行时挂钩没接上")
        self.assertTrue(finding["title"]["en"])
        self.assertEqual(finding["tutorial"]["en"][0][:4], "Open")
        self.assertEqual(finding["go_to"], {"page": "installed"})

    def test_diagnosis_writes_nothing(self) -> None:
        """诊断自己不落任何东西：跑完之后游戏目录与 LocalLow 一个字节都没变。

        安装记录的对账是刷新路径一直在做的事（已安装页每次刷新都走），不是诊断带进来的写入。
        """
        game = self._game()
        detected_melonloader(game)
        self._loader_log(game, f"{ICALL_LINE}\n")
        self._player_log(f"{CURL_LINE}\n")
        api = self._client(game)
        before_game = snapshot(game)
        before_low = snapshot(self.local_low)

        api.run_diagnosis()

        self.assertEqual(snapshot(game), before_game)
        self.assertEqual(snapshot(self.local_low), before_low)

    def test_the_upload_picker_offers_the_game_log(self) -> None:
        game = self._game()
        detected_melonloader(game)
        player_log = self._player_log(CURL_LINE)
        api = self._client(game)

        sources = api.get_log_sources()["sources"]

        unity = next(source for source in sources if source["id"] == "unity")
        self.assertEqual(unity["kind"], "game")
        self.assertEqual(unity["loader"], "Unity")
        self.assertEqual(unity["path"], str(player_log))
        self.assertTrue(unity["available"])
        self.assertEqual(sources[0]["id"], "manager")
        self.assertNotIn("target", unity, "给界面看的读数里不带要读的文件")

    def test_the_manager_log_is_read_first(self) -> None:
        game = self._game()
        api = self._client(game)

        report = api.run_diagnosis()["report"]

        self.assertEqual(report["sources"][0]["role"], "manager_log")
        self.assertTrue(report["sources"][0]["path"])


class FactBuildingTests(unittest.TestCase):
    """`build_facts()`：规则包能读到的那份事实，缺什么就少什么。"""

    def _registry(self) -> Registry:
        return Registry(
            [
                loader_package(),
                mod_package(
                    "fixture.sprocket-mod",
                    "FixtureMod.dll",
                    FIXTURE_MOD.read_bytes(),
                    repository="fixture/FixtureMod",
                ),
            ],
            _loader_table(),
        )

    def test_a_broken_loader_and_a_broken_mod_are_told_apart(self) -> None:
        facts = build_facts(
            game_dir=Path("G:/game"),
            registry=self._registry(),
            installed={
                "lavagang.melonloader": {"corrupted": True},
                "fixture.sprocket-mod": {"corrupted": True},
                "not.in.the.registry": {"corrupted": True},
                "fixture.sprocket-mod.healthy": {"corrupted": False},
            },
            environment=None,
        )

        broken = {item["id"]: item for item in facts["packages_broken"]}
        self.assertTrue(broken["lavagang.melonloader"]["is_loader"])
        self.assertFalse(broken["fixture.sprocket-mod"]["is_loader"])
        self.assertEqual(broken["lavagang.melonloader"]["name"], "MelonLoader")
        self.assertNotIn("fixture.sprocket-mod.healthy", broken)
        self.assertEqual(facts["loaders_installed"], ["lavagang.melonloader"])

    def test_an_environment_conflict_carries_the_id_and_the_readable_name(self) -> None:
        environment = CapabilityEnvironment(
            sprocket="0.2.55.5",
            sprocket_state="ok",
            loaders={LOADER_ID: "0.7.3"},
            installed_loaders=frozenset({LOADER_ID}),
            table=_loader_table(sprocket_range="<0.2.54.0"),
        )

        facts = build_facts(
            game_dir=Path("G:/game"),
            registry=self._registry(),
            installed={},
            environment=environment,
        )

        self.assertEqual(
            facts["environment_conflict"],
            {"conflict": True, "loader": LOADER_ID, "loader_name": "MelonLoader"},
        )
        self.assertEqual(facts["game_version"], "0.2.55.5")

    def test_a_consistent_environment_reports_no_conflict(self) -> None:
        environment = CapabilityEnvironment(
            sprocket="0.2.53.2",
            sprocket_state="ok",
            loaders={LOADER_ID: "0.7.3"},
            installed_loaders=frozenset({LOADER_ID}),
            table=_loader_table(sprocket_range="<0.2.54.0"),
        )

        facts = build_facts(
            game_dir=Path("G:/game"),
            registry=self._registry(),
            installed={},
            environment=environment,
        )

        self.assertEqual(facts["environment_conflict"], {"conflict": False})

    def test_without_a_game_directory_nothing_claims_a_version(self) -> None:
        facts = build_facts(
            game_dir=None, registry=self._registry(), installed=None, environment=None
        )

        self.assertFalse(facts["game_configured"])
        self.assertEqual(facts["game_version"], "")
        self.assertEqual(facts["packages_broken"], [])

    def test_a_registry_without_a_loader_says_so(self) -> None:
        facts = build_facts(
            game_dir=Path("G:/game"),
            registry=Registry([], _loader_table()),
            installed={},
            environment=None,
        )

        self.assertFalse(facts["loaders_available"])

    def test_a_loader_detected_on_disk_counts_as_installed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = game_dir_with_version(Path(directory), unity_payload("0.2.53.2"))
            detected_melonloader(game)
            facts = build_facts(
                game_dir=game,
                registry=self._registry(),
                installed={},
                environment=None,
            )

        self.assertEqual(facts["loaders_installed"], [LOADER_ID])


class StreamingDeliveryTests(unittest.TestCase):
    """边扫边交：错误列表先建出来，随后日志的命中一条条交出去。"""

    def _run(self, log_text: str, *, on_progress, interval: float | None = None) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Latest.log"
            path.write_text(log_text, encoding="utf-8")
            context = (
                patch.object(diagnosis, "PROGRESS_SECONDS", interval)
                if interval is not None
                else nullcontext()
            )
            with context:
                return run_diagnosis(
                    game_dir=None,
                    registry=None,
                    installed={},
                    environment=None,
                    pack=RULE_PACK,
                    pack_source="registry",
                    specs=(LogSpec(role="loader_log", label="Latest.log", path=path),),
                    on_progress=on_progress,
                )

    def test_the_error_list_is_handed_over_before_the_logs_are_read(self) -> None:
        seen: list[dict] = []

        report = self._run("nothing to see\n", on_progress=seen.append)

        self.assertTrue(seen, "扫描一开始就该交一份现状出来")
        self.assertTrue(seen[0]["running"], "第一份现状说的是「正在扫描」")
        self.assertFalse(seen[-1]["running"], "收尾那一份说明扫完了")
        self.assertEqual(seen[-1], report, "收尾那份与返回值是同一份")

    def test_a_hit_is_handed_over_while_the_scan_still_runs(self) -> None:
        seen: list[dict] = []

        self._run(
            "\n".join(["filler", "filler", ICALL_LINE]) + "\n",
            on_progress=seen.append,
            interval=0.0,
        )

        during = [state for state in seen if state["running"]]
        self.assertTrue(
            any(state["required"] or state["optional"] for state in during),
            "命中在扫描结束之前就交出去了",
        )

    def test_the_reading_says_which_log_was_read(self) -> None:
        seen: list[dict] = []

        report = self._run(ICALL_LINE + "\n", on_progress=seen.append)

        self.assertEqual(report["sources"][0]["label"], "Latest.log")
        self.assertEqual(report["sources"][0]["status"], "", "读到了就是 ok（空串）")
        self.assertEqual(report["sources"][0]["lines"], 1)
        self.assertEqual(report["lines_read"], 1)


if __name__ == "__main__":
    unittest.main()
