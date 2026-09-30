"""诊断规则包：规范化扛得住畸形输入，求值只认本机事实，命中按两级排好。"""

from __future__ import annotations

import json
import time
import unittest

from sprocket_mod_manager.domain.diagnosis import (
    BUCKET_OPTIONAL,
    BUCKET_REQUIRED,
    CHECKS,
    LOG_ROLES,
    UNJUDGED_GAME_NOT_CONFIGURED,
    UNJUDGED_LOG_MISSING,
    UNJUDGED_TIMEOUT,
    diagnosis_pack,
    evaluate,
    split_buckets,
)

TEXT = {"zh": "标题", "en": "Title"}


def log_rule(rule_id: str, *, pattern: str = "boom", bucket: str = BUCKET_OPTIONAL, level: int = 3, **extra):
    entry = {
        "id": rule_id,
        "bucket": bucket,
        "level": level,
        "title": TEXT,
        "match": {"sources": ["loader_log"], "pattern": pattern},
    }
    entry.update(extra)
    return entry


def state_rule(rule_id: str, *, check: str = "loader_missing", bucket: str = BUCKET_OPTIONAL, level: int = 3, **extra):
    entry = {"id": rule_id, "bucket": bucket, "level": level, "title": TEXT, "check": check}
    entry.update(extra)
    return entry


def pack_of(*entries) -> dict:
    return {"schema_version": 1, "pack_version": 7, "entries": list(entries)}


def lines_of(*texts, role: str = "loader_log", source: str = "MelonLoader/Latest.log"):
    return [
        {"role": role, "source": source, "number": index, "text": text}
        for index, text in enumerate(texts, start=1)
    ]


class PackNormalizationTests(unittest.TestCase):
    def test_a_usable_entry_keeps_its_identity_and_text(self) -> None:
        normalized = diagnosis_pack(pack_of(log_rule("a", explain={"zh": "为什么"})))

        self.assertEqual(normalized["pack_version"], 7)
        self.assertEqual([entry["id"] for entry in normalized["entries"]], ["a"])
        entry = normalized["entries"][0]
        self.assertEqual(entry["bucket"], BUCKET_OPTIONAL)
        self.assertEqual(entry["title"]["zh"], "标题")
        self.assertEqual(entry["explain"]["zh"], "为什么")
        self.assertIsNone(entry["check"])

    def test_a_normalized_pack_survives_json_round_trip(self) -> None:
        """规范化后的包要能写进本地缓存，所以里面不能有正则对象这类东西。"""
        normalized = diagnosis_pack(pack_of(log_rule("a"), state_rule("b")))

        self.assertEqual(json.loads(json.dumps(normalized, ensure_ascii=False)), normalized)

    def test_a_state_rule_is_dropped_when_its_check_does_not_exist(self) -> None:
        normalized = diagnosis_pack(pack_of(state_rule("a", check="no_such_check")))

        self.assertEqual(normalized["entries"], [])

    def test_a_rule_carrying_both_kinds_of_judgement_is_dropped(self) -> None:
        entry = state_rule("a")
        entry["match"] = {"sources": ["loader_log"], "pattern": "boom"}

        self.assertEqual(diagnosis_pack(pack_of(entry))["entries"], [])

    def test_a_rule_with_an_uncompilable_pattern_is_dropped(self) -> None:
        normalized = diagnosis_pack(pack_of(log_rule("a", pattern="([unclosed")))

        self.assertEqual(normalized["entries"], [])

    def test_only_known_log_roles_are_kept(self) -> None:
        entry = log_rule("a")
        entry["match"]["sources"] = ["loader_log", "no_such_role"]

        sources = diagnosis_pack(pack_of(entry))["entries"][0]["match"]["sources"]
        self.assertEqual(sources, ["loader_log"])

    def test_a_pattern_with_no_known_role_is_dropped(self) -> None:
        entry = log_rule("a")
        entry["match"]["sources"] = ["no_such_role"]

        self.assertEqual(diagnosis_pack(pack_of(entry))["entries"], [])

    def test_an_unknown_bucket_drops_the_rule(self) -> None:
        self.assertEqual(diagnosis_pack(pack_of(log_rule("a", bucket="maybe")))["entries"], [])

    def test_a_title_without_text_drops_the_rule(self) -> None:
        entry = log_rule("a")
        entry["title"] = {"zh": "   "}

        self.assertEqual(diagnosis_pack(pack_of(entry))["entries"], [])

    def test_an_out_of_range_level_falls_back_to_the_default(self) -> None:
        entry = diagnosis_pack(pack_of(log_rule("a", level=99)))["entries"][0]

        self.assertEqual(entry["level"], 3)

    def test_a_go_to_page_outside_the_closed_set_is_dropped(self) -> None:
        entry = diagnosis_pack(pack_of(log_rule("a", go_to={"page": "https://evil.example"})))["entries"][0]

        self.assertEqual(entry["go_to"], {})

    def test_a_repeated_rule_id_keeps_the_first_entry(self) -> None:
        normalized = diagnosis_pack(pack_of(log_rule("a", pattern="first"), log_rule("a", pattern="second")))

        self.assertEqual(len(normalized["entries"]), 1)
        self.assertEqual(normalized["entries"][0]["match"]["pattern"], "first")

    def test_a_payload_that_is_not_a_pack_normalizes_to_an_empty_one(self) -> None:
        for payload in (None, [], "x", {"entries": "x"}, {"entries": [1, "a", None]}):
            with self.subTest(payload=payload):
                self.assertEqual(diagnosis_pack(payload)["entries"], [])

    def test_the_check_vocabulary_and_log_roles_are_non_empty(self) -> None:
        self.assertIn("environment_conflict", CHECKS)
        self.assertIn("unity_log", LOG_ROLES)


class EvaluateTests(unittest.TestCase):
    def test_required_comes_first_and_level_orders_within_a_bucket(self) -> None:
        pack = pack_of(
            log_rule("optional-low", bucket=BUCKET_OPTIONAL, level=5),
            log_rule("required-slow", bucket=BUCKET_REQUIRED, level=4),
            log_rule("required-first", bucket=BUCKET_REQUIRED, level=1),
            log_rule("optional-high", bucket=BUCKET_OPTIONAL, level=1),
        )
        findings, unjudged = evaluate({}, lines_of("boom"), pack, available_roles=["loader_log"])

        self.assertEqual(unjudged, [])
        self.assertEqual(
            [finding["rule"] for finding in findings],
            ["required-first", "required-slow", "optional-high", "optional-low"],
        )

    def test_split_buckets_keeps_the_sorted_order_inside_each_one(self) -> None:
        pack = pack_of(
            log_rule("r2", bucket=BUCKET_REQUIRED, level=2),
            log_rule("r1", bucket=BUCKET_REQUIRED, level=1),
            log_rule("o1", bucket=BUCKET_OPTIONAL, level=1),
        )
        findings, _ = evaluate({}, lines_of("boom"), pack, available_roles=["loader_log"])

        buckets = split_buckets(findings)
        self.assertEqual([item["rule"] for item in buckets[BUCKET_REQUIRED]], ["r1", "r2"])
        self.assertEqual([item["rule"] for item in buckets[BUCKET_OPTIONAL]], ["o1"])

    def test_a_signature_reports_how_many_lines_it_hit_and_quotes_the_first(self) -> None:
        pack = pack_of(log_rule("a"))
        lines = lines_of("fine", "boom one", "fine", "boom two")

        findings, _ = evaluate({}, lines, pack, available_roles=["loader_log"])

        self.assertEqual(findings[0]["params"]["count"], "2")
        self.assertEqual(findings[0]["log_line"]["number"], 2)
        self.assertEqual(findings[0]["log_line"]["text"], "boom one")

    def test_a_signature_stays_quiet_below_its_minimum_hit_count(self) -> None:
        entry = log_rule("a")
        entry["match"]["min_count"] = 2
        findings, _ = evaluate({}, lines_of("boom"), pack_of(entry), available_roles=["loader_log"])

        self.assertEqual(findings, [])

    def test_a_signature_only_reads_the_roles_it_asked_for(self) -> None:
        pack = pack_of(log_rule("a"))
        findings, _ = evaluate(
            {}, lines_of("boom", role="unity_log"), pack, available_roles=["loader_log", "unity_log"]
        )

        self.assertEqual(findings, [])

    def test_capture_groups_become_params(self) -> None:
        pack = pack_of(log_rule("a", pattern="missing '([^']+)' for (.+)"))
        lines = lines_of("missing 'Foo.dll' for Bar")

        findings, _ = evaluate({}, lines, pack, available_roles=["loader_log"])

        self.assertEqual(findings[0]["params"]["group1"], "Foo.dll")
        self.assertEqual(findings[0]["params"]["group2"], "Bar")

    def test_a_go_to_without_a_package_picks_up_the_one_the_check_named(self) -> None:
        entry = state_rule("a", check="loader_files_broken", go_to={"page": "installed"})
        facts = {
            "game_configured": True,
            "packages_broken": [{"id": "lavagang.melonloader", "name": "MelonLoader", "is_loader": True}],
        }

        findings, _ = evaluate(facts, [], pack_of(entry))

        self.assertEqual(findings[0]["go_to"], {"page": "installed", "package": "lavagang.melonloader"})

    def test_a_go_to_the_pack_pinned_itself_wins_over_the_check(self) -> None:
        entry = state_rule(
            "a", check="loader_files_broken", go_to={"page": "modloaders", "package": "pinned"}
        )
        facts = {
            "game_configured": True,
            "packages_broken": [{"id": "other", "is_loader": True}],
        }

        findings, _ = evaluate(facts, [], pack_of(entry))

        self.assertEqual(findings[0]["go_to"]["package"], "pinned")

    def test_a_state_rule_is_unjudged_when_no_game_directory_is_configured(self) -> None:
        _, unjudged = evaluate({}, [], pack_of(state_rule("a")))

        self.assertEqual(unjudged, [{"rule": "a", "reason": UNJUDGED_GAME_NOT_CONFIGURED}])

    def test_a_signature_is_unjudged_when_its_log_is_absent(self) -> None:
        _, unjudged = evaluate({}, [], pack_of(log_rule("a")), available_roles=["unity_log"])

        self.assertEqual(unjudged, [{"rule": "a", "reason": UNJUDGED_LOG_MISSING}])

    def test_a_missing_pack_judges_nothing_and_reports_nothing(self) -> None:
        findings, unjudged = evaluate({}, [], diagnosis_pack(None))

        self.assertEqual((findings, unjudged), ([], []))

    def test_a_spent_budget_leaves_the_remaining_rules_unjudged(self) -> None:
        pack = pack_of(log_rule("a"), log_rule("b"))

        findings, unjudged = evaluate(
            {},
            lines_of("boom"),
            pack,
            available_roles=["loader_log"],
            deadline=time.monotonic() - 1,
        )

        self.assertEqual(findings, [])
        self.assertEqual(
            unjudged,
            [
                {"rule": "a", "reason": UNJUDGED_TIMEOUT},
                {"rule": "b", "reason": UNJUDGED_TIMEOUT},
            ],
        )


class StateCheckTests(unittest.TestCase):
    """四条检查各自的触发条件 —— 不满足就一条都不出。"""

    def _findings(self, entry: dict, facts: dict) -> list[dict]:
        findings, _ = evaluate({**{"game_configured": True}, **facts}, [], pack_of(entry))
        return findings

    def test_environment_conflict_fires_and_names_the_loader_it_points_at(self) -> None:
        entry = state_rule("a", check="environment_conflict", bucket=BUCKET_REQUIRED, level=1)
        findings = self._findings(
            entry,
            {
                "game_version": "0.2.55.5",
                "environment_conflict": {"conflict": True, "loader": "lavagang.melonloader"},
            },
        )

        self.assertEqual(findings[0]["params"]["loader"], "lavagang.melonloader")
        self.assertEqual(findings[0]["params"]["package"], "lavagang.melonloader")
        self.assertEqual(findings[0]["params"]["sprocket"], "0.2.55.5")

    def test_environment_conflict_shows_the_loaders_readable_name_but_links_by_id(self) -> None:
        entry = state_rule("a", check="environment_conflict", go_to={"page": "modloaders"})
        findings = self._findings(
            entry,
            {
                "environment_conflict": {
                    "conflict": True,
                    "loader": "lavagang.melonloader",
                    "loader_name": "MelonLoader",
                }
            },
        )

        self.assertEqual(findings[0]["params"]["loader"], "MelonLoader")
        self.assertEqual(findings[0]["go_to"]["package"], "lavagang.melonloader")

    def test_a_consistent_environment_says_nothing(self) -> None:
        entry = state_rule("a", check="environment_conflict")

        self.assertEqual(self._findings(entry, {"environment_conflict": {"conflict": False}}), [])

    def test_a_broken_loader_and_a_broken_mod_are_told_apart(self) -> None:
        facts = {
            "packages_broken": [
                {"id": "some.mod", "name": "Mod", "is_loader": False},
                {"id": "some.loader", "name": "Loader", "is_loader": True},
            ]
        }

        loaders = self._findings(state_rule("a", check="loader_files_broken"), facts)
        mods = self._findings(state_rule("b", check="mod_files_broken"), facts)

        self.assertEqual([item["params"]["package"] for item in loaders], ["some.loader"])
        self.assertEqual([item["params"]["package"] for item in mods], ["some.mod"])

    def test_an_intact_install_says_nothing(self) -> None:
        self.assertEqual(self._findings(state_rule("a", check="mod_files_broken"), {}), [])

    def test_a_game_without_any_loader_is_reported(self) -> None:
        findings = self._findings(
            state_rule("a", check="loader_missing", bucket=BUCKET_OPTIONAL),
            {"game_version": "0.2.55.5", "loaders_available": True, "loaders_installed": []},
        )

        self.assertEqual(findings[0]["params"]["sprocket"], "0.2.55.5")

    def test_a_registry_without_any_loader_package_says_nothing(self) -> None:
        entry = state_rule("a", check="loader_missing")

        self.assertEqual(self._findings(entry, {"loaders_available": False}), [])

    def test_a_loader_that_is_installed_says_nothing(self) -> None:
        entry = state_rule("a", check="loader_missing")

        self.assertEqual(
            self._findings(entry, {"loaders_available": True, "loaders_installed": ["x"]}), []
        )


if __name__ == "__main__":
    unittest.main()
