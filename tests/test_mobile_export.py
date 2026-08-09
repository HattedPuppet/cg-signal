import importlib.util
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "mobile" / "build_mobile.py"
SPEC = importlib.util.spec_from_file_location("build_mobile", MODULE_PATH)
build_mobile = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(build_mobile)


class MobileExportTests(unittest.TestCase):
    def fixture(self):
        return {
            "classification_version": 4,
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
                    "etag": '"private-request-validator"',
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
        self.assertNotIn("private-request-validator", serialized)
        self.assertNotIn("connection detail", serialized)
        self.assertEqual(result["unavailable_sources"], ["Example"])

    def test_build_copies_only_mobile_assets_and_sanitized_feed(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "site"
            build_mobile.build_site(output, self.fixture())
            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "feed.json").is_file())
            self.assertTrue((output / "domain.mjs").is_file())
            self.assertFalse((output / "user-state.json").exists())
            self.assertFalse((output / "cg-signal.db").exists())

    def test_scheduled_build_restores_only_the_public_request_cache(self):
        project_root = MODULE_PATH.parents[1]
        workflow = (
            project_root / ".github" / "workflows" / "mobile-pages.yml"
        ).read_text(encoding="utf-8")
        ignore = (project_root / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("uses: actions/cache@v5", workflow)
        self.assertIn("path: .mobile-cache", workflow)
        self.assertIn("--request-cache-dir .mobile-cache", workflow)
        self.assertIn(".mobile-cache/", ignore)

    def test_previous_public_feed_fills_transient_gaps_with_bounded_history(self):
        current = self.fixture()
        previous = {
            "classification_version": 4,
            "generated_at": "2026-07-15T10:00:00+00:00",
            "articles": [
                {
                    "id": "article-2",
                    "title": "A retained Houdini story",
                    "url": "https://example.com/retained",
                    "published_at": "2026-07-10T09:00:00+00:00",
                    "source": "Example",
                    "source_id": "example",
                    "private_note": "must still be removed",
                },
                {
                    "id": "duplicate-url",
                    "title": "Duplicate of the current story",
                    "url": "https://example.com/article",
                    "published_at": "2026-07-15T09:00:00+00:00",
                    "source": "Example",
                    "source_id": "example",
                },
                {
                    "id": "expired",
                    "title": "Outside the rolling history",
                    "url": "https://example.com/expired",
                    "published_at": "2025-01-01T09:00:00+00:00",
                    "source": "Example",
                    "source_id": "example",
                },
            ],
            "sources": current["sources"],
        }

        result = build_mobile.merge_feed_history(
            current,
            previous,
            now=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )

        self.assertEqual(
            [article["id"] for article in result["articles"]],
            ["article-1", "article-2"],
        )
        self.assertEqual(result["carried_forward_count"], 1)
        self.assertEqual(result["sources"][0]["count"], 2)
        self.assertNotIn("private_note", str(result))

    def test_previous_classification_version_is_not_carried_forward(self):
        current = self.fixture()
        previous = {
            "classification_version": 1,
            "generated_at": "2026-07-15T10:00:00+00:00",
            "articles": [
                {
                    "id": "stale-label",
                    "title": "Limited-time gacha event",
                    "url": "https://example.com/stale",
                    "published_at": "2026-07-15T09:00:00+00:00",
                    "source": "Example",
                    "source_id": "example",
                    "lane": "Tech & Development",
                }
            ],
            "sources": current["sources"],
        }

        result = build_mobile.merge_feed_history(
            current,
            previous,
            now=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )

        self.assertEqual([article["id"] for article in result["articles"]], ["article-1"])
        self.assertEqual(result["classification_version"], 4)
        self.assertEqual(result["carried_forward_count"], 0)

    def test_mobile_offline_cache_requires_current_feed_schema(self):
        javascript = (MODULE_PATH.parent / "site" / "app.js").read_text(encoding="utf-8")
        self.assertIn("FEED_SCHEMA_VERSION", javascript)
        self.assertIn("payload?.feed_schema_version === FEED_SCHEMA_VERSION", javascript)
        self.assertIn("function feedPayloadIsCompatible(payload)", javascript)
        self.assertIn("feedPayloadIsStructurallyCompatible(payload)", javascript)
        self.assertNotIn("CLASSIFICATION_VERSION", javascript)

    def test_mobile_shell_revision_is_bumped_with_lane_schema(self):
        site = MODULE_PATH.parent / "site"
        html = (site / "index.html").read_text(encoding="utf-8")
        javascript = (site / "app.js").read_text(encoding="utf-8")
        worker = (site / "sw.js").read_text(encoding="utf-8")
        self.assertIn('from "./domain.mjs"', javascript)
        self.assertIn('type="module"', html)
        self.assertIn("app.js?v=20260810", html)
        self.assertIn("styles.css?v=20260810", html)
        self.assertIn('register("./sw.js?v=20260810")', javascript)
        self.assertIn('const CACHE_NAME = "cg-signal-mobile-v20"', worker)
        self.assertIn("app.js?v=20260810", worker)
        self.assertIn("./domain.mjs", worker)

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
        self.assertIn('id="density-toggle"', html)
        self.assertIn('id="filter-drawer-handle"', html)
        self.assertIn('id="filter-drawer-content"', html)
        self.assertIn("scrollIntoView({ behavior: \"auto\", block: \"center\" })", javascript)
        self.assertIn("story-card:not(.skeleton)", javascript)
        self.assertNotIn('window.scrollTo({ top: 0, behavior: "smooth"', javascript)
        self.assertIn("setFilterDrawerExpanded", javascript)
        self.assertIn('cg-signal-mobile:density', javascript)
        self.assertIn('storyList.classList.toggle("is-compact"', javascript)
        self.assertIn('document.addEventListener("pointerdown"', javascript)
        self.assertIn('elements.filterDrawer.contains(event.target)', javascript)
        self.assertIn("pointerdown", javascript)
        self.assertIn('class="app-header-row"', html)
        self.assertIn("position: sticky", styles)
        self.assertIn(".filter-drawer { padding: 5px 0; }", styles)
        self.assertIn(".filter-drawer.is-collapsed", styles)
        self.assertIn(".app-header .filter-drawer-handle", styles)
        self.assertIn("rgba(255,255,255,.1)", styles)
        self.assertIn(".scroll-top-button", styles)
        self.assertIn(".story-list.is-compact", styles)
        self.assertIn("grid-template-columns: 102px minmax(0, 1fr)", styles)
        self.assertIn("grid-template-columns: repeat(4, 1fr)", styles)
        self.assertIn("margin: 4px 0 0", styles)
        self.assertNotIn('class="hero"', html)
        self.assertNotIn('id="story-total"', html)
        self.assertNotIn('id="repeat-total"', html)
        self.assertNotIn('id="recent-total"', html)
        self.assertIn('class="update-row feed-update-row"', html)
        self.assertNotIn("storyTotal", javascript)
        self.assertNotIn("cg-signal-mobile:visited", javascript)

    def test_mobile_source_management_is_device_local(self):
        site = MODULE_PATH.parent / "site"
        html = (site / "index.html").read_text(encoding="utf-8")
        javascript = (site / "app.js").read_text(encoding="utf-8")
        styles = (site / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="source-manager-panel"', html)
        self.assertIn('class="source-manage-button"', html)
        self.assertNotIn('id="result-count"', html)
        self.assertIn('id="enable-all-sources"', html)
        self.assertNotIn("Add RSS", html)
        self.assertIn('disabledSources: "cg-signal-mobile:disabled-sources"', javascript)
        self.assertIn("function persistDisabledSources()", javascript)
        self.assertIn("function articleHasEnabledSource(article)", javascript)
        self.assertNotIn("if (article.source_id) return !state.disabledSources", javascript)
        self.assertIn("function renderSourceManager()", javascript)
        self.assertNotIn("resultCount", javascript)
        self.assertIn("localStorage.removeItem(storageKeys.disabledSources)", javascript)
        self.assertIn(".source-manager-panel", styles)
        self.assertIn(".source-manage-button", styles)
        self.assertIn(".source-manager-item.is-enabled", styles)

    def test_mobile_feed_has_pins_without_brief_or_read_state(self):
        site = MODULE_PATH.parent / "site"
        html = (site / "index.html").read_text(encoding="utf-8")
        javascript = (site / "app.js").read_text(encoding="utf-8")
        styles = (site / "styles.css").read_text(encoding="utf-8")
        self.assertIn('data-view="pinned"', html)
        self.assertIn('id="pinned-total"', html)
        self.assertNotIn('id="brief-panel"', html)
        self.assertNotIn('id="briefing-listen"', html)
        self.assertNotIn('id="unread-count"', html)
        self.assertIn('pinned: "cg-signal-mobile:pinned"', javascript)
        self.assertNotIn("currentIds.has(id)", javascript)
        self.assertNotIn("state.read", javascript)
        self.assertNotIn("data-read-id", javascript)
        self.assertNotIn("brief", javascript.lower())
        self.assertNotIn("brief", styles.lower())
        self.assertIn("data-pin-id", javascript)

    def test_mobile_shell_has_a_recent_publication_window(self):
        site = MODULE_PATH.parent / "site"
        html = (site / "index.html").read_text(encoding="utf-8")
        javascript = (site / "app.js").read_text(encoding="utf-8")
        styles = (site / "styles.css").read_text(encoding="utf-8")
        service_worker = (site / "sw.js").read_text(encoding="utf-8")
        self.assertIn('data-time-window="month"', html)
        self.assertIn('data-time-window="quarter"', html)
        self.assertIn('data-time-window="all"', html)
        self.assertIn('timeWindow: "cg-signal-mobile:time-window"', javascript)
        self.assertIn("function articleWithinTimeWindow(article)", javascript)
        self.assertIn("function storyListMarkup(articles)", javascript)
        self.assertIn("function readCachedFeed()", javascript)
        self.assertIn("applyFeed(cached)", javascript)
        self.assertIn("cg-signal-mobile-v20", service_worker)
        self.assertIn("styles.css?v=20260810", service_worker)
        self.assertIn("app.js?v=20260810", service_worker)
        self.assertIn("./domain.mjs", service_worker)
        self.assertIn("sw.js?v=20260810", javascript)
        self.assertIn("sw.js?v=20260810", service_worker)
        self.assertIn("fetch(event.request)", service_worker)
        self.assertIn("function articleRecencyBucket(article)", javascript)
        self.assertIn("Last 24 hours", javascript)
        self.assertIn("Last 3 days", javascript)
        self.assertIn(".recency-section", styles)

    def test_mobile_bottom_navigation_uses_latest_and_pinned(self):
        html = (MODULE_PATH.parent / "site" / "index.html").read_text(encoding="utf-8")
        javascript = (MODULE_PATH.parent / "site" / "app.js").read_text(encoding="utf-8")
        styles = (MODULE_PATH.parent / "site" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".bottom-nav", styles)
        self.assertIn('data-view="latest"', html)
        self.assertIn('data-view="pinned"', html)
        self.assertIn('view: "latest"', javascript)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", styles)


if __name__ == "__main__":
    unittest.main()
