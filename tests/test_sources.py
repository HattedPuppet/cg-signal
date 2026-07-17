import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


class SourceConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database_patch = mock.patch.object(
            server, "ARCHIVE_DB_FILE", Path(self.temporary.name) / "sources.db"
        )
        self.cache_patch = mock.patch.object(
            server, "CACHE_FILE", Path(self.temporary.name) / "feed-cache.json"
        )
        self.database_patch.start()
        self.cache_patch.start()
        server.ARCHIVE_INITIALIZED = False

    def tearDown(self):
        server.ARCHIVE_INITIALIZED = False
        self.cache_patch.stop()
        self.database_patch.stop()
        self.temporary.cleanup()

    def test_builtin_and_custom_sources_can_be_managed(self):
        self.assertEqual(len(server.list_source_configs()), len(server.FEEDS))
        added = server.add_source_config(
            {
                "name": "Example CG",
                "feed": "https://example.com/feed.xml",
                "site": "https://example.com/",
            }
        )
        self.assertFalse(added["is_builtin"])
        self.assertTrue(added["enabled"])
        disabled = server.set_source_enabled(added["id"], False)
        self.assertFalse(disabled["enabled"])
        self.assertNotIn(added["id"], {item["id"] for item in server.list_source_configs(True)})

        with self.assertRaisesRegex(ValueError, "already configured"):
            server.add_source_config({"name": "Duplicate", "feed": added["feed"]})

    def test_only_http_feed_urls_are_accepted(self):
        with self.assertRaisesRegex(ValueError, "HTTP or HTTPS"):
            server.add_source_config({"name": "Unsafe", "feed": "file:///tmp/feed.xml"})

    def test_automaton_is_a_builtin_source(self):
        source = next(item for item in server.FEEDS if item["id"] == "automaton")
        self.assertEqual(source["feed"], "https://automaton-media.com/feed/")
        self.assertEqual(source["site"], "https://automaton-media.com/")
        self.assertEqual(source["limit"], 20)


if __name__ == "__main__":
    unittest.main()
