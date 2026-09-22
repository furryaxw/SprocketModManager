"""i18n 表的结构契约：键不重复、zh 与 en 一一对应。

`TEXT` 是普通对象字面量，**重复键会被 JS 静默吞掉**（后一个胜出），所以只能做文本解析：
把 `zh: {…}` / `en: {…}` 两个块按花括号配对切出来，再逐行抓键。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

I18N = Path(__file__).resolve().parents[1] / "sprocket_mod_manager" / "presentation" / "client_ui" / "js" / "i18n.js"
LANGUAGES = ("zh", "en")
_KEY = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:", re.MULTILINE)


def _block(text: str, language: str) -> str:
    """切出某个语言的对象字面量（花括号配对；表里没有嵌套对象）。"""
    match = re.search(rf"\n\s*{language}:\s*\{{", text)
    if not match:
        raise AssertionError(f"i18n.js has no '{language}' block")
    start = text.index("{", match.start())
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start:index]
    raise AssertionError(f"i18n.js '{language}' block is not closed")


def keys_of(text: str, language: str) -> list[str]:
    return _KEY.findall(_block(text, language))


class I18nKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = I18N.read_text(encoding="utf-8")

    def test_no_duplicate_keys(self) -> None:
        for language in LANGUAGES:
            keys = keys_of(self.text, language)
            duplicates = sorted({key for key in keys if keys.count(key) > 1})
            self.assertEqual(duplicates, [], f"{language} has duplicate i18n keys: {duplicates}")

    def test_both_languages_define_the_same_keys(self) -> None:
        zh = set(keys_of(self.text, "zh"))
        en = set(keys_of(self.text, "en"))
        self.assertEqual(sorted(en - zh), [], "keys missing from zh")
        self.assertEqual(sorted(zh - en), [], "keys missing from en")

    def test_not_installed_and_missing_are_distinct_keys(self) -> None:
        """MelonLoader 的「未安装」与依赖缺口的「缺失」必须是两个键，否则会被 JS 静默覆盖。"""
        assert "notInstalled" in keys_of(self.text, "zh")

    def test_every_literal_tr_key_exists(self) -> None:
        """界面里写死的 `tr("key")` 必须在表里有定义（否则玩家直接看到键名）。"""
        known = set(keys_of(self.text, "zh")) | set(keys_of(self.text, "en"))
        js_root = I18N.parent
        missing: dict[str, set[str]] = {}
        for path in sorted(js_root.glob("*.js")):
            if path == I18N:
                continue
            source = path.read_text(encoding="utf-8")
            for key in re.findall(r'\btr\(\s*"([A-Za-z][A-Za-z0-9_]*)"', source):
                if key not in known:
                    missing.setdefault(path.name, set()).add(key)
            for key in re.findall(r'\btr\(\s*\'([A-Za-z][A-Za-z0-9_]*)\'', source):
                if key not in known:
                    missing.setdefault(path.name, set()).add(key)
        self.assertEqual(missing, {}, f"tr() keys without a definition: {missing}")


if __name__ == "__main__":
    unittest.main()
