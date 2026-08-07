import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cg_signal.config import CLASSIFICATION_REVISION, FEED_SCHEMA_VERSION, RuntimePaths
from cg_signal.feeds import FeedService
from cg_signal.storage import SQLiteRepository, STATE_IMPORT_MARKER


class StorageMigrationTests(unittest.TestCase):
    def make_runtime(self):
        temporary = tempfile.TemporaryDirectory()
        paths = RuntimePaths.for_root(Path(temporary.name))
        return temporary, paths

    def test_valid_json_imports_once_and_remains_recovery_evidence(self):
        temporary, paths = self.make_runtime()
        try:
            paths.cache_dir.mkdir(parents=True, exist_ok=True)
            original = {"saved": ["legacy-save"], "muted_sources": ["noisy"]}
            paths.user_state_file.write_text(json.dumps(original), encoding="utf-8")
            repository = SQLiteRepository(paths)
            self.assertEqual(repository.read_state()["saved"], ["legacy-save"])
            self.assertEqual(repository.read_state()["muted_sources"], ["noisy"])
            recovery_bytes = paths.user_state_file.read_bytes()
            with repository.connection() as connection:
                marker = connection.execute(
                    "SELECT value FROM metadata WHERE key = ?", (STATE_IMPORT_MARKER,)
                ).fetchone()["value"]
            self.assertEqual(marker, "imported")

            paths.user_state_file.write_text(
                json.dumps({"saved": ["should-not-import"]}), encoding="utf-8"
            )
            self.assertEqual(SQLiteRepository(paths).read_state()["saved"], ["legacy-save"])
            # The application never rewrites the legacy file; an external edit
            # remains visible as recovery evidence while SQLite stays authoritative.
            self.assertNotEqual(recovery_bytes, paths.user_state_file.read_bytes())
            self.assertIn(b"should-not-import", paths.user_state_file.read_bytes())
        finally:
            temporary.cleanup()

    def test_missing_or_invalid_json_does_not_erase_existing_sqlite_state(self):
        for raw in (None, "{not valid json", "[]"):
            temporary, paths = self.make_runtime()
            try:
                repository = SQLiteRepository(paths)
                repository.write_state({"saved": ["db-save"], "muted_sources": ["db-source"]})
                with repository.connection() as connection:
                    connection.execute("DELETE FROM metadata WHERE key = ?", (STATE_IMPORT_MARKER,))
                if raw is not None:
                    paths.cache_dir.mkdir(parents=True, exist_ok=True)
                    paths.user_state_file.write_text(raw, encoding="utf-8")
                else:
                    paths.user_state_file.unlink(missing_ok=True)
                repository.initialize(force=True)
                state = repository.read_state()
                self.assertEqual(state["saved"], ["db-save"])
                self.assertEqual(state["muted_sources"], ["db-source"])
                with repository.connection() as connection:
                    marker = connection.execute(
                        "SELECT value FROM metadata WHERE key = ?", (STATE_IMPORT_MARKER,)
                    ).fetchone()["value"]
                self.assertIn(marker, {"no_file", "invalid"})
            finally:
                temporary.cleanup()

    def test_state_and_archive_filters_share_one_transactional_authority(self):
        temporary, paths = self.make_runtime()
        try:
            repository = SQLiteRepository(paths)
            article = {
                "id": "story", "title": "Blender workflow", "url": "https://example.com/story",
                "summary": "Reference", "source": "Example", "source_id": "example",
                "published_at": "2026-08-01T00:00:00+00:00", "lane": "Tech & Development",
                "software_group": "Blender", "software_tags": ["Blender"], "topic_tags": [],
                "sources": [], "related": [],
            }
            repository.archive_articles([article])
            repository.write_state({"saved": ["story"], "feedback": [{"id": "story", "value": 1}]})
            self.assertEqual(repository.read_state()["saved"], ["story"])
            self.assertEqual(repository.query_archive("#is:saved")["total"], 1)
            self.assertEqual(repository.query_archive("#is:liked")["total"], 1)
        finally:
            temporary.cleanup()

    def test_runtime_paths_isolate_two_feed_services(self):
        one, paths_one = self.make_runtime()
        two, paths_two = self.make_runtime()
        try:
            first = FeedService(paths_one)
            second = FeedService(paths_two)
            first.repository.write_state({"saved": ["one"]})
            self.assertEqual(first.repository.read_state()["saved"], ["one"])
            self.assertEqual(second.repository.read_state()["saved"], [])
            self.assertNotEqual(paths_one.archive_db_file, paths_two.archive_db_file)
        finally:
            one.cleanup()
            two.cleanup()

    def test_schema_and_classifier_versions_are_independent(self):
        temporary, paths = self.make_runtime()
        try:
            service = FeedService(paths)
            now = datetime.now(timezone.utc).isoformat()
            compatible = {
                "feed_schema_version": FEED_SCHEMA_VERSION,
                "classification_revision": CLASSIFICATION_REVISION,
                "generated_at": now,
            }
            self.assertTrue(service.cached_feed_is_fresh(compatible))
            stale_classifier = {**compatible, "classification_revision": CLASSIFICATION_REVISION - 1}
            stale_schema = {**compatible, "feed_schema_version": FEED_SCHEMA_VERSION + 1}
            self.assertFalse(service.cached_feed_is_fresh(stale_classifier))
            self.assertFalse(service.cached_feed_is_fresh(stale_schema))
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
