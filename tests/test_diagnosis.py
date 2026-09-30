"""诊断规则包：规范化扛得住畸形输入，求值只认本机事实，命中按两级排好。"""

from __future__ import annotations

import json
import unittest

from sprocket_mod_manager.domain.diagnosis import (
    BLOCK_CHARS,
    BUCKET_OPTIONAL,
    BUCKET_REQUIRED,
    CHECKS,
    EVIDENCE_CHARS,
    LOG_ROLES,
    RULE_BUDGET_SECONDS,
    UNJUDGED_GAME_NOT_CONFIGURED,
    UNJUDGED_LOG_MISSING,
    UNJUDGED_TIMEOUT,
    diagnosis_pack,
    log_hits,
    log_signatures,
    log_unjudged,
    sort_findings,
    split_buckets,
    state_findings,
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


def judge(facts, lines, pack, *, available_roles=()):
    """把行喂进签名再取结论：与生产那条路走的是同一组原语。"""
    normalized = diagnosis_pack(pack)
    hits, unjudged = state_findings(facts or {}, normalized)
    signatures = log_signatures(normalized)
    for line in lines or ():
        for signature in signatures:
            signature.feed(line)
    hits.extend(log_hits(signatures))
    unjudged.extend(
        log_unjudged(
            signatures,
            available_roles=available_roles,
            timed_out={signature.rule_id for signature in signatures if signature.abandoned},
        )
    )
    return sort_findings(hits), unjudged


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
        findings, unjudged = judge({}, lines_of("boom"), pack, available_roles=["loader_log"])

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
        findings, _ = judge({}, lines_of("boom"), pack, available_roles=["loader_log"])

        buckets = split_buckets(findings)
        self.assertEqual([item["rule"] for item in buckets[BUCKET_REQUIRED]], ["r1", "r2"])
        self.assertEqual([item["rule"] for item in buckets[BUCKET_OPTIONAL]], ["o1"])

    def test_a_signature_reports_how_many_lines_it_hit_and_quotes_the_first(self) -> None:
        pack = pack_of(log_rule("a"))
        lines = lines_of("fine", "boom one", "fine", "boom two")

        findings, _ = judge({}, lines, pack, available_roles=["loader_log"])

        self.assertEqual(findings[0]["params"]["count"], "2")
        self.assertEqual(findings[0]["log_line"]["number"], 2)
        self.assertEqual(findings[0]["log_line"]["text"], "boom one")

    def test_a_signature_stays_quiet_below_its_minimum_hit_count(self) -> None:
        entry = log_rule("a")
        entry["match"]["min_count"] = 2
        findings, _ = judge({}, lines_of("boom"), pack_of(entry), available_roles=["loader_log"])

        self.assertEqual(findings, [])

    def test_a_signature_only_reads_the_roles_it_asked_for(self) -> None:
        pack = pack_of(log_rule("a"))
        findings, _ = judge(
            {}, lines_of("boom", role="unity_log"), pack, available_roles=["loader_log", "unity_log"]
        )

        self.assertEqual(findings, [])

    def test_capture_groups_become_params(self) -> None:
        pack = pack_of(log_rule("a", pattern="missing '([^']+)' for (.+)"))
        lines = lines_of("missing 'Foo.dll' for Bar")

        findings, _ = judge({}, lines, pack, available_roles=["loader_log"])

        self.assertEqual(findings[0]["params"]["group1"], "Foo.dll")
        self.assertEqual(findings[0]["params"]["group2"], "Bar")

    def test_a_go_to_without_a_package_picks_up_the_one_the_check_named(self) -> None:
        entry = state_rule("a", check="loader_files_broken", go_to={"page": "installed"})
        facts = {
            "game_configured": True,
            "packages_broken": [{"id": "lavagang.melonloader", "name": "MelonLoader", "is_loader": True}],
        }

        findings, _ = judge(facts, [], pack_of(entry))

        self.assertEqual(findings[0]["go_to"], {"page": "installed", "package": "lavagang.melonloader"})

    def test_a_go_to_the_pack_pinned_itself_wins_over_the_check(self) -> None:
        entry = state_rule(
            "a", check="loader_files_broken", go_to={"page": "modloaders", "package": "pinned"}
        )
        facts = {
            "game_configured": True,
            "packages_broken": [{"id": "other", "is_loader": True}],
        }

        findings, _ = judge(facts, [], pack_of(entry))

        self.assertEqual(findings[0]["go_to"]["package"], "pinned")

    def test_a_state_rule_is_unjudged_when_no_game_directory_is_configured(self) -> None:
        _, unjudged = judge({}, [], pack_of(state_rule("a")))

        self.assertEqual(unjudged, [{"rule": "a", "reason": UNJUDGED_GAME_NOT_CONFIGURED}])

    def test_a_signature_is_unjudged_when_its_log_is_absent(self) -> None:
        _, unjudged = judge({}, [], pack_of(log_rule("a")), available_roles=["unity_log"])

        self.assertEqual(unjudged, [{"rule": "a", "reason": UNJUDGED_LOG_MISSING}])

    def test_a_missing_pack_judges_nothing_and_reports_nothing(self) -> None:
        findings, unjudged = judge({}, [], diagnosis_pack(None))

        self.assertEqual((findings, unjudged), ([], []))

    def test_a_signature_that_used_up_its_gate_stops_scanning(self) -> None:
        """闸门吃满的那一条不再往下扫，收尾按「没跑完」报 —— 单条规则拖不垮整次诊断。"""
        signature = log_signatures(diagnosis_pack(pack_of(log_rule("a"))))[0]
        signature.cost = RULE_BUDGET_SECONDS + 1
        signature.feed({"role": "loader_log", "source": "s", "number": 1, "text": "boom"})

        self.assertTrue(signature.abandoned)
        self.assertEqual(signature.count, 0, "闸门吃满之后不该再匹配")
        self.assertIsNone(signature.finding())
        self.assertEqual(
            log_unjudged([signature], available_roles=["loader_log"], timed_out={"a"}),
            [{"rule": "a", "reason": UNJUDGED_TIMEOUT}],
        )


class StateCheckTests(unittest.TestCase):
    """四条检查各自的触发条件 —— 不满足就一条都不出。"""

    def _findings(self, entry: dict, facts: dict) -> list[dict]:
        findings, _ = judge({**{"game_configured": True}, **facts}, [], pack_of(entry))
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


class BlockWindowTests(unittest.TestCase):
    """行内滑窗：长行切成有重叠的块，跨块的命中割不断；一行里命中几处仍然只算一行。"""

    def test_a_hit_spanning_a_block_boundary_is_still_found(self) -> None:
        line = "x" * (BLOCK_CHARS - 2) + "boom"

        findings, _ = judge({}, lines_of(line), pack_of(log_rule("a")))

        self.assertEqual(len(findings), 1, "跨过块边界的那条命中必须还在")

    def test_a_hit_at_the_end_of_a_long_line_is_still_found(self) -> None:
        line = "x" * (BLOCK_CHARS * 3) + "boom"

        findings, _ = judge({}, lines_of(line), pack_of(log_rule("a")))

        self.assertEqual(len(findings), 1)

    def test_several_hits_in_one_line_count_as_one(self) -> None:
        findings, _ = judge({}, lines_of("boom and boom"), pack_of(log_rule("a")))

        self.assertEqual(findings[0]["params"]["count"], "1", "计数是行数，不是命中处数")

    def test_the_quoted_line_is_capped_for_the_report(self) -> None:
        findings, _ = judge({}, lines_of("boom " + "y" * EVIDENCE_CHARS), pack_of(log_rule("a")))

        self.assertEqual(len(findings[0]["log_line"]["text"]), EVIDENCE_CHARS)


if __name__ == "__main__":
    unittest.main()
