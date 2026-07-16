import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


def article(article_id, title, software_group="Production techniques", lane="Tech & Development"):
    return {
        "id": article_id,
        "title": title,
        "url": f"https://example.com/{article_id}",
        "summary": "A practical lighting workflow reference",
        "image": "",
        "published_at": "2026-07-16T10:00:00+00:00",
        "source": "Example Source",
        "source_id": "example",
        "source_site": "https://example.com/",
        "accent": "#4b75ff",
        "lane": lane,
        "related": [],
        "source_count": 1,
        "cluster_size": 1,
        "sources": [{"id": "example", "name": "Example Source", "accent": "#4b75ff"}],
        "priority_score": 50,
        "priority_reasons": [software_group],
        "software_tags": [] if software_group == "Production techniques" else [software_group],
        "software_group": software_group,
        "topic_tags": ["Lighting & rendering"] if software_group == "Production techniques" else [],
    }


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database_patch = mock.patch.object(
            server, "ARCHIVE_DB_FILE", Path(self.temporary.name) / "archive.db"
        )
        self.database_patch.start()
        server.ARCHIVE_INITIALIZED = False

    def tearDown(self):
        server.ARCHIVE_INITIALIZED = False
        self.database_patch.stop()
        self.temporary.cleanup()

    def test_articles_are_durable_searchable_and_paginated(self):
        server.archive_articles(
            [
                article("blender-light", "Blender lighting breakdown", "Blender"),
                article("studio-deal", "Animation studio acquisition", "Industry context", "Industry & Business"),
            ]
        )
        result = server.query_archive("#blender lighting", limit=10)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["articles"][0]["id"], "blender-light")
        self.assertEqual(server.query_archive("-#industry", limit=1)["total"], 1)
        self.assertTrue(server.query_archive("", limit=1)["has_more"])

        updated = article("blender-light", "Blender lighting workflow", "Blender")
        server.archive_articles([updated])
        self.assertEqual(server.archive_article_count(), 2)
        self.assertEqual(server.query_archive("workflow")["articles"][0]["title"], updated["title"])

    def test_saved_state_and_notes_are_searchable(self):
        server.archive_articles([article("saved-story", "Procedural material guide")])
        state = server.normalize_user_state(
            {
                "saved": ["saved-story"],
                "notes": {"saved-story": "Revisit this node graph reference"},
                "feedback": [{"id": "saved-story", "value": 1}],
            }
        )
        server.sync_user_state_to_archive(state)
        self.assertEqual(server.query_archive("#is:saved reference")["total"], 1)
        self.assertEqual(server.query_archive("#is:liked")["total"], 1)


if __name__ == "__main__":
    unittest.main()
