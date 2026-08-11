import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cg_signal.config import CLASSIFICATION_REVISION, FEED_SCHEMA_VERSION, RuntimePaths
from cg_signal.feeds import FeedService
from cg_signal.storage import (
    SQLiteRepository,
    STATE_IMPORT_MARKER,
    STORAGE_SCHEMA_VERSION,
    StorageSchemaError,
    validate_current_schema,
)


class StorageMigrationTests(unittest.TestCase):
    def make_runtime(self):
        temporary = tempfile.TemporaryDirectory()
        paths = RuntimePaths.for_root(Path(temporary.name))
        return temporary, paths

    def _create_known_schema(
        self,
        paths: RuntimePaths,
        *,
        version: int = 0,
        feedback_count: int = 3,
        include_metadata: bool = True,
        historical: bool = False,
        ddl_variant: str | None = None,
        article_variant: str = "valid",
        state_order: str = "current",
        indexes: bool = True,
    ) -> sqlite3.Connection:
        """Build one of the finite persisted states used by migration tests."""

        paths.cache_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(paths.archive_db_file)
        articles = """
            CREATE TABLE articles (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '', published_at TEXT NOT NULL,
                lane TEXT NOT NULL DEFAULT '', software_group TEXT NOT NULL DEFAULT '',
                software_tags TEXT NOT NULL DEFAULT '[]', topic_tags TEXT NOT NULL DEFAULT '[]',
                sources_text TEXT NOT NULL DEFAULT '', search_text TEXT NOT NULL DEFAULT '',
                data_json TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
            )
        """
        if article_variant == "missing_published_at":
            articles = articles.replace("published_at TEXT NOT NULL,\n", "")
        elif article_variant == "nullable_published_at":
            articles = articles.replace("published_at TEXT NOT NULL", "published_at TEXT")
        elif article_variant == "extra_column":
            articles = articles.replace("last_seen_at TEXT NOT NULL", "last_seen_at TEXT NOT NULL, extra TEXT NOT NULL DEFAULT ''")
        if ddl_variant == "check":
            articles = articles.replace("title TEXT NOT NULL", "title TEXT NOT NULL CHECK(length(title)<=1)")
        elif ddl_variant == "collate":
            articles = articles.replace("title TEXT NOT NULL", "title TEXT NOT NULL COLLATE NOCASE")
        elif ddl_variant == "not_null_conflict":
            articles = articles.replace("title TEXT NOT NULL", "title TEXT NOT NULL ON CONFLICT IGNORE")
        connection.execute(articles)
        if indexes:
            connection.execute("CREATE INDEX articles_published_idx ON articles(published_at DESC)")
            if ddl_variant == "index_collate":
                connection.execute("CREATE INDEX articles_source_idx ON articles(source_id COLLATE NOCASE)")
            else:
                connection.execute("CREATE INDEX articles_source_idx ON articles(source_id)")
        if state_order == "updated_before_feedback":
            state = """
                CREATE TABLE article_state (
                    article_id TEXT PRIMARY KEY, is_read INTEGER NOT NULL DEFAULT 0,
                    is_saved INTEGER NOT NULL DEFAULT 0, is_archived INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '', feedback_value INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    feedback_source_id TEXT NOT NULL DEFAULT '',
                    feedback_software_tags TEXT NOT NULL DEFAULT '[]',
                    feedback_topic_tags TEXT NOT NULL DEFAULT '[]'
                )
            """
            feedback_count = 0
        else:
            state = """
                CREATE TABLE article_state (
                    article_id TEXT PRIMARY KEY, is_read INTEGER NOT NULL DEFAULT 0,
                    is_saved INTEGER NOT NULL DEFAULT 0, is_archived INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '', feedback_value INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
            """
        connection.execute(state)
        feedback_columns = (
            ("feedback_source_id", "TEXT NOT NULL DEFAULT ''"),
            ("feedback_software_tags", "TEXT NOT NULL DEFAULT '[]'"),
            ("feedback_topic_tags", "TEXT NOT NULL DEFAULT '[]'"),
        )
        for name, definition in feedback_columns[:feedback_count]:
            connection.execute(f"ALTER TABLE article_state ADD COLUMN {name} {definition}")
        sources = """
            CREATE TABLE sources (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, site TEXT NOT NULL DEFAULT '',
                feed TEXT NOT NULL UNIQUE, accent TEXT NOT NULL,
                item_limit INTEGER NOT NULL DEFAULT 40, enabled INTEGER NOT NULL DEFAULT 1,
                is_builtin INTEGER NOT NULL DEFAULT 0, sort_order INTEGER NOT NULL DEFAULT 1000,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """
        if ddl_variant == "unique_conflict":
            sources = sources.replace("feed TEXT NOT NULL UNIQUE", "feed TEXT NOT NULL UNIQUE ON CONFLICT REPLACE")
        connection.execute(sources)
        if not historical:
            connection.execute("""
                CREATE TABLE source_preferences (
                    source_id TEXT PRIMARY KEY, muted INTEGER NOT NULL DEFAULT 0,
                    reduced INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                )
            """)
        if include_metadata or historical:
            connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(f"PRAGMA user_version={int(version)}")
        connection.commit()
        return connection

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

    def test_empty_database_migrates_to_exact_v1(self):
        temporary, paths = self.make_runtime()
        try:
            repository = SQLiteRepository(paths)
            repository.initialize()
            with repository.connection() as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], STORAGE_SCHEMA_VERSION)
                validate_current_schema(connection)
        finally:
            temporary.cleanup()

    def test_exact_historical_four_table_state_migrates_without_data_loss(self):
        temporary, paths = self.make_runtime()
        try:
            connection = self._create_known_schema(paths, historical=True, feedback_count=0)
            connection.execute(
                "INSERT INTO articles(id,title,url,published_at,data_json,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?)",
                ("article", "Title", "https://example.invalid/a", "2026-01-01", "{}", "a", "b"),
            )
            connection.execute(
                "INSERT INTO article_state(article_id,is_saved,note,feedback_value,updated_at) VALUES (?,?,?,?,?)",
                ("article", 1, "note", 1, "2026-01-02"),
            )
            connection.execute(
                "INSERT INTO sources(id,name,feed,accent,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                ("custom", "Custom", "https://example.invalid/feed", "#fff", "a", "b"),
            )
            connection.commit()
            connection.close()
            SQLiteRepository(paths).initialize()
            migrated = sqlite3.connect(paths.archive_db_file)
            try:
                row = migrated.execute(
                    "SELECT title FROM articles WHERE id='article'"
                ).fetchone()
                state = migrated.execute(
                    "SELECT is_saved,note,feedback_value,feedback_source_id,feedback_software_tags,feedback_topic_tags "
                    "FROM article_state WHERE article_id='article'"
                ).fetchone()
                self.assertEqual(row[0], "Title")
                self.assertEqual(state, (1, "note", 1, "", "[]", "[]"))
                self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(migrated.execute("SELECT COUNT(*) FROM source_preferences").fetchone()[0], 0)
            finally:
                migrated.close()
        finally:
            temporary.cleanup()

    def test_exact_current_shape_v0_is_stamped_without_data_loss(self):
        temporary, paths = self.make_runtime()
        try:
            connection = self._create_known_schema(paths, version=0, feedback_count=3)
            connection.execute(
                "INSERT INTO metadata(key,value) VALUES ('marker','kept')"
            )
            connection.commit()
            connection.close()
            SQLiteRepository(paths).initialize()
            migrated = sqlite3.connect(paths.archive_db_file)
            try:
                self.assertEqual(migrated.execute("SELECT value FROM metadata WHERE key='marker'").fetchone()[0], "kept")
                self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], 1)
            finally:
                migrated.close()
        finally:
            temporary.cleanup()

    def test_interrupted_feedback_prefixes_migrate(self):
        for feedback_count in (0, 1, 2):
            temporary, paths = self.make_runtime()
            try:
                connection = self._create_known_schema(paths, feedback_count=feedback_count)
                connection.close()
                SQLiteRepository(paths).initialize()
                migrated = sqlite3.connect(paths.archive_db_file)
                try:
                    self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], 1)
                    columns = {row[1] for row in migrated.execute("PRAGMA table_info(article_state)")}
                    self.assertTrue({"feedback_source_id", "feedback_software_tags", "feedback_topic_tags"} <= columns)
                finally:
                    migrated.close()
            finally:
                temporary.cleanup()

    def test_wrong_version_zero_shape_is_rejected_before_migration_ddl(self):
        temporary, paths = self.make_runtime()
        try:
            connection = self._create_known_schema(paths, article_variant="missing_published_at", indexes=False)
            connection.close()
            with self.assertRaises(StorageSchemaError):
                SQLiteRepository(paths).initialize()
            untouched = sqlite3.connect(paths.archive_db_file)
            try:
                self.assertEqual(untouched.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(
                    untouched.execute(
                        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_autoindex_%'"
                    ).fetchall(),
                    [],
                )
            finally:
                untouched.close()
        finally:
            temporary.cleanup()

    def test_v1_missing_wrong_and_extra_columns_are_rejected(self):
        for variant in ("missing_published_at", "nullable_published_at", "extra_column"):
            temporary, paths = self.make_runtime()
            try:
                connection = self._create_known_schema(
                    paths,
                    version=1,
                    article_variant=variant,
                    indexes=variant != "missing_published_at",
                )
                connection.close()
                with self.assertRaises(StorageSchemaError):
                    SQLiteRepository(paths).initialize()
                untouched = sqlite3.connect(paths.archive_db_file)
                try:
                    self.assertEqual(untouched.execute("PRAGMA user_version").fetchone()[0], 1)
                finally:
                    untouched.close()
            finally:
                temporary.cleanup()

    def test_future_version_is_rejected_without_downgrade(self):
        temporary, paths = self.make_runtime()
        try:
            paths.cache_dir.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(paths.archive_db_file)
            connection.execute("PRAGMA user_version=2")
            connection.commit()
            connection.close()
            with self.assertRaises(StorageSchemaError):
                SQLiteRepository(paths).initialize()
            untouched = sqlite3.connect(paths.archive_db_file)
            try:
                self.assertEqual(untouched.execute("PRAGMA user_version").fetchone()[0], 2)
            finally:
                untouched.close()
        finally:
            temporary.cleanup()

    def test_schema_rejects_extra_objects_and_wrong_indexes(self):
        for mutation in ("extra_table", "extra_view", "extra_trigger", "wrong_sort"):
            temporary, paths = self.make_runtime()
            try:
                connection = self._create_known_schema(paths, version=1)
                if mutation == "extra_table":
                    connection.execute("CREATE TABLE extra_table(id TEXT)")
                elif mutation == "extra_view":
                    connection.execute("CREATE VIEW extra_view AS SELECT 1")
                elif mutation == "extra_trigger":
                    connection.execute("CREATE TRIGGER extra_trigger AFTER INSERT ON articles BEGIN SELECT 1; END")
                else:
                    connection.execute("DROP INDEX articles_source_idx")
                    connection.execute("CREATE INDEX articles_source_idx ON articles(source_id DESC)")
                connection.commit()
                connection.close()
                with self.assertRaises(StorageSchemaError):
                    SQLiteRepository(paths).initialize()
            finally:
                temporary.cleanup()

    def test_v0_allowlisted_ddl_rejects_semantically_similar_variants(self):
        for variant in ("check", "collate", "not_null_conflict", "unique_conflict", "index_collate"):
            temporary, paths = self.make_runtime()
            try:
                connection = self._create_known_schema(paths, ddl_variant=variant)
                connection.close()
                with self.assertRaises(StorageSchemaError):
                    SQLiteRepository(paths).initialize()
                untouched = sqlite3.connect(paths.archive_db_file)
                try:
                    self.assertEqual(untouched.execute("PRAGMA user_version").fetchone()[0], 0)
                finally:
                    untouched.close()
            finally:
                temporary.cleanup()

    def test_v1_allowlisted_ddl_rejects_semantically_similar_variants(self):
        for variant in ("check", "collate", "not_null_conflict", "unique_conflict", "index_collate"):
            temporary, paths = self.make_runtime()
            try:
                connection = self._create_known_schema(paths, version=1, ddl_variant=variant)
                connection.close()
                with self.assertRaises(StorageSchemaError):
                    SQLiteRepository(paths).initialize()
            finally:
                temporary.cleanup()

    def test_legitimate_article_state_column_orders_validate(self):
        for state_order in ("current", "updated_before_feedback"):
            temporary, paths = self.make_runtime()
            try:
                connection = self._create_known_schema(paths, version=1, state_order=state_order)
                validate_current_schema(connection)
                connection.close()
            finally:
                temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
