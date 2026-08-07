import tempfile
import unittest
from pathlib import Path

from cg_signal.config import FEEDS, RuntimePaths
from cg_signal.feeds import FeedService


class SourceConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        paths = RuntimePaths.for_root(Path(self.temporary.name))
        self.service = FeedService(paths)
        self.repository = self.service.repository

    def tearDown(self):
        self.temporary.cleanup()

    def test_builtin_and_custom_sources_can_be_managed(self):
        self.assertEqual(len(self.repository.list_source_configs()), len(FEEDS))
        added = self.repository.add_source_config({
            "name": "Example CG",
            "feed": "https://example.com/feed.xml",
            "site": "https://example.com/",
        })
        self.assertFalse(added["is_builtin"])
        self.assertTrue(added["enabled"])
        disabled = self.repository.set_source_enabled(added["id"], False)
        self.assertFalse(disabled["enabled"])
        self.assertNotIn(added["id"], {item["id"] for item in self.repository.list_source_configs(True)})
        with self.assertRaisesRegex(ValueError, "already configured"):
            self.repository.add_source_config({"name": "Duplicate", "feed": added["feed"]})

    def test_only_http_feed_urls_are_accepted(self):
        with self.assertRaisesRegex(ValueError, "HTTP or HTTPS"):
            self.repository.add_source_config({"name": "Unsafe", "feed": "file:///tmp/feed.xml"})

    def test_automaton_is_a_builtin_source(self):
        source = next(item for item in FEEDS if item["id"] == "automaton")
        self.assertEqual(source["feed"], "https://automaton-media.com/feed/")
        self.assertEqual(source["site"], "https://automaton-media.com/")
        self.assertEqual(source["limit"], 20)

    def test_denfaminicogamer_is_a_builtin_source(self):
        source = next(item for item in FEEDS if item["id"] == "denfaminicogamer")
        self.assertEqual(source["name"], "Denfaminicogamer")
        self.assertEqual(source["feed"], "https://news.denfaminicogamer.jp/feed")
        self.assertEqual(source["site"], "https://news.denfaminicogamer.jp/")
        self.assertEqual(source["limit"], 20)


if __name__ == "__main__":
    unittest.main()
