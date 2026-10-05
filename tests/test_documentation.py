import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_english_documents_are_default_and_link_to_chinese(self):
        # (chinese_name, english_name): English is the default document, Chinese is the sibling.
        pairs = (("README.zh.md", "README.md"),)

        for chinese_name, english_name in pairs:
            with self.subTest(document=english_name):
                chinese = (ROOT / chinese_name).read_text(encoding="utf-8")
                english = (ROOT / english_name).read_text(encoding="utf-8")
                self.assertIn(f"[English]({english_name})", chinese)
                self.assertIn(f"[中文]({chinese_name})", english)


if __name__ == "__main__":
    unittest.main()
