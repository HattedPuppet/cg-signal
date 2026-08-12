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
    def test_runtime_paths_include_private_backup_and_database_lease_locations(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = RuntimePaths.for_root(Path(temporary))
            self.assertEqual(paths.backup_dir, Path(temporary).resolve() / ".backups")
            self.assertEqual(paths.database_lock_file.parent, paths.cache_dir)
            self.assertEqual(paths.database_lock_file.name, "database.lock")

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

    def test_search_lives_in_the_sticky_header_above_filters(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('id="search-input"'), 1)
        self.assertEqual(html.count('class="search-wrap"'), 1)
        self.assertNotIn('class="feed-search-row"', html)
        self.assertIn('id="home-button"', html)
        self.assertLess(html.index('id="home-button"'), html.index('class="search-wrap"'))
        self.assertLess(html.index('class="search-wrap"'), html.index('class="topbar-actions"'))
        self.assertLess(html.index('class="topbar"'), html.index('class="feed-toolbar"'))
        self.assertLess(html.index('class="feed-toolbar"'), html.index('id="stories"'))
        self.assertIn('id="scroll-top-button"', html)
        self.assertNotIn('id="density-toggle"', html)
        self.assertIn('id="sidebar-toggle"', html)
        self.assertIn('<script src="/app.js" type="module">', html)
        self.assertNotIn('id="sidebar-close"', html)
        self.assertNotIn('class="brand-row"', html)
        self.assertNotIn('class="local-pill"', html)
        self.assertLess(html.index('id="sidebar-toggle"'), html.index('class="search-wrap"'))
        self.assertLess(html.index('id="sidebar-toggle"'), html.index('<main class="main-panel">'))
        self.assertIn('class="sidebar-toggle-icon"', html)
        self.assertIn('elements.home.addEventListener("click"', (SITE / "app.js").read_text(encoding="utf-8"))
        self.assertIn('window.scrollTo({ top: 0, behavior: "auto" })', (SITE / "app.js").read_text(encoding="utf-8"))

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

    def test_desktop_filters_follow_search_without_a_board(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        styles = (SITE / "styles.css").read_text(encoding="utf-8")
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('class="hero"', html)
        self.assertNotIn('id="hero-unique"', html)
        self.assertNotIn('id="source-orbit"', html)
        self.assertNotIn("heroUnique", javascript)
        self.assertNotIn("sourceOrbit", javascript)
        self.assertNotIn(".hero {", styles)
        self.assertIn(".feed-toolbar {", styles)
        self.assertIn("position: sticky", styles)
        self.assertIn("backdrop-filter: blur(16px)", styles)
        self.assertIn("padding: 2px 2px 18px", styles)
        self.assertIn('id="last-updated" class="feed-update-status"', html)

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

    def test_learning_library_is_the_only_user_saving_feature(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-view="saved"', html)
        self.assertIn("Learning library", html)
        self.assertNotIn('data-view="archived"', html)
        self.assertNotIn("data-archive-id", javascript)
        self.assertNotIn('key === "a"', javascript)
        self.assertIn('saved: new Set()', javascript)
        self.assertIn('const presentationStorageKeys = new Set(Object.values(storageKeys));', javascript)
        self.assertIn('key?.startsWith("cg-signal:") && !presentationStorageKeys.has(key)', javascript)

    def test_latest_signal_has_a_recent_publication_window(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        styles = (SITE / "styles.css").read_text(encoding="utf-8")
        self.assertIn('data-time-window="month"', html)
        self.assertIn('data-time-window="quarter"', html)
        self.assertIn('data-time-window="all"', html)
        lane_group = html.index('class="lane-filters"')
        window_group = html.index('class="time-window-row"')
        result_summary = html.index('class="result-summary"')
        self.assertLess(lane_group, window_group)
        self.assertLess(window_group, result_summary)
        self.assertIn('timeWindow: "cg-signal:time-window"', javascript)
        self.assertIn("function articleWithinTimeWindow(article)", javascript)
        self.assertIn("function articleMonthLabel(article)", javascript)
        self.assertIn(".month-divider", styles)

    def test_latest_signal_is_the_default_chronological_view(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        styles = (SITE / "styles.css").read_text(encoding="utf-8")
        self.assertIn('data-view="all"', html)
        self.assertIn('view: "all"', javascript)
        self.assertNotIn("feedback", javascript.lower())
        self.assertNotIn("reduced", javascript.lower())
        self.assertNotIn("is-returning", styles)

    def test_sidebar_handle_tracks_drawer_boundary_at_both_breakpoints(self):
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        styles = (SITE / "styles.css").read_text(encoding="utf-8")
        self.assertIn("data-sidebar-toggle-icon", javascript)
        self.assertIn("state.sidebarOpen ? \"‹\" : \"›\"", javascript)
        self.assertIn("--sidebar-width: 258px", styles)
        self.assertIn("@media (max-width: 850px)", styles)
        self.assertIn(".app-shell.sidebar-closed .sidebar", styles)
        self.assertIn("left: calc(var(--sidebar-width) - 1px)", styles)
        self.assertIn("width: var(--sidebar-width)", styles)
        self.assertIn("body.night .sidebar-toggle", styles)
        self.assertIn("background: var(--sidebar);", styles)
        self.assertIn("transition: left 220ms ease", styles)

    def test_warnings_have_a_concise_summary_and_expandable_detail(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="notice"', html)
        self.assertIn("sources unavailable · showing cached stories", javascript)
        self.assertIn("Show unavailable sources", javascript)

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

    def test_user_state_controls_require_authoritative_recovery(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="user-state-status"', html)
        self.assertIn('userStateStatus: "loading"', javascript)
        self.assertIn('state.userStateStatus = "error"', javascript)
        self.assertIn('state.userStateStatus = "ready"', javascript)
        self.assertIn('data-retry-user-state', javascript)
        self.assertIn('if (state.userStateStatus !== "ready") return;', javascript)
        self.assertIn('[data-save-id], [data-source-action]', javascript)
        self.assertIn('#reset-sources, [data-view=\'saved\']', javascript)
        self.assertIn('if (!userStateReady()) return;', javascript)

    def test_schema_transition_warning_is_documented(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        prd = (PROJECT_ROOT / "PRD.md").read_text(encoding="utf-8")
        for document in (readme, prd):
            lowered = document.lower()
            self.assertIn("schema 1", lowered)
            self.assertIn("schema 2", lowered)
            self.assertIn("in place", lowered)
            self.assertIn("saved ids", lowered)
            self.assertIn("muted sources", lowered)
            self.assertIn("permanently discards", lowered)
        self.assertIn("old schema 1 binary rejects a schema 2 database", readme.lower())
        self.assertIn("format 1 snapshot", readme.lower())
        self.assertIn("cannot include changes made after upgrade", readme.lower())

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
