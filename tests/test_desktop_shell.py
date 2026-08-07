import unittest
from pathlib import Path
import tempfile
import threading
import urllib.request

from cg_signal.config import RuntimePaths
from cg_signal.http import DashboardHandler, DashboardServer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SITE = PROJECT_ROOT / "static"


class DesktopShellTests(unittest.TestCase):
    def test_domain_module_is_served_as_javascript(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = RuntimePaths.for_root(PROJECT_ROOT).with_cache_dir(Path(temporary))
            server = DashboardServer(("127.0.0.1", 0), DashboardHandler, paths=paths)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/domain.mjs") as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "application/javascript")
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

    def test_search_lives_in_the_sticky_header(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('id="search-input"'), 1)
        self.assertEqual(html.count('class="search-wrap"'), 1)
        self.assertLess(html.index('class="search-wrap"'), html.index('class="topbar-actions"'))
        self.assertIn('id="scroll-top-button"', html)
        self.assertNotIn('id="density-toggle"', html)
        self.assertIn('id="sidebar-toggle"', html)
        self.assertIn('<script src="/app.js" type="module">', html)
        self.assertNotIn('id="sidebar-close"', html)
        self.assertNotIn('class="brand-row"', html)
        self.assertNotIn('class="local-pill"', html)
        self.assertLess(html.index('id="sidebar-toggle"'), html.index('class="search-wrap"'))
        self.assertIn('class="sidebar-toggle-lines"', html)

    def test_first_article_jump_is_instant_and_persistent(self):
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        self.assertIn('from "./domain.mjs"', javascript)
        styles = (SITE / "styles.css").read_text(encoding="utf-8")
        self.assertIn('story-card:not(.skeleton-card)', javascript)
        self.assertIn('scrollIntoView({ behavior: "auto", block: "center" })', javascript)
        self.assertIn(".scroll-top-button", styles)
        self.assertIn("position: fixed", styles)
        self.assertIn(".sidebar-closed", styles)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", styles)
        self.assertIn("body.night .sidebar", styles)
        self.assertIn("--sidebar: #e5e2d8", styles)
        self.assertIn("--sidebar: #151915", styles)
        self.assertIn("color-mix(in srgb, var(--blue) 22%, var(--paper-deep))", styles)
        self.assertIn("sidebarInteractionIsInternal", javascript)
        self.assertIn('document.addEventListener("pointerdown"', javascript)
        self.assertIn('document.addEventListener("focusin"', javascript)

    def test_brief_read_and_density_features_are_removed_from_desktop(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        styles = (SITE / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn('id="briefing-panel"', html)
        self.assertNotIn('id="unread-count"', html)
        self.assertNotIn("state.read", javascript)
        self.assertNotIn("data-read-id", javascript)
        self.assertNotIn("briefing", javascript.lower())
        self.assertNotIn("density-toggle", javascript)
        self.assertNotIn("density", javascript.lower())
        self.assertNotIn(".briefing", styles)
        self.assertNotIn("density", styles.lower())
        self.assertIn("sidebarOpen", javascript)
        self.assertIn("setSidebarOpen", javascript)

    def test_latest_signal_has_a_recent_publication_window(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        styles = (SITE / "styles.css").read_text(encoding="utf-8")
        self.assertIn('data-time-window="month"', html)
        self.assertIn('data-time-window="quarter"', html)
        self.assertIn('data-time-window="all"', html)
        self.assertIn('timeWindow: "cg-signal:time-window"', javascript)
        self.assertIn("function articleWithinTimeWindow(article)", javascript)
        self.assertIn("function articleMonthLabel(article)", javascript)
        self.assertIn(".month-divider", styles)

    def test_obsolete_or_invalid_persisted_lane_defaults_to_all(self):
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        self.assertIn('ARTICLE_LANE_VALUES', javascript)
        self.assertIn('LANE_VALUES', javascript)
        self.assertIn('lane: LANE_VALUES.has(storedLane) ? storedLane : "All"', javascript)

    def test_feed_rejects_stale_or_unknown_classification_lanes(self):
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        self.assertIn("FEED_SCHEMA_VERSION", javascript)
        self.assertIn("function validateFeedPayload(payload)", javascript)
        self.assertIn("payload?.feed_schema_version !== FEED_SCHEMA_VERSION", javascript)
        self.assertIn("feedPayloadIsStructurallyCompatible(payload)", javascript)
        self.assertNotIn("CLASSIFICATION_VERSION", javascript)
        self.assertIn("Restart CG Signal", javascript)

    def test_launcher_replaces_only_the_recorded_stale_server(self):
        launcher = (PROJECT_ROOT / "launch-dashboard.ps1").read_text(encoding="utf-8")
        server = (PROJECT_ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('$serverScript, "--print-source-revision"', launcher)
        self.assertNotIn('Get-FileHash -LiteralPath $serverScript -Algorithm SHA256', launcher)
        self.assertIn('$health.source_revision -eq $serverSourceRevision', launcher)
        self.assertIn('Join-Path $projectRoot ".cache\\server.pid"', launcher)
        self.assertIn('if (-not $health.pid -or -not (Test-Path -LiteralPath $serverPidFile))', launcher)
        self.assertIn('if ([int]$health.pid -ne $candidateProcessId)', launcher)
        self.assertIn('$process.ProcessName -notin @("python", "python3", "py")', launcher)
        self.assertIn('Stop-Process -Id $candidateProcessId', launcher)
        self.assertIn('--print-source-revision', server)

    def test_startup_requests_state_and_feed_in_parallel(self):
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        self.assertIn("Promise.all([loadUserState(), loadFeed()])", javascript)
        self.assertIn("syncAfterBackgroundRefresh(payload)", javascript)
        self.assertIn("syncAfterThumbnailRefresh(payload)", javascript)
        self.assertIn("?wait_thumbnails=1", javascript)

    def test_thumbnail_wait_rearms_after_a_server_timeout(self):
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        self.assertIn("thumbnailRefreshRetryTimer", javascript)
        self.assertIn("THUMBNAIL_REFRESH_RETRY_MAX_MS", javascript)
        self.assertIn(
            "if (state.payload?.thumbnails_refreshing) {\n"
            "          syncAfterThumbnailRefresh(state.payload);",
            javascript,
        )


if __name__ == "__main__":
    unittest.main()
