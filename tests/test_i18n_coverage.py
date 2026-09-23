"""显示文案必须走 i18n：HTML 带 `data-i18n`、JS 不写死中文，且两种语言都有那个键。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

CLIENT_UI = Path(__file__).resolve().parent.parent / "sprocket_mod_manager" / "presentation" / "client_ui"
NODE = shutil.which("node")

CJK = re.compile(r"[\u4e00-\u9fff]")

# 只属于元素自身的键：`data-i18n` 管文本，另外两个管 placeholder / aria。
TEXT_KEYS = {"data-i18n"}
OTHER_KEYS = {"data-i18n-placeholder", "data-i18n-aria"}

# 语言选项用它自己的语言写（中文永远写「中文」，英文永远写 English），站点域名运行时会被覆盖。
ALLOWED_HTML_TEXT = {"中文"}
ALLOWED_HTML_IDS = {"page-subtitle"}
# 读取中的省略号：语言无关。
ALLOWED_JS_LITERALS = {"..."}
# 品牌名与常量不是文案。
BRAND = re.compile(
    r"^(Sprocket|MelonLoader|GitHub|Registry|Mod Manager|Sprocket Mod Manager|"
    r"\.dll|\.zip|DLL|ZIP|AGPL|https?://|\d)",
)


class _TextFinder(HTMLParser):
    """找出「可见文字」，同时记住它有没有被 data-i18n 覆盖。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, str]] = []
        self.gaps: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        values = dict(attrs)
        covered = any(key in values for key in TEXT_KEYS)
        self.stack.append((tag, covered, values.get("id", "")))

    def handle_endtag(self, tag) -> None:
        if self.stack:
            self.stack.pop()

    def handle_data(self, data) -> None:
        text = data.strip()
        if not text or not CJK.search(text):
            return
        if text in ALLOWED_HTML_TEXT:
            return
        if any(tag in {"script", "style"} for tag, _covered, _id in self.stack):
            return
        if any(covered for _tag, covered, _id in self.stack):
            return
        if any(identifier in ALLOWED_HTML_IDS for _tag, _covered, identifier in self.stack):
            return
        self.gaps.append(text)


def html_gaps() -> list[str]:
    finder = _TextFinder()
    finder.feed((CLIENT_UI / "index.html").read_text(encoding="utf-8"))
    return finder.gaps


def strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return "\n".join(re.sub(r"//.*$", "", line) for line in source.splitlines())


def js_cjk_literals() -> list[str]:
    """JS 里的中文字面量（注释不算）：文案该走 `tr(...)`。"""
    literal = re.compile(r"`(?:\\.|[^`\\])*`|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")
    found = []
    for path in sorted((CLIENT_UI / "js").glob("*.js")):
        if path.name == "i18n.js":  # 翻译表本身就是中文
            continue
        for number, line in enumerate(strip_comments(path.read_text(encoding="utf-8")).splitlines(), start=1):
            for match in literal.finditer(line):
                text = match.group()
                if CJK.search(text) and text not in ALLOWED_JS_LITERALS:
                    found.append(f"{path.name}:{number}: {text[:60]}")
    return found


def referenced_keys() -> set[str]:
    """HTML 与 JS 里被引用的键（字符串字面量形式）。"""
    keys: set[str] = set()
    html = (CLIENT_UI / "index.html").read_text(encoding="utf-8")
    for key in TEXT_KEYS | OTHER_KEYS:
        keys.update(re.findall(rf'{key}="([A-Za-z0-9_]+)"', html))
    pattern = re.compile(
        r'tr\(\s*"([A-Za-z0-9_]+)"'
        r'|(?:kicker|title|confirmText|cancelText|body):\s*"([A-Za-z0-9_]+)"'
    )
    for path in sorted((CLIENT_UI / "js").glob("*.js")):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            keys.add(match.group(1) or match.group(2))
    return keys


def translation_keys() -> dict[str, set[str]]:
    """i18n.js 里两张表的键：交给 node 求值，别在 Python 里重写一份解析。"""
    script = (
        "const vm=require('vm'),fs=require('fs');"
        f"const src=fs.readFileSync({json.dumps(str(CLIENT_UI / 'js' / 'i18n.js'))},'utf8');"
        'const TEXT=vm.runInNewContext(src+"\\n;TEXT;",{});'
        "process.stdout.write(JSON.stringify({zh:Object.keys(TEXT.zh),en:Object.keys(TEXT.en)}));"
    )
    completed = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    payload = json.loads(completed.stdout)
    return {"zh": set(payload["zh"]), "en": set(payload["en"])}


@unittest.skipIf(NODE is None, "node is not available")
class I18nCoverageTests(unittest.TestCase):
    def test_every_visible_text_goes_through_i18n(self) -> None:
        gaps = html_gaps() + js_cjk_literals()

        self.assertEqual(gaps, [], "这些文案没走 i18n（HTML 加 data-i18n / JS 用 tr(...)）")

    def test_referenced_keys_exist_in_both_languages(self) -> None:
        keys = translation_keys()

        self.assertEqual(
            sorted(referenced_keys() - keys["zh"] - keys["en"]),
            [],
            "引用了不存在的 i18n 键",
        )

    def test_no_key_is_missing_a_language(self) -> None:
        keys = translation_keys()

        self.assertEqual(sorted(keys["zh"] - keys["en"]), [], "zh 有、en 缺")
        self.assertEqual(sorted(keys["en"] - keys["zh"]), [], "en 有、zh 缺")

    def test_every_backend_error_code_has_a_translated_headline(self) -> None:
        """后端只给 `code` + 原文；界面按 code 查文案，否则英文界面里会冒出中文原文。"""
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((CLIENT_UI.parent / "controllers").glob("*.py"))
        )
        codes = set(re.findall(r'code="([a-z_]+)"', sources))
        # 安装控制器里写成 `else "<code>"` 的那批：都以 `_failed` 结尾（同一行里还有别的字符串）。
        codes.update(re.findall(r'else "([a-z_]+_failed)"', sources))
        keys = translation_keys()
        missing = sorted(f"error_{code}" for code in codes if f"error_{code}" not in keys["zh"])

        self.assertEqual(missing, [], "这些错误码还没有界面文案")

    def test_the_incompatible_update_sentence_is_composed_per_axis(self) -> None:
        """缺哪个轴就不提哪个轴：一句整串会把未知的轴也写进去（「Sprocket - 和 MelonLoader 未知」）。"""
        installed = (CLIENT_UI / "js" / "installs.js").read_text(encoding="utf-8")

        self.assertIn('tr("incompatibleUpdateHead", {version})', installed)
        self.assertIn('parts.join(tr("environmentAxisAnd"))', installed)
        self.assertIn('tr("environmentAxisSprocket", {version: sprocket})', installed)
        self.assertIn('tr("environmentAxisMelonLoader", {version: loader})', installed)
        self.assertIn('tr("incompatibleUpdateUnknown", {version})', installed)

        fragments = self._fragments(
            [
                "incompatibleUpdateHead",
                "incompatibleUpdateUnknown",
                "environmentAxisSprocket",
                "environmentAxisMelonLoader",
                "environmentAxisAnd",
            ]
        )
        head = fragments["en"]["incompatibleUpdateHead"].replace("{version}", "1.1.0")
        parts = [
            fragments["en"]["environmentAxisSprocket"].replace("{version}", "0.2.53.2"),
            fragments["en"]["environmentAxisMelonLoader"].replace("{version}", "0.7.3"),
        ]
        both = head + fragments["en"]["environmentAxisAnd"].join(parts)
        self.assertEqual(
            both,
            "Update to 1.1.0 is available, but it does not support your "
            "Sprocket 0.2.53.2 and MelonLoader 0.7.3",
        )
        only_sprocket = head + parts[0]
        self.assertNotIn("MelonLoader", only_sprocket, "缺的那一轴不提")
        self.assertIn("（", fragments["zh"]["incompatibleUpdateHead"], "中文模板保留全角括号")

    def _fragments(self, keys: list[str]) -> dict[str, dict[str, str]]:
        script = (
            "const vm=require('vm'),fs=require('fs');"
            f"const src=fs.readFileSync({json.dumps(str(CLIENT_UI / 'js' / 'i18n.js'))},'utf8');"
            'const TEXT=vm.runInNewContext(src+"\\n;TEXT;",{});'
            f"const keys={json.dumps(keys)};"
            "process.stdout.write(JSON.stringify({zh:Object.fromEntries(keys.map((k)=>[k,TEXT.zh[k]])),"
            "en:Object.fromEntries(keys.map((k)=>[k,TEXT.en[k]]))}));"
        )
        completed = subprocess.run(
            [NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)
        return json.loads(completed.stdout)

    def test_section_kickers_are_translated(self) -> None:
        """设置页那几个英文大写小标题曾经是写死的，钉住它们。"""
        html = (CLIENT_UI / "index.html").read_text(encoding="utf-8")

        for key in (
            "kickerAccessibility",
            "kickerDiagnostics",
            "kickerModRuntime",
            "kickerIndexSource",
            "kickerNetwork",
            "registrySection",
        ):
            self.assertIn(f'data-i18n="{key}"', html)
        for literal in ("<small>ACCESSIBILITY</small>", "<small>MOD RUNTIME</small>", "<dt>Registry</dt>"):
            self.assertNotIn(literal, html)


if __name__ == "__main__":
    unittest.main()
