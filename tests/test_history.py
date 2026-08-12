import tempfile
import unittest
from pathlib import Path

from cg_signal.config import RuntimePaths
from cg_signal.storage import SQLiteRepository


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


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = SQLiteRepository(RuntimePaths.for_root(Path(self.temporary.name)))

    def tearDown(self):
        self.temporary.cleanup()

    def test_articles_are_durable_searchable_and_paginated(self):
        self.repository.record_articles([
            article("blender-light", "Blender lighting breakdown", "Blender"),
            article("studio-deal", "Animation studio acquisition", "Business context", "Business"),
        ])
        result = self.repository.query_history("#blender lighting", limit=10)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["articles"][0]["id"], "blender-light")
        self.assertEqual(self.repository.query_history("-#industry", limit=1)["total"], 2)
        self.assertEqual(self.repository.query_history("#business")["total"], 1)
        self.assertTrue(self.repository.query_history("", limit=1)["has_more"])
        self.repository.record_articles([article("blender-light", "Blender lighting workflow", "Blender")])
        self.assertEqual(self.repository.history_article_count(), 2)
        self.assertEqual(self.repository.query_history("workflow")["articles"][0]["title"], "Blender lighting workflow")

    def test_saved_filter_and_text_search_do_not_use_notes_or_feedback(self):
        self.repository.record_articles([article("saved-story", "Procedural material guide")])
        self.repository.write_state({"saved": ["saved-story"], "notes": {"saved-story": "reference"}, "feedback": [{"id": "saved-story", "value": 1}]})
        self.assertEqual(self.repository.query_history("#is:saved")["total"], 1)
        self.assertEqual(self.repository.query_history("private memo")["total"], 0)
        self.assertEqual(self.repository.query_history("#is:liked")["total"], 0)


if __name__ == "__main__":
    unittest.main()
