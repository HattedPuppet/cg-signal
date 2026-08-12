import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cg_signal.config import RuntimePaths
from cg_signal.storage import (
    STORAGE_SCHEMA_VERSION,
    SQLiteRepository,
    StorageSchemaError,
    _DDL_ARTICLE_STATE_V1,
    _DDL_ARTICLE_STATE_V1_UPDATED_FIRST,
    _DDL_ARTICLES,
    _DDL_ARTICLES_PUBLISHED_INDEX,
    _DDL_ARTICLES_SOURCE_INDEX,
    _DDL_METADATA,
    _DDL_SOURCES,
    _DDL_SOURCE_PREFERENCES_V1,
    migrate_storage_schema,
    validate_current_schema,
)


class StorageMigrationTests(unittest.TestCase):
    def _v1_database(self, path: Path, *, updated_first: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        for ddl in (
            _DDL_ARTICLES,
            _DDL_ARTICLES_PUBLISHED_INDEX,
            _DDL_ARTICLES_SOURCE_INDEX,
            _DDL_ARTICLE_STATE_V1_UPDATED_FIRST if updated_first else _DDL_ARTICLE_STATE_V1,
            _DDL_SOURCES,
            _DDL_SOURCE_PREFERENCES_V1,
            _DDL_METADATA,
        ):
            connection.execute(ddl)
        connection.execute("PRAGMA user_version=1")
        connection.execute(
            "INSERT INTO articles(id,title,url,published_at,data_json,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?)",
            ("article", "Article", "https://example.test/article", "2026-01-01", "{}", "first", "last"),
        )
        connection.execute("INSERT INTO sources(id,name,feed,accent,created_at,updated_at) VALUES ('custom','Custom','https://example.test/feed','#fff','x','x')")
        connection.execute("INSERT INTO article_state(article_id,is_saved,is_archived,note,updated_at) VALUES ('article',1,1,'legacy','saved-at')")
        connection.execute("INSERT INTO article_state(article_id,is_saved,updated_at) VALUES ('unsaved',0,'discard')")
        connection.execute("INSERT INTO source_preferences(source_id,muted,reduced,updated_at) VALUES ('custom',1,1,'muted-at')")
        connection.execute("INSERT INTO source_preferences(source_id,muted,reduced,updated_at) VALUES ('quiet',0,1,'discard')")
        connection.execute("INSERT INTO metadata(key,value) VALUES ('user_state_json_imported','imported')")
        connection.execute("INSERT INTO metadata(key,value) VALUES ('user_state_updated_at','2026-01-02T03:04:05+00:00')")
        connection.commit()
        return connection

    def test_fresh_empty_bootstrap_is_exact_v2(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = RuntimePaths.for_root(Path(temporary))
            repository = SQLiteRepository(paths)
            repository.initialize()
            with repository.connection() as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
                validate_current_schema(connection)
                self.assertEqual([row[1] for row in connection.execute("PRAGMA table_info(article_state)")], ["article_id", "is_saved", "updated_at"])
                self.assertEqual([row[1] for row in connection.execute("PRAGMA table_info(source_preferences)")], ["source_id", "muted", "updated_at"])

    def test_both_exact_v1_layouts_migrate_saved_and_muted_only(self):
        for updated_first in (False, True):
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "cg-signal.db"
                connection = self._v1_database(path, updated_first=updated_first)
                migrate_storage_schema(connection)
                validate_current_schema(connection)
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], STORAGE_SCHEMA_VERSION)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute("SELECT id, name, feed FROM sources WHERE id='custom'").fetchone(),
                    ("custom", "Custom", "https://example.test/feed"),
                )
                self.assertEqual(connection.execute("SELECT article_id FROM article_state").fetchall(), [("article",)])
                self.assertEqual(
                    connection.execute("SELECT source_id, muted FROM source_preferences").fetchall(),
                    [("custom", 1)],
                )
                self.assertEqual(connection.execute("SELECT 1 FROM metadata WHERE key='user_state_json_imported'").fetchone(), None)
                self.assertEqual(
                    connection.execute("SELECT value FROM metadata WHERE key='state_updated_at'").fetchone(),
                    ("2026-01-02T03:04:05+00:00",),
                )
                self.assertEqual(connection.execute("SELECT 1 FROM metadata WHERE key='user_state_updated_at'").fetchone(), None)
                connection.close()

    def test_exact_v2_is_validated_without_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = RuntimePaths.for_root(Path(temporary))
            repository = SQLiteRepository(paths)
            repository.initialize()
            before = paths.history_db_file.read_bytes()
            connection = sqlite3.connect(paths.history_db_file)
            try:
                migrate_storage_schema(connection)
            finally:
                connection.close()
            self.assertEqual(before, paths.history_db_file.read_bytes())

    def test_populated_v0_future_and_malformed_v1_reject_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cg-signal.db"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE unexpected(value TEXT)")
            connection.commit()
            before = path.read_bytes()
            with self.assertRaises(StorageSchemaError):
                migrate_storage_schema(connection)
            self.assertEqual(before, path.read_bytes())
            connection.close()

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cg-signal.db"
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA user_version=99")
            connection.commit()
            with self.assertRaises(StorageSchemaError):
                migrate_storage_schema(connection)
            connection.close()

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cg-signal.db"
            connection = self._v1_database(path)
            connection.execute("ALTER TABLE article_state ADD COLUMN extra TEXT")
            connection.commit()
            before = path.read_bytes()
            with self.assertRaises(StorageSchemaError):
                migrate_storage_schema(connection)
            self.assertEqual(before, path.read_bytes())
            connection.close()

    def test_migration_rolls_back_if_v2_validation_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cg-signal.db"
            connection = self._v1_database(path)
            original = validate_current_schema
            try:
                import cg_signal.storage as storage
                storage.validate_current_schema = lambda *_args, **_kwargs: (_ for _ in ()).throw(StorageSchemaError("forced"))
                with self.assertRaises(StorageSchemaError):
                    migrate_storage_schema(connection)
            finally:
                storage.validate_current_schema = original
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT note FROM article_state WHERE article_id='article'").fetchone()[0], "legacy")
            connection.close()

    def test_invalid_v1_state_timestamp_is_discarded_for_normal_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cg-signal.db"
            connection = self._v1_database(path)
            connection.execute(
                "UPDATE metadata SET value='not-a-timestamp' WHERE key='user_state_updated_at'"
            )
            connection.commit()
            migrate_storage_schema(connection)
            self.assertIsNone(
                connection.execute("SELECT value FROM metadata WHERE key='state_updated_at'").fetchone()
            )
            connection.close()


if __name__ == "__main__":
    unittest.main()
