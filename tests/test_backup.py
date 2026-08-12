import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cg_signal import backup as backup_module
from cg_signal.backup import (
    BackupError,
    SnapshotFormatError,
    RestoreError,
    create_backup,
    format_preview,
    restore_snapshot,
    verify_snapshot,
)
from cg_signal.config import RuntimePaths
from cg_signal.storage import (
    _DDL_ARTICLE_STATE_V1,
    _DDL_ARTICLES,
    _DDL_ARTICLES_PUBLISHED_INDEX,
    _DDL_ARTICLES_SOURCE_INDEX,
    _DDL_METADATA,
    _DDL_SOURCES,
    _DDL_SOURCE_PREFERENCES_V1,
    SQLiteRepository,
)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = RuntimePaths.for_root(self.root).with_cache_dir(self.root / "cache")
        self.repository = SQLiteRepository(self.paths)
        self.repository.initialize()
        self.repository.record_articles([{
            "id": "story", "title": "Saved story", "url": "https://example.test/story",
            "summary": "summary", "source": "Example", "source_id": "example",
            "published_at": "2026-01-01T00:00:00+00:00",
        }])
        self.repository.write_state({"saved": ["story"], "muted_sources": ["quiet"]})

    def tearDown(self):
        self.temporary.cleanup()

    def test_new_backup_is_format2_schema2_with_exact_counts(self):
        snapshot = create_backup(self.paths, self.root / "backups")
        verification = verify_snapshot(snapshot)
        self.assertEqual(verification.manifest["format_version"], 2)
        self.assertEqual(verification.manifest["sqlite"]["user_version"], 2)
        self.assertEqual(set(verification.logical_counts), {"articles", "saved", "configured_sources", "muted_sources"})
        self.assertEqual(verification.logical_counts["saved"], 1)
        self.assertEqual(verification.logical_counts["muted_sources"], 1)

    def test_backup_normalizes_exact_v1_in_temporary_copy_only(self):
        self.paths.history_db_file.unlink()
        connection = sqlite3.connect(self.paths.history_db_file)
        for ddl in (_DDL_ARTICLES, _DDL_ARTICLES_PUBLISHED_INDEX, _DDL_ARTICLES_SOURCE_INDEX, _DDL_ARTICLE_STATE_V1, _DDL_SOURCES, _DDL_SOURCE_PREFERENCES_V1, _DDL_METADATA):
            connection.execute(ddl)
        connection.execute("PRAGMA user_version=1")
        connection.execute("INSERT INTO articles(id,title,url,published_at,data_json,first_seen_at,last_seen_at) VALUES ('a','a','u','d','{}','f','l')")
        connection.execute("INSERT INTO article_state(article_id,is_saved,note,updated_at) VALUES ('a',1,'discard','x')")
        connection.commit(); connection.close()
        before = self.paths.history_db_file.read_bytes()
        snapshot = create_backup(self.paths, self.root / "backups")
        self.assertEqual(before, self.paths.history_db_file.read_bytes())
        self.assertEqual(verify_snapshot(snapshot).manifest["format_version"], 2)

    def _format1_snapshot(self) -> Path:
        source = self.root / "v1-source.db"
        connection = sqlite3.connect(source)
        for ddl in (_DDL_ARTICLES, _DDL_ARTICLES_PUBLISHED_INDEX, _DDL_ARTICLES_SOURCE_INDEX, _DDL_ARTICLE_STATE_V1, _DDL_SOURCES, _DDL_SOURCE_PREFERENCES_V1, _DDL_METADATA):
            connection.execute(ddl)
        connection.execute("PRAGMA user_version=1")
        connection.execute("INSERT INTO articles(id,title,url,published_at,data_json,first_seen_at,last_seen_at) VALUES ('a','a','u','d','{}','f','l')")
        connection.execute("INSERT INTO article_state(article_id,is_saved,note,updated_at) VALUES ('a',1,'legacy','x')")
        connection.execute("INSERT INTO source_preferences(source_id,muted,updated_at) VALUES ('quiet',1,'x')")
        connection.commit(); connection.close()
        root = self.root / "format1"; root.mkdir()
        database = root / "cg-signal.db"; database.write_bytes(source.read_bytes())
        connection = sqlite3.connect(database)
        metadata = backup_module._sqlite_metadata(connection)
        table_counts = backup_module._table_counts(connection)
        logical_counts = backup_module._logical_counts_v1(connection)
        connection.close()
        manifest = backup_module._manifest(
            snapshot_id="format1", created_at=backup_module._utc_text(), reason="manual",
            database_size=database.stat().st_size, database_sha256=backup_module._sha256(database),
            sqlite_metadata=metadata, table_counts=table_counts, logical_counts=logical_counts,
        )
        manifest["format_version"] = 1
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return root

    def test_exact_format1_verifies_and_restores_as_v2(self):
        snapshot = self._format1_snapshot()
        verification = verify_snapshot(snapshot)
        self.assertEqual(verification.manifest["format_version"], 1)
        preview = format_preview(verification, self.paths.history_db_file)
        self.assertIn("Schema 1 installs as schema 2", preview)
        result = restore_snapshot(self.paths, snapshot)
        self.assertEqual(result.target, self.paths.history_db_file)
        connection = sqlite3.connect(self.paths.history_db_file)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
        self.assertEqual(connection.execute("SELECT article_id FROM article_state").fetchall(), [("a",)])
        self.assertEqual([row[1] for row in connection.execute("PRAGMA table_info(article_state)")], ["article_id", "is_saved", "updated_at"])
        connection.close()

    def test_crossed_manifest_versions_and_raw_v0_are_rejected(self):
        snapshot = create_backup(self.paths, self.root / "backups")
        manifest_path = snapshot / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["format_version"] = 1
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(SnapshotFormatError):
            verify_snapshot(snapshot)

        crossed = self._format1_snapshot()
        crossed_manifest_path = crossed / "manifest.json"
        crossed_manifest = json.loads(crossed_manifest_path.read_text())
        crossed_manifest["format_version"] = 2
        crossed_manifest_path.write_text(json.dumps(crossed_manifest))
        with self.assertRaises(SnapshotFormatError):
            verify_snapshot(crossed)

        raw_v0 = self.root / "raw-v0"
        raw_v0.mkdir()
        raw_db = raw_v0 / "cg-signal.db"
        sqlite3.connect(raw_db).close()
        raw_manifest = json.loads((snapshot / "manifest.json").read_text())
        raw_manifest["snapshot_id"] = raw_v0.name
        raw_manifest["database"]["size_bytes"] = raw_db.stat().st_size
        raw_manifest["database"]["sha256"] = backup_module._sha256(raw_db)
        raw_connection = sqlite3.connect(raw_db, uri=False)
        raw_manifest["sqlite"]["page_count"] = int(raw_connection.execute("PRAGMA page_count").fetchone()[0])
        raw_manifest["sqlite"]["user_version"] = 0
        raw_manifest["table_counts"] = {}
        raw_manifest["logical_counts"] = {key: 0 for key in raw_manifest["logical_counts"]}
        raw_connection.close()
        (raw_v0 / "manifest.json").write_text(json.dumps(raw_manifest), encoding="utf-8")
        with self.assertRaises(SnapshotFormatError):
            verify_snapshot(raw_v0)

    def test_forced_post_install_failure_rolls_back_verified_v2(self):
        candidate = create_backup(self.paths, self.root / "candidate")
        self.repository.write_state({"saved": [], "muted_sources": []})
        original = backup_module._verify_database
        calls = {"count": 0}
        def fail_once(path, manifest, **kwargs):
            if Path(path) == self.paths.history_db_file and calls["count"] == 0:
                calls["count"] += 1
                raise SnapshotFormatError("forced post-install failure")
            return original(path, manifest, **kwargs)
        with mock.patch.object(backup_module, "_verify_database", side_effect=fail_once):
            with self.assertRaises(RestoreError):
                restore_snapshot(self.paths, candidate)
        self.assertEqual(self.repository.read_state()["saved"], [])

    def test_missing_database_backup_fails(self):
        self.paths.history_db_file.unlink()
        with self.assertRaises(BackupError):
            create_backup(self.paths, self.root / "backups")


if __name__ == "__main__":
    unittest.main()
