import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "mobile" / "build_mobile.py"
SPEC = importlib.util.spec_from_file_location("build_mobile", MODULE_PATH)
build_mobile = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(build_mobile)


class MobileExportTests(unittest.TestCase):
    def fixture(self):
        return {
            "generated_at": "2026-07-16T10:00:00+00:00",
            "duplicates_collapsed": 2,
            "archive_count": 999,
            "saved": ["private-id"],
            "notes": {"private-id": "never publish this"},
            "articles": [
                {
                    "id": "article-1",
                    "title": "Blender workflow",
                    "url": "https://example.com/article",
                    "summary": "A public RSS excerpt",
                    "published_at": "2026-07-16T09:00:00+00:00",
                    "source": "Example",
                    "source_id": "example",
                    "lane": "Tech & Development",
                    "software_group": "Blender",
                    "software_tags": ["Blender"],
                    "topic_tags": ["Modeling & sculpting"],
                    "priority_score": 88,
                    "private_note": "do not copy",
                    "related": [{"source": "Other", "title": "Coverage", "url": "https://other.example", "secret": "no"}],
                    "sources": [{"id": "example", "name": "Example", "accent": "#fff", "feed": "https://example.com/private-feed"}],
                }
            ],
            "sources": [
                {
                    "id": "example",
                    "name": "Example",
                    "site": "https://example.com",
                    "feed": "https://example.com/feed.xml",
                    "accent": "#fff",
                    "ok": True,
                    "count": 1,
                }
            ],
            "warnings": ["Example: connection detail that should stay private"],
        }

    def test_public_payload_uses_an_explicit_allowlist(self):
        result = build_mobile.sanitize_feed(self.fixture())
        serialized = str(result)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["unique_count"], 1)
        self.assertNotIn("saved", result)
        self.assertNotIn("archive_count", result)
        self.assertNotIn("never publish this", serialized)
        self.assertNotIn("private_note", serialized)
        self.assertNotIn("feed.xml", serialized)
        self.assertNotIn("connection detail", serialized)
        self.assertEqual(result["unavailable_sources"], ["Example"])

    def test_build_copies_only_mobile_assets_and_sanitized_feed(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "site"
            build_mobile.build_site(output, self.fixture())
            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "feed.json").is_file())
            self.assertFalse((output / "user-state.json").exists())
            self.assertFalse((output / "cg-signal.db").exists())

    def test_mobile_shell_keeps_inline_controls_reachable(self):
        site = MODULE_PATH.parent / "site"
        html = (site / "index.html").read_text(encoding="utf-8")
        javascript = (site / "app.js").read_text(encoding="utf-8")
        styles = (site / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn('id="explore-button"', html)
        self.assertNotIn('id="explore-panel"', html)
        self.assertEqual(html.count("data-search-input"), 1)
        self.assertEqual(html.count("data-source-select"), 0)
        self.assertEqual(html.count("data-category-list"), 1)
        self.assertIn('id="source-list"', html)
        self.assertIn('class="search-box header-search"', html)
        self.assertNotIn('<select id="source-select"', html)
        self.assertIn('data-source-option', javascript)
        self.assertIn("function syncControlValues()", javascript)
        self.assertNotIn("function openExplore", javascript)
        self.assertIn("function renderSourceButtons()", javascript)
        self.assertIn(".source-button", styles)
        self.assertIn(".header-search", styles)
        self.assertIn('id="scroll-top-button"', html)
        self.assertIn('id="filter-drawer-handle"', html)
        self.assertIn('id="filter-drawer-content"', html)
        self.assertIn("window.scrollTo({ top: 0", javascript)
        self.assertIn("setFilterDrawerExpanded", javascript)
        self.assertIn("pointerdown", javascript)
        self.assertIn("position: sticky", styles)
        self.assertIn(".filter-drawer.is-collapsed", styles)
        self.assertIn(".scroll-top-button", styles)
        self.assertIn("grid-template-columns: repeat(3, 1fr)", styles)

    def test_mobile_source_management_is_device_local(self):
        site = MODULE_PATH.parent / "site"
        html = (site / "index.html").read_text(encoding="utf-8")
        javascript = (site / "app.js").read_text(encoding="utf-8")
        styles = (site / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="source-manager-panel"', html)
        self.assertIn('id="enable-all-sources"', html)
        self.assertNotIn("Add RSS", html)
        self.assertIn('disabledSources: "cg-signal-mobile:disabled-sources"', javascript)
        self.assertIn("function persistDisabledSources()", javascript)
        self.assertIn("function articleHasEnabledSource(article)", javascript)
        self.assertIn("function renderSourceManager()", javascript)
        self.assertIn("localStorage.removeItem(storageKeys.disabledSources)", javascript)
        self.assertIn(".source-manager-panel", styles)
        self.assertIn(".source-manager-item.is-enabled", styles)


if __name__ == "__main__":
    unittest.main()
