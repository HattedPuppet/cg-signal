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

    def test_first_article_jump_is_instant_and_persistent(self):
        javascript = (SITE / "app.js").read_text(encoding="utf-8")
        styles = (SITE / "styles.css").read_text(encoding="utf-8")
        self.assertIn('story-card:not(.skeleton-card)', javascript)
        self.assertIn('scrollIntoView({ behavior: "auto", block: "start" })', javascript)
        self.assertIn(".scroll-top-button", styles)
        self.assertIn("position: fixed", styles)


if __name__ == "__main__":
    unittest.main()
