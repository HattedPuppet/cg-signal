import unittest
from pathlib import Path


SITE = Path(__file__).resolve().parents[1] / "static"


class DesktopShellTests(unittest.TestCase):
    def test_search_lives_in_the_sticky_header(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('id="search-input"'), 1)
        self.assertEqual(html.count('class="search-wrap"'), 1)
        self.assertLess(html.index('class="search-wrap"'), html.index('class="topbar-actions"'))
        self.assertIn('id="scroll-top-button"', html)
        self.assertNotIn('id="density-toggle"', html)
        self.assertIn('id="sidebar-toggle"', html)
        self.assertNotIn('id="sidebar-close"', html)
        self.assertNotIn('class="brand-row"', html)
        self.assertNotIn('class="local-pill"', html)
        self.assertLess(html.index('id="sidebar-toggle"'), html.index('class="search-wrap"'))
        self.assertIn('class="sidebar-toggle-lines"', html)

    def test_first_article_jump_is_instant_and_persistent(self):
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
