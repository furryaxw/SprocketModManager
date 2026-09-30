"""「错误修复」页的渲染：两栏结论、证据与步骤、以及跳转按钮交回去的目标。"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "fixtures" / "client_ui" / "render_diagnosis_harness.js"
CLIENT_UI = Path(__file__).resolve().parent.parent / "sprocket_mod_manager" / "presentation" / "client_ui"
NODE = shutil.which("node")

ICALL_LINE = (
    "[07:55:36.342] [WARNING] [UnityExplorer] [UniverseLib] "
    "Could not find any iCall with the signature 'UnityEngine.AssetBundle::LoadAsset_Internal_Injected'!"
)


def finding(**overrides) -> dict:
    base = {
        "rule": "test-rule",
        "bucket": "optional",
        "level": 3,
        "title": {"zh": "标题", "en": "Title"},
        "explain": {"zh": "为什么会这样", "en": "Why it happens"},
        "evidence": {"zh": "命中 {count} 行", "en": "Matched {count} line(s)"},
        "tutorial": {"zh": ["第一步", "第二步"], "en": ["Step one", "Step two"]},
        "params": {"count": "2"},
        "labels": {},
        "log_line": None,
        "go_to": {},
    }
    base.update(overrides)
    return base


def log_finding(**overrides) -> dict:
    return finding(
        bucket="required",
        level=1,
        title={"zh": "某个模组挂了", "en": "A mod broke"},
        log_line={"source": "MelonLoader/Latest.log", "number": 186, "text": ICALL_LINE},
        **overrides,
    )


def report(**overrides) -> dict:
    base = {
        "generated_at": "2026-09-29T08:00:00Z",
        "pack_version": 3,
        "pack_source": "registry",
        "rules_total": 7,
        "required": [],
        "optional": [],
        "unjudged": [],
        "sources": [],
        "lines_read": 12,
        "elapsed_ms": 5,
    }
    base.update(overrides)
    return base


@unittest.skipIf(NODE is None, "node is not available")
class DiagnosisRenderHarnessTests(unittest.TestCase):
    def _render(self, **payload) -> dict:
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
            self.assertEqual(
                completed.returncode, 0, (completed.stdout or "") + (completed.stderr or "")
            )
            return json.loads(completed.stdout or "{}")

    def test_the_two_buckets_are_drawn_in_order_with_their_counts(self) -> None:
        result = self._render(
            language="zh",
            report=report(
                required=[log_finding()],
                optional=[finding(level=5), finding(rule="second", level=2)],
            ),
        )

        self.assertIsNone(result["error"])
        sections = result["report"]["sections"]
        self.assertEqual([section["bucket"] for section in sections], ["required", "optional"])
        self.assertEqual(sections[0]["heading"], "必须解决 (1)")
        self.assertEqual(sections[1]["heading"], "非必要 (2)")
        self.assertEqual([card["level"] for card in sections[1]["cards"]], ["L5", "L2"])

    def test_an_empty_bucket_says_so_instead_of_showing_nothing(self) -> None:
        result = self._render(language="zh", report=report(required=[]))

        required, optional = result["report"]["sections"]
        self.assertEqual(required["empty"], "没有阻止游戏启动的问题")
        self.assertEqual(required["cards"], [])
        self.assertEqual(optional["empty"], "没有其他发现")

    def test_a_log_finding_quotes_the_line_and_lays_out_the_steps(self) -> None:
        result = self._render(language="zh", report=report(required=[log_finding()]))

        card = result["report"]["sections"][0]["cards"][0]
        self.assertEqual(card["facts"][0]["label"], "证据")
        self.assertEqual(card["facts"][0]["value"], "MelonLoader/Latest.log:186")
        self.assertEqual(card["facts"][0]["quote"], ICALL_LINE)
        self.assertEqual(card["facts"][1]["label"], "原因")
        self.assertEqual(card["facts"][1]["value"], "为什么会这样")
        self.assertEqual(card["steps"], ["第一步", "第二步"])

    def test_a_state_finding_uses_its_evidence_template(self) -> None:
        entry = finding(evidence={"zh": "{name} 里有 {count} 个文件对不上", "en": "x"}, params={"name": "MelonLoader", "count": "3"})

        result = self._render(language="zh", report=report(optional=[entry]))

        value = result["report"]["sections"][1]["cards"][0]["facts"][0]["value"]
        self.assertEqual(value, "MelonLoader 里有 3 个文件对不上")

    def test_a_localized_label_wins_over_the_plain_parameter(self) -> None:
        entry = finding(
            title={"zh": "「{name}」有问题", "en": "'{name}' is broken"},
            params={"name": "lavagang.melonloader"},
            labels={"name": {"zh": "MelonLoader", "en": "MelonLoader"}},
        )

        result = self._render(language="zh", report=report(optional=[entry]))

        self.assertEqual(result["report"]["sections"][1]["cards"][0]["title"], "「MelonLoader」有问题")

    def test_without_a_label_the_plain_parameter_fills_the_placeholder(self) -> None:
        entry = finding(title={"zh": "「{name}」有问题", "en": "x"}, params={"name": "raw-name"})

        result = self._render(language="zh", report=report(optional=[entry]))

        self.assertEqual(result["report"]["sections"][1]["cards"][0]["title"], "「raw-name」有问题")

    def test_a_placeholder_with_nothing_behind_it_stays_visible(self) -> None:
        """填不上就原样留着：宁可露出 `{name}`，也别让一句话里凭空少一个词。"""
        entry = finding(title={"zh": "「{name}」有问题", "en": "x"}, params={})

        result = self._render(language="zh", report=report(optional=[entry]))

        self.assertEqual(result["report"]["sections"][1]["cards"][0]["title"], "「{name}」有问题")

    def test_the_unjudged_line_names_each_reason_once(self) -> None:
        result = self._render(
            language="zh",
            report=report(
                unjudged=[
                    {"rule": "a", "reason": "log_missing"},
                    {"rule": "b", "reason": "log_missing"},
                    {"rule": "c", "reason": "timeout"},
                ]
            ),
        )

        self.assertEqual(
            result["report"]["unjudged"], "未判定 3 条：这项检查要的日志不在 · 时间预算用完，没跑完"
        )

    def test_a_missing_pack_is_said_out_loud(self) -> None:
        result = self._render(
            language="zh",
            report=report(
                pack_source="missing",
                pack_version=0,
                rules_total=0,
                unjudged=[{"rule": "", "reason": "pack_missing"}],
            ),
        )

        self.assertEqual(result["meta"], "没有规则包，这次没有做任何判定。")
        self.assertEqual(result["report"]["unjudged"], "未判定 1 条：没有规则包")

    def test_the_pack_version_travels_with_the_report(self) -> None:
        result = self._render(report=report(pack_version=9))

        self.assertIn("9", result["meta"])
        self.assertIn("Rule pack", result["meta"])

    def test_before_any_diagnosis_the_page_says_so(self) -> None:
        result = self._render(language="zh", run=False, report=None)

        self.assertEqual(result["report"]["empty"], "还没有诊断过。")
        self.assertEqual(result["report"]["sections"], [])
        self.assertEqual(result["meta"], "")
        self.assertIsNone(result["report"]["unjudged"])

    def test_a_running_scan_says_so_and_holds_the_hint_back(self) -> None:
        result = self._render(language="zh", report=report(running=True, required=[log_finding()]))

        self.assertEqual(result["report"]["running"], "正在读日志…")
        self.assertIsNone(result["report"]["hint"], "还在扫就不该说「没有查出已知问题」")

    def test_nothing_found_points_at_the_log_upload(self) -> None:
        result = self._render(language="zh", report=report())

        self.assertEqual(
            result["report"]["hint"], "没有查出已知问题？把日志传上去，让维护者看看。"
        )

    def test_findings_do_not_show_the_upload_hint(self) -> None:
        result = self._render(language="zh", report=report(optional=[finding()]))

        self.assertIsNone(result["report"]["hint"])

    def test_the_go_button_hands_over_an_in_app_target(self) -> None:
        entry = finding(go_to={"page": "installed", "package": "lavagang.melonloader"})

        result = self._render(language="zh", report=report(optional=[entry]), click_go=0)

        self.assertEqual(result["goTargets"], [{"page": "installed", "package": "lavagang.melonloader"}])
        self.assertEqual(result["report"]["sections"][1]["cards"][0]["go"], "去处理")

    def test_a_finding_without_a_target_offers_no_button(self) -> None:
        result = self._render(language="zh", report=report(optional=[finding()]))

        self.assertIsNone(result["report"]["sections"][1]["cards"][0]["go"])

    def test_the_english_rendering_uses_the_english_text(self) -> None:
        result = self._render(language="en", report=report(required=[log_finding()]))

        sections = result["report"]["sections"]
        self.assertEqual(sections[0]["heading"], "Must fix (1)")
        self.assertEqual(sections[0]["cards"][0]["title"], "A mod broke")
        self.assertEqual(sections[0]["cards"][0]["facts"][0]["label"], "Evidence")

    def test_a_failed_run_keeps_the_previous_report_and_says_why(self) -> None:
        result = self._render(
            language="zh",
            run_ok=False,
            preload_report=report(optional=[finding()]),
        )

        self.assertEqual(result["report"]["sections"][1]["cards"][0]["title"], "标题")
        self.assertTrue(any("boom" in toast for toast in result["toasts"]), result["toasts"])
        self.assertEqual(result["buttonLabel"], "诊断游戏错误", "按钮文字要还回去")
        self.assertFalse(result["buttonDisabled"])

    def test_a_successful_run_calls_the_api_once_and_restores_the_button(self) -> None:
        result = self._render(language="zh", report=report(optional=[finding()]))

        self.assertEqual(result["apiCalls"], [{"kind": "call", "args": ["run_diagnosis"]}])
        self.assertEqual(result["buttonLabel"], "诊断游戏错误")
        self.assertFalse(result["buttonDisabled"])

    def test_the_button_says_it_is_working_while_the_run_is_in_flight(self) -> None:
        result = self._render(language="zh", report=report(optional=[finding()]))

        self.assertEqual(result["duringLabel"], "正在读日志…")
        self.assertTrue(result["duringDisabled"])


if __name__ == "__main__":
    unittest.main()
