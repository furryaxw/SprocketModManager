import re
import unittest
from pathlib import Path


SITE_ROOT = Path(__file__).resolve().parents[1] / "site"


class SiteUiTests(unittest.TestCase):
    def test_primary_button_color_meets_text_contrast(self):
        styles = (SITE_ROOT / "styles.css").read_text(encoding="utf-8")

        def color(variable):
            match = re.search(rf"--{variable}:\s*(#[0-9a-fA-F]{{6}})", styles)
            self.assertIsNotNone(match)
            return match.group(1)

        def luminance(value):
            channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [
                channel / 12.92
                if channel <= 0.04045
                else ((channel + 0.055) / 1.055) ** 2.4
                for channel in channels
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        for background in (color("button-accent"), color("button-accent-hover")):
            brighter, darker = sorted((luminance(background), luminance("#ffffff")), reverse=True)
            self.assertGreaterEqual((brighter + 0.05) / (darker + 0.05), 4.5)

    def test_header_links_to_latest_client_release(self):
        html = (SITE_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn(
            'href="https://github.com/furryaxw/sprocket-mods/releases/latest"',
            html,
        )
        self.assertIn('data-i18n="downloadClient"', html)

    def test_catalog_uses_embedded_release_cache_without_github_api(self):
        html = (SITE_ROOT / "index.html").read_text(encoding="utf-8")
        script = (SITE_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn("pkg.releases", script)
        self.assertIn('cache: "no-store"', script)
        self.assertNotIn("release-api.js", html)
        self.assertNotIn("api.github.com", script)
        self.assertNotIn("SprocketReleaseApi.fetchRepositoryReleases", script)
        self.assertNotIn("sprocket-release:", script)

    def test_pages_refreshes_embedded_releases_hourly(self):
        workflow = (SITE_ROOT.parent / ".github" / "workflows" / "pages.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("schedule:", workflow)
        self.assertIn("--fetch-releases", workflow)
        self.assertIn("validate_registry.py --mods-dir mods --offline", workflow)

    def test_pages_custom_domain_is_packaged_with_the_site(self):
        cname = (SITE_ROOT / "CNAME").read_text(encoding="utf-8")

        self.assertEqual(cname, "sprocketmods.furryaxw.top\n")

    def test_language_pickers_use_the_sort_select_structure_without_inputs(self):
        html = (SITE_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertEqual(html.count('class="language-picker select-control"'), 2)
        self.assertEqual(html.count('data-lucide="chevron-down" aria-hidden="true"'), 3)
        self.assertNotIn('data-field="custom-language"', html)
        self.assertNotIn('value="__custom__"', html)

    def test_language_picker_options_are_data_driven(self):
        script = (SITE_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn("const SUBMISSION_LANGUAGES = [", script)
        self.assertIn("populateLanguageOptions(languageSelect);", script)
        self.assertNotIn("updateCustomLanguage", script)

    def test_localized_fields_match_catalog_tool_font_size(self):
        styles = (SITE_ROOT / "styles.css").read_text(encoding="utf-8")

        self.assertIn(
            ".localized-row input, .localized-row textarea, .localized-row select { font-size: 13px; }",
            styles,
        )
