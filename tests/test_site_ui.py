import re
import unittest
from pathlib import Path


SITE_ROOT = Path(__file__).resolve().parents[1] / "site"


SCRIPT_TAG = re.compile(r'<script\s+src="\./(js/[^"]+\.js)"')


def site_javascript() -> str:
    """`index.html` 实际加载的站点脚本（站点的网络行为只看这些文件）。"""
    html = (SITE_ROOT / "index.html").read_text(encoding="utf-8")
    return "\n".join(
        (SITE_ROOT / relative).read_text(encoding="utf-8")
        for relative in SCRIPT_TAG.findall(html)
    )


class SiteUiTests(unittest.TestCase):
    def test_site_uses_packaged_application_icon(self):
        html = (SITE_ROOT / "index.html").read_text(encoding="utf-8")
        icon = SITE_ROOT / "favicon.png"

        self.assertTrue(icon.is_file())
        self.assertRegex(html, r'<link\s+rel="icon"\s+type="image/png"\s+href="\./favicon\.png"')
        self.assertRegex(html, r'<span class="brand-mark"><img\s+src="\./favicon\.png"\s+alt=""\s*/></span>')

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

    def test_header_links_to_latest_client_executable(self):
        html = (SITE_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn(
            'href="https://github.com/furryaxw/sprocket-mods/releases/latest/download/SprocketModManager.exe"',
            html,
        )
        self.assertIn('data-i18n="downloadClient"', html)

    def test_catalog_uses_embedded_release_cache_without_github_api(self):
        script = site_javascript()

        self.assertIn("pkg.releases", script)
        self.assertIn('cache: "no-store"', script)
        self.assertNotIn("api.github.com", script)

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

    def test_details_show_recommendations(self):
        html = (SITE_ROOT / "index.html").read_text(encoding="utf-8")
        script = site_javascript()

        self.assertIn('id="detail-recommendations"', html)
        self.assertIn("pkg.recommendations || []", script)

    def test_featured_packages_are_pinned_and_labeled(self):
        html = (SITE_ROOT / "index.html").read_text(encoding="utf-8")
        script = site_javascript()

        self.assertIn('id="detail-featured" hidden', html)
        self.assertIn("if (featured) return featured", script)
        self.assertIn('class="featured-star"', script)
        self.assertIn('tr("starterRecommended")', script)
