"""Deterministic, temporary-only coverage for verified SQLite snapshots."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from cg_signal import backup as backup_module
from cg_signal.backup import (
    BackupError,
    DatabaseLease,
    DatabaseLeaseHeldError,
    RestoreError,
    SnapshotCorruptionError,
    SnapshotFormatError,
    SnapshotVerificationError,
    create_backup,
    restore_snapshot,
    verify_snapshot,
)
from cg_signal.config import RuntimePaths
from cg_signal.storage import SQLiteRepository


def _lease_child(path: str, ready: multiprocessing.synchronize.Event, release: multiprocessing.synchronize.Event) -> None:
    lease = DatabaseLease(path).acquire()
    ready.set()
    release.wait(10)
    lease.release()


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths.for_root(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def _repository(self) -> SQLiteRepository:
        return SQLiteRepository(self.paths)

    def _article(self, article_id: str = "story", title: str | None = None) -> dict[str, object]:
        return {
            "id": article_id,
            "title": title or f"Private title {article_id}",
            "url": f"https://example.invalid/{article_id}",
            "summary": "Private summary",
            "source": "Custom source",
            "source_id": "custom",
            "published_at": "2026-08-01T00:00:00+00:00",
            "lane": "Tech & Development",
            "software_group": "Blender",
            "software_tags": ["Blender"],
            "topic_tags": [],
            "sources": [],
            "related": [],
        }

    def _create_v0_database(self, *, historical: bool = False, malformed: str | None = None) -> None:
        """Create a raw persisted v0 fixture without invoking repository migration."""

        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
        self.paths.archive_db_file.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            self.paths.archive_db_file.with_name(self.paths.archive_db_file.name + suffix).unlink(missing_ok=True)
        connection = sqlite3.connect(self.paths.archive_db_file)
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
        if malformed == "check":
            articles = articles.replace("title TEXT NOT NULL", "title TEXT NOT NULL CHECK(length(title)<=1)")
        connection.execute(articles)
        connection.execute("CREATE INDEX articles_published_idx ON articles(published_at DESC)")
        connection.execute("CREATE INDEX articles_source_idx ON articles(source_id)")
        connection.execute("""
            CREATE TABLE article_state (
                article_id TEXT PRIMARY KEY, is_read INTEGER NOT NULL DEFAULT 0,
                is_saved INTEGER NOT NULL DEFAULT 0, is_archived INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '', feedback_value INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
        if not historical:
            for name, definition in (
                ("feedback_source_id", "TEXT NOT NULL DEFAULT ''"),
                ("feedback_software_tags", "TEXT NOT NULL DEFAULT '[]'"),
                ("feedback_topic_tags", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                connection.execute(f"ALTER TABLE article_state ADD COLUMN {name} {definition}")
        connection.execute("""
            CREATE TABLE sources (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, site TEXT NOT NULL DEFAULT '',
                feed TEXT NOT NULL UNIQUE, accent TEXT NOT NULL,
                item_limit INTEGER NOT NULL DEFAULT 40, enabled INTEGER NOT NULL DEFAULT 1,
                is_builtin INTEGER NOT NULL DEFAULT 0, sort_order INTEGER NOT NULL DEFAULT 1000,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        if not historical:
            connection.execute("""
                CREATE TABLE source_preferences (
                    source_id TEXT PRIMARY KEY, muted INTEGER NOT NULL DEFAULT 0,
                    reduced INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                )
            """)
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(f"PRAGMA user_version=0")
        connection.execute(
            "INSERT INTO articles(id,title,url,published_at,data_json,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?)",
            ("legacy", "L" if malformed == "check" else "Legacy", "https://legacy.invalid", "2026-01-01", "{}", "a", "b"),
        )
        connection.execute(
            "INSERT INTO article_state(article_id,is_saved,note,feedback_value,updated_at) VALUES (?,?,?,?,?)",
            ("legacy", 1, "legacy note", 1, "2026-01-02"),
        )
        connection.commit()
        connection.close()

    def _snapshot(self, root: Path | None = None) -> Path:
        if not self.paths.archive_db_file.exists():
            self._repository().initialize()
        return create_backup(self.paths, root)

    def _manifest(self, snapshot: Path) -> dict[str, object]:
        return json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))

    def _rewrite_manifest(self, snapshot: Path, value: dict[str, object]) -> None:
        (snapshot / "manifest.json").write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )

    def test_missing_database_fails_without_creating_backup_directory(self):
        with self.assertRaises(BackupError):
            create_backup(self.paths)
        self.assertFalse(self.paths.backup_dir.exists())

    def test_destination_is_root_successive_children_are_unique_and_manifest_id_matches(self):
        root = Path(self.temporary.name) / "custom-backups"
        first = self._snapshot(root)
        second = self._snapshot(root)
        self.assertEqual(first.parent, root.resolve())
        self.assertEqual(second.parent, root.resolve())
        self.assertNotEqual(first, second)
        self.assertEqual(self._manifest(first)["snapshot_id"], first.name)
        self.assertEqual(self._manifest(second)["snapshot_id"], second.name)
        self.assertEqual({path.name for path in root.iterdir()}, {first.name, second.name})

    def test_manifest_is_canonical_private_and_snapshot_has_exact_two_files(self):
        repository = self._repository()
        repository.archive_articles([self._article()])
        repository.write_state({"saved": ["story"], "notes": {"story": "private note"}})
        snapshot = self._snapshot()
        manifest = self._manifest(snapshot)
        self.assertEqual(
            set(manifest),
            {"app", "format_version", "snapshot_id", "created_at", "reason", "database", "sqlite", "table_counts", "logical_counts"},
        )
        self.assertEqual(set(manifest["database"]), {"filename", "size_bytes", "sha256"})
        self.assertEqual(set(manifest["sqlite"]), {"library_version", "user_version", "page_size", "page_count"})
        self.assertEqual({entry.name for entry in snapshot.iterdir()}, {"cg-signal.db", "manifest.json"})
        text = (snapshot / "manifest.json").read_text(encoding="utf-8")
        self.assertNotIn("private note", text)
        self.assertNotIn("Private title", text)
        self.assertNotIn("https://example.invalid", text)
        verified = verify_snapshot(snapshot)
        self.assertEqual(verified.path, snapshot.resolve())

    def test_real_committed_wal_is_captured_while_writer_connection_remains_open(self):
        repository = self._repository()
        repository.initialize()
        writer = sqlite3.connect(self.paths.archive_db_file, timeout=5)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO articles (id,title,url,published_at,data_json,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?)", ("wal", "WAL", "https://wal", "2026-01-01", "{}", "x", "x"))
        writer.commit()
        wal_path = self.paths.archive_db_file.with_name("cg-signal.db-wal")
        self.assertTrue(wal_path.exists())
        try:
            snapshot = self._snapshot()
            restored = sqlite3.connect(snapshot / "cg-signal.db")
            try:
                self.assertEqual(restored.execute("SELECT COUNT(*) FROM articles WHERE id='wal'").fetchone()[0], 1)
            finally:
                restored.close()
        finally:
            writer.close()

    def test_backup_normalizes_current_v0_in_temp_and_leaves_live_unchanged(self):
        self._create_v0_database()
        live_before = self.paths.archive_db_file.read_bytes()
        snapshot = create_backup(self.paths)
        verified = verify_snapshot(snapshot)
        self.assertEqual(verified.manifest["sqlite"]["user_version"], 1)
        self.assertEqual(live_before, self.paths.archive_db_file.read_bytes())
        live = sqlite3.connect(self.paths.archive_db_file)
        try:
            self.assertEqual(live.execute("PRAGMA user_version").fetchone()[0], 0)
        finally:
            live.close()

    def test_backup_normalizes_historical_v0_and_preserves_defaults(self):
        self._create_v0_database(historical=True)
        snapshot = create_backup(self.paths)
        verify_snapshot(snapshot)
        restored = sqlite3.connect(snapshot / "cg-signal.db")
        try:
            self.assertEqual(restored.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(restored.execute("SELECT COUNT(*) FROM source_preferences").fetchone()[0], 0)
            self.assertEqual(
                restored.execute(
                    "SELECT feedback_source_id,feedback_software_tags,feedback_topic_tags FROM article_state"
                ).fetchone(),
                ("", "[]", "[]"),
            )
        finally:
            restored.close()

    def test_malformed_v0_backup_leaves_live_and_backup_root_untouched(self):
        self._create_v0_database(malformed="check")
        live_before = self.paths.archive_db_file.read_bytes()
        with self.assertRaises(BackupError):
            create_backup(self.paths)
        self.assertEqual(live_before, self.paths.archive_db_file.read_bytes())
        self.assertEqual(list(self.paths.backup_dir.iterdir()), [])

    def test_restore_v0_live_creates_verified_v1_recovery_snapshot(self):
        candidate_root = Path(self.temporary.name) / "candidate-root"
        candidate = self._snapshot(candidate_root)
        self._create_v0_database(historical=True)
        result = restore_snapshot(self.paths, candidate)
        self.assertIsNotNone(result.recovery_snapshot)
        recovery = verify_snapshot(result.recovery_snapshot)
        self.assertEqual(recovery.manifest["sqlite"]["user_version"], 1)
        live = sqlite3.connect(self.paths.archive_db_file)
        try:
            self.assertEqual(live.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            live.close()

    def test_postinstall_corruption_of_live_v0_rolls_back_as_v1(self):
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        self._create_v0_database(historical=True)
        live = self.paths.archive_db_file
        real_replace = backup_module.os.replace

        def corrupt_stage(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
            source_path = Path(source)
            if Path(destination) == live and source_path.name.startswith(".cg-signal.db.restore-"):
                data = bytearray(source_path.read_bytes())
                data[100] ^= 0xFF
                source_path.write_bytes(data)
            return real_replace(source, destination)

        with mock.patch.object(backup_module.os, "replace", side_effect=corrupt_stage):
            with self.assertRaisesRegex(RestoreError, "rollback succeeded"):
                restore_snapshot(self.paths, candidate)
        recovered = sqlite3.connect(live)
        try:
            self.assertEqual(recovered.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(recovered.execute("SELECT note FROM article_state").fetchone()[0], "legacy note")
        finally:
            recovered.close()

    def test_raw_v0_candidate_is_rejected_before_restore(self):
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        raw_root = Path(self.temporary.name) / "raw-root"
        raw_root.mkdir()
        raw_db = raw_root / "cg-signal.db"
        self._create_v0_database(historical=True)
        raw_db.write_bytes(self.paths.archive_db_file.read_bytes())
        manifest = self._manifest(candidate)
        content = raw_db.read_bytes()
        manifest["database"]["size_bytes"] = len(content)
        manifest["database"]["sha256"] = hashlib.sha256(content).hexdigest()
        manifest["sqlite"]["user_version"] = 0
        raw_candidate = Path(self.temporary.name) / "raw-candidate"
        raw_candidate.mkdir()
        manifest["snapshot_id"] = raw_candidate.name
        (raw_candidate / "cg-signal.db").write_bytes(content)
        self._rewrite_manifest(raw_candidate, manifest)
        with self.assertRaises(SnapshotFormatError):
            verify_snapshot(raw_candidate)

    def test_correlated_transaction_snapshot_is_before_or_after_never_mixed(self):
        repository = self._repository()
        repository.initialize()
        with repository.connection() as connection:
            connection.execute("INSERT INTO articles (id,title,url,published_at,data_json,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?)", ("pair", "old", "u", "2026-01-01", "{}", "x", "x"))
            connection.execute("INSERT INTO metadata(key,value) VALUES ('pair_marker','old')")
        writer = sqlite3.connect(self.paths.archive_db_file, timeout=5, isolation_level=None)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE articles SET title='new' WHERE id='pair'")
        writer.execute("UPDATE metadata SET value='new' WHERE key='pair_marker'")
        before = self._snapshot()
        writer.commit()
        after = self._snapshot()
        writer.close()
        for snapshot, expected in ((before, "old"), (after, "new")):
            connection = sqlite3.connect(snapshot / "cg-signal.db")
            try:
                self.assertEqual(connection.execute("SELECT title FROM articles WHERE id='pair'").fetchone()[0], expected)
                self.assertEqual(connection.execute("SELECT value FROM metadata WHERE key='pair_marker'").fetchone()[0], expected)
            finally:
                connection.close()

    def test_atomic_publish_failure_cleans_all_temporary_directories(self):
        root = Path(self.temporary.name) / "publish-root"
        root.mkdir()
        real_replace = backup_module.os.replace
        calls = 0

        def fail_publish(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated publish failure")
            return real_replace(source, destination)

        with mock.patch.object(backup_module.os, "replace", side_effect=fail_publish):
            with self.assertRaises(BackupError):
                self._snapshot(root)
        self.assertEqual(list(root.iterdir()), [])

    def test_verification_failure_cleans_temporary_directory_without_publishing_child(self):
        root = Path(self.temporary.name) / "verify-root"
        with mock.patch.object(
            backup_module,
            "_verify_snapshot",
            side_effect=SnapshotVerificationError("forced verification failure"),
        ):
            with self.assertRaises(SnapshotVerificationError):
                self._snapshot(root)
        self.assertEqual(list(root.iterdir()), [])

    def test_changed_library_version_is_informational_only(self):
        snapshot = self._snapshot()
        manifest = self._manifest(snapshot)
        manifest["sqlite"]["library_version"] = "future-test-version"
        self._rewrite_manifest(snapshot, manifest)
        verify_snapshot(snapshot)

    def test_uri_read_only_verification_handles_special_unicode_path(self):
        root = Path(self.temporary.name) / "space # percent% apostrophe' 日本"
        snapshot = self._snapshot(root)
        verify_snapshot(snapshot)

    def test_pre_restore_backup_failure_leaves_live_and_stage_untouched(self):
        repository = self._repository()
        repository.write_state({"saved": ["before"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        before = self.paths.archive_db_file.read_bytes()
        with mock.patch.object(backup_module, "create_backup", side_effect=BackupError("pre failed")):
            with self.assertRaisesRegex(RestoreError, "pre-restore backup failed"):
                restore_snapshot(self.paths, candidate)
        self.assertEqual(before, self.paths.archive_db_file.read_bytes())
        self.assertEqual(list(self.paths.cache_dir.glob(".*.restore-*")), [])

    def test_stage_cleanup_failure_preserves_primary_restore_error_and_releases_lease(self):
        repository = self._repository()
        repository.write_state({"saved": ["candidate"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        self.paths.archive_db_file.unlink()
        for suffix in ("-wal", "-shm"):
            self.paths.archive_db_file.with_name(self.paths.archive_db_file.name + suffix).unlink(missing_ok=True)
        original_unlink = Path.unlink

        def fail_restore_stage(path: Path, *args: object, **kwargs: object):
            if path.name.startswith(".cg-signal.db.restore-"):
                raise PermissionError("injected stage cleanup failure")
            return original_unlink(path, *args, **kwargs)

        caught: RestoreError | None = None
        traceback_ref = None
        with mock.patch.object(
            backup_module,
            "_verify_staged_database",
            side_effect=SnapshotVerificationError("primary staged verification failure"),
        ), mock.patch.object(Path, "unlink", side_effect=fail_restore_stage):
            try:
                restore_snapshot(self.paths, candidate)
            except RestoreError as error:
                caught = error
                traceback_ref = error.__traceback__
        self.assertIsNotNone(caught)
        self.assertIn("Unable to install verified SQLite snapshot", str(caught))
        self.assertIn("primary staged verification failure", str(caught))
        self.assertTrue(any("Restore cleanup failed" in note for note in (caught.__notes__ or [])))
        self.assertIsNotNone(traceback_ref)
        lease = DatabaseLease(self.paths.database_lock_file).acquire()
        lease.release()
        # The injected cleanup failure intentionally leaves the stage for the
        # test process to remove after proving lease release.
        for stage in self.paths.cache_dir.glob(".*.restore-*"):
            original_unlink(stage)

    def test_restore_cleanup_only_failure_is_reported(self):
        repository = self._repository()
        repository.write_state({"saved": ["candidate"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        self.paths.archive_db_file.unlink()
        for suffix in ("-wal", "-shm"):
            self.paths.archive_db_file.with_name(self.paths.archive_db_file.name + suffix).unlink(missing_ok=True)
        original_unlink = Path.unlink

        def fail_restore_stage(path: Path, *args: object, **kwargs: object):
            if path.name.startswith(".cg-signal.db.restore-"):
                raise PermissionError("injected stage cleanup failure")
            return original_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", side_effect=fail_restore_stage):
            with self.assertRaisesRegex(RestoreError, "Unable to clean up restore stage"):
                restore_snapshot(self.paths, candidate)
        for stage in self.paths.cache_dir.glob(".*.restore-*"):
            original_unlink(stage)

    def test_initial_replace_failure_preserves_usable_old_database_and_recovery(self):
        repository = self._repository()
        repository.write_state({"saved": ["old"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        repository.write_state({"saved": ["new"]})
        live = self.paths.archive_db_file
        real_replace = backup_module.os.replace

        def fail_initial(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
            if Path(destination) == live:
                raise OSError("initial replace failure")
            return real_replace(source, destination)

        with mock.patch.object(backup_module.os, "replace", side_effect=fail_initial):
            with self.assertRaises(RestoreError):
                restore_snapshot(self.paths, candidate)
        self.assertEqual(repository.read_state()["saved"], ["new"])
        recovery = list(self.paths.backup_dir.glob("pre-restore-*/manifest.json"))
        self.assertEqual(len(recovery), 1)
        verify_snapshot(recovery[0].parent)

    def test_forced_post_install_failure_rolls_back_without_checkpointing_failed_db(self):
        repository = self._repository()
        repository.write_state({"saved": ["old"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        repository.write_state({"saved": ["new"]})
        original_verify = backup_module._verify_database
        original_checkpoint = backup_module._checkpoint_live
        verify_calls: list[Path] = []
        checkpoint_calls: list[Path] = []

        forced = False

        def fail_candidate_once(path: Path, manifest: dict[str, object], **kwargs: object):
            nonlocal forced
            verify_calls.append(path)
            if path == self.paths.archive_db_file and not forced:
                forced = True
                raise SnapshotVerificationError("forced post-install failure")
            return original_verify(path, manifest, **kwargs)

        def count_checkpoint(path: Path):
            checkpoint_calls.append(path)
            return original_checkpoint(path)

        with mock.patch.object(backup_module, "_verify_database", side_effect=fail_candidate_once), mock.patch.object(backup_module, "_checkpoint_live", side_effect=count_checkpoint):
            with self.assertRaisesRegex(RestoreError, "rollback succeeded"):
                restore_snapshot(self.paths, candidate)
        self.assertEqual(repository.read_state()["saved"], ["new"])
        self.assertEqual(len(checkpoint_calls), 1)

    def test_forced_rollback_replace_failure_reports_retained_recovery(self):
        repository = self._repository()
        repository.write_state({"saved": ["old"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        repository.write_state({"saved": ["new"]})
        original_verify = backup_module._verify_database
        real_replace = backup_module.os.replace

        def fail_rollback(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
            if Path(destination) == self.paths.archive_db_file and Path(source).name.startswith(".cg-signal.db.rollback-"):
                raise OSError("rollback replace failure")
            return real_replace(source, destination)

        def fail_post(path: Path, manifest: dict[str, object], **kwargs: object):
            if path == self.paths.archive_db_file and manifest["snapshot_id"] == candidate.name:
                raise SnapshotVerificationError("forced post-install failure")
            return original_verify(path, manifest, **kwargs)

        with mock.patch.object(backup_module, "_verify_database", side_effect=fail_post), mock.patch.object(backup_module.os, "replace", side_effect=fail_rollback):
            with self.assertRaisesRegex(RestoreError, "rollback failed") as raised:
                restore_snapshot(self.paths, candidate)
        self.assertIn("Recovery snapshot retained at", str(raised.exception))

    def test_actual_stage_corruption_rolls_back_existing_live_database(self):
        repository = self._repository()
        repository.write_state({"saved": ["old"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        repository.write_state({"saved": ["new"]})
        live = self.paths.archive_db_file
        real_replace = backup_module.os.replace

        def corrupt_stage(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
            source_path = Path(source)
            if Path(destination) == live and source_path.name.startswith(".cg-signal.db.restore-"):
                data = bytearray(source_path.read_bytes())
                data[100] ^= 0xFF
                source_path.write_bytes(data)
            return real_replace(source, destination)

        with mock.patch.object(backup_module.os, "replace", side_effect=corrupt_stage):
            with self.assertRaisesRegex(RestoreError, "rollback succeeded"):
                restore_snapshot(self.paths, candidate)
        self.assertEqual(repository.read_state()["saved"], ["new"])
        recovery_paths = list(self.paths.backup_dir.glob("pre-restore-*/manifest.json"))
        self.assertEqual(len(recovery_paths), 1)
        verify_snapshot(recovery_paths[0].parent)
        self.assertFalse(live.with_name("cg-signal.db-wal").exists())
        self.assertFalse(live.with_name("cg-signal.db-shm").exists())
        self.assertEqual(list(self.paths.cache_dir.glob(".*.restore-*")), [])
        self.assertEqual(list(self.paths.cache_dir.glob(".*.rollback-*")), [])

    def test_actual_stage_corruption_removes_originally_absent_database(self):
        repository = self._repository()
        repository.write_state({"saved": ["old"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        live = self.paths.archive_db_file
        live.unlink()
        sibling = self.paths.cache_dir / "keep-me.txt"
        sibling.write_text("unrelated", encoding="utf-8")
        real_replace = backup_module.os.replace

        def corrupt_stage(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
            source_path = Path(source)
            if Path(destination) == live and source_path.name.startswith(".cg-signal.db.restore-"):
                data = bytearray(source_path.read_bytes())
                data[100] ^= 0xFF
                source_path.write_bytes(data)
            return real_replace(source, destination)

        with mock.patch.object(backup_module.os, "replace", side_effect=corrupt_stage):
            with self.assertRaisesRegex(RestoreError, "originally absent database and sidecars were removed"):
                restore_snapshot(self.paths, candidate)
        self.assertFalse(live.exists())
        self.assertFalse(live.is_symlink())
        self.assertFalse(live.with_name("cg-signal.db-wal").exists())
        self.assertFalse(live.with_name("cg-signal.db-shm").exists())
        self.assertEqual(sibling.read_text(encoding="utf-8"), "unrelated")
        self.assertEqual(list(self.paths.backup_dir.glob("pre-restore-*")), [])

    def test_identity_mismatch_refuses_existing_live_rollback_replacement(self):
        repository = self._repository()
        repository.write_state({"saved": ["old"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        repository.write_state({"saved": ["new"]})
        live = self.paths.archive_db_file
        real_replace = backup_module.os.replace

        def replace_with_unexpected_identity(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
            source_path = Path(source)
            result = real_replace(source, destination)
            if Path(destination) == live and source_path.name.startswith(".cg-signal.db.restore-"):
                unexpected = live.with_name("unexpected.db")
                unexpected.write_bytes(b"unexpected")
                real_replace(unexpected, live)
            return result

        with mock.patch.object(backup_module.os, "replace", side_effect=replace_with_unexpected_identity):
            with self.assertRaisesRegex(RestoreError, "rollback failed") as raised:
                restore_snapshot(self.paths, candidate)
        self.assertIn("Recovery snapshot retained at", str(raised.exception))
        self.assertEqual(live.read_bytes(), b"unexpected")

    def test_identity_mismatch_refuses_absent_cleanup(self):
        repository = self._repository()
        repository.write_state({"saved": ["old"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        live = self.paths.archive_db_file
        live.unlink()
        real_replace = backup_module.os.replace

        def replace_with_unexpected_identity(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
            source_path = Path(source)
            result = real_replace(source, destination)
            if Path(destination) == live and source_path.name.startswith(".cg-signal.db.restore-"):
                unexpected = live.with_name("unexpected.db")
                unexpected.write_bytes(b"unexpected")
                real_replace(unexpected, live)
            return result

        with mock.patch.object(backup_module.os, "replace", side_effect=replace_with_unexpected_identity):
            with self.assertRaisesRegex(RestoreError, "failed database may remain"):
                restore_snapshot(self.paths, candidate)
        self.assertEqual(live.read_bytes(), b"unexpected")
        self.assertEqual(list(self.paths.backup_dir.glob("pre-restore-*")), [])

    def test_checksum_consistent_wrong_columns_snapshot_is_rejected_before_recovery(self):
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        database = candidate / "cg-signal.db"
        connection = sqlite3.connect(database)
        connection.execute("ALTER TABLE articles ADD COLUMN unexpected TEXT NOT NULL DEFAULT ''")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.commit()
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        connection.close()
        manifest = self._manifest(candidate)
        content = database.read_bytes()
        manifest["database"]["size_bytes"] = len(content)
        manifest["database"]["sha256"] = hashlib.sha256(content).hexdigest()
        manifest["sqlite"]["page_count"] = page_count
        self._rewrite_manifest(candidate, manifest)
        with self.assertRaises(SnapshotFormatError):
            verify_snapshot(candidate)
        before = self.paths.archive_db_file.read_bytes()
        with self.assertRaises(SnapshotFormatError):
            restore_snapshot(self.paths, candidate)
        self.assertEqual(before, self.paths.archive_db_file.read_bytes())
        self.assertEqual(list(self.paths.backup_dir.glob("pre-restore-*")), [])

    def test_restore_over_stale_sidecars_removes_only_wal_shm_and_closes_handles(self):
        repository = self._repository()
        repository.write_state({"saved": ["old"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        (self.paths.archive_db_file.with_name("cg-signal.db-wal")).write_bytes(b"stale")
        (self.paths.archive_db_file.with_name("cg-signal.db-shm")).write_bytes(b"stale")
        restore_snapshot(self.paths, candidate)
        self.assertFalse(self.paths.archive_db_file.with_name("cg-signal.db-wal").exists())
        self.assertFalse(self.paths.archive_db_file.with_name("cg-signal.db-shm").exists())

    def test_restore_missing_live_database_succeeds_without_recovery(self):
        repository = self._repository()
        repository.write_state({"saved": ["old"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        self.paths.archive_db_file.unlink()
        result = restore_snapshot(self.paths, candidate)
        self.assertEqual(result.target, self.paths.archive_db_file)
        self.assertIsNone(result.recovery_snapshot)
        self.assertEqual(list(self.paths.backup_dir.glob("pre-restore-*")), [])

    def test_cross_process_lease_blocks_restore_until_released(self):
        repository = self._repository()
        repository.write_state({"saved": ["old"]})
        candidate = self._snapshot(Path(self.temporary.name) / "candidate-root")
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        release = context.Event()
        process = context.Process(target=_lease_child, args=(str(self.paths.database_lock_file), ready, release))
        process.start()
        self.assertTrue(ready.wait(10))
        try:
            with self.assertRaisesRegex(BackupError, "stop-dashboard"):
                restore_snapshot(self.paths, candidate)
        finally:
            release.set()
            process.join(10)
        self.assertEqual(process.exitcode, 0)
        restore_snapshot(self.paths, candidate)

    def test_manifest_tampering_and_unexpected_entries_are_rejected(self):
        snapshot = self._snapshot()
        clean = self._manifest(snapshot)
        cases = []
        oversized = "{" + "x" * (64 * 1024) + "}"
        (snapshot / "manifest.json").write_text(oversized, encoding="utf-8")
        cases.append(SnapshotFormatError)
        self.assertRaises(SnapshotFormatError, verify_snapshot, snapshot)
        self._rewrite_manifest(snapshot, clean)
        with (snapshot / "manifest.json").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        duplicate = '{"app":"cg-signal","app":"cg-signal"}'
        (snapshot / "manifest.json").write_text(duplicate, encoding="utf-8")
        self.assertRaises(SnapshotFormatError, verify_snapshot, snapshot)
        for extra in ("alias",):
            tampered = dict(clean)
            tampered[extra] = 1
            self._rewrite_manifest(snapshot, tampered)
            self.assertRaises(SnapshotFormatError, verify_snapshot, snapshot)
        self._rewrite_manifest(snapshot, clean)
        (snapshot / "unexpected").write_text("x", encoding="utf-8")
        self.assertRaises(SnapshotFormatError, verify_snapshot, snapshot)

    def test_checksum_size_count_integrity_schema_and_nonregular_tampering_are_rejected(self):
        snapshot = self._snapshot()
        clean = self._manifest(snapshot)
        for key, value in (("size_bytes", clean["database"]["size_bytes"] + 1), ("sha256", "0" * 64)):
            tampered = json.loads(json.dumps(clean))
            tampered["database"][key] = value
            self._rewrite_manifest(snapshot, tampered)
            self.assertRaises(SnapshotCorruptionError, verify_snapshot, snapshot)
        self._rewrite_manifest(snapshot, clean)
        tampered = json.loads(json.dumps(clean))
        tampered["table_counts"]["articles"] += 1
        self._rewrite_manifest(snapshot, tampered)
        self.assertRaises(SnapshotVerificationError, verify_snapshot, snapshot)
        self._rewrite_manifest(snapshot, clean)
        tampered = json.loads(json.dumps(clean))
        tampered["sqlite"]["user_version"] = 2
        self._rewrite_manifest(snapshot, tampered)
        self.assertRaises(SnapshotFormatError, verify_snapshot, snapshot)
        self._rewrite_manifest(snapshot, clean)
        db = snapshot / "cg-signal.db"
        content = bytearray(db.read_bytes())
        content[100] ^= 0xFF
        db.write_bytes(content)
        tampered = json.loads(json.dumps(clean))
        tampered["database"]["size_bytes"] = len(content)
        tampered["database"]["sha256"] = hashlib.sha256(content).hexdigest()
        self._rewrite_manifest(snapshot, tampered)
        self.assertRaises(SnapshotVerificationError, verify_snapshot, snapshot)

    def test_symlinked_or_nonregular_snapshot_entries_are_rejected(self):
        snapshot = self._snapshot()
        manifest = snapshot / "manifest.json"
        manifest.unlink()
        manifest.mkdir()
        self.assertRaises(SnapshotFormatError, verify_snapshot, snapshot)


class BackupCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths.for_root(Path(self.temporary.name))
        SQLiteRepository(self.paths).write_state({"saved": ["story"]})

    def tearDown(self):
        self.temporary.cleanup()

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        import cg_signal.http as http

        output, error = io.StringIO(), io.StringIO()
        with mock.patch.object(http.RuntimePaths, "for_root", return_value=self.paths), mock.patch.object(sys, "argv", ["server.py", *argv]), redirect_stdout(output), redirect_stderr(error):
            try:
                http.main()
            except SystemExit as exc:
                return int(exc.code or 0), output.getvalue(), error.getvalue()
        return 0, output.getvalue(), error.getvalue()

    def test_cli_preview_is_read_only_and_reports_paths(self):
        snapshot = create_backup(self.paths)
        before = self.paths.archive_db_file.read_bytes()
        code, output, error = self._run(["restore", str(snapshot)])
        self.assertEqual(code, 0)
        self.assertIn("Created:", output)
        self.assertIn("Schema:", output)
        self.assertIn("Counts:", output)
        self.assertIn("Target:", output)
        self.assertIn("--confirm", output)
        self.assertEqual(error, "")
        self.assertEqual(before, self.paths.archive_db_file.read_bytes())

    def test_cli_backup_and_operational_or_argparse_errors(self):
        code, output, _ = self._run(["backup", "--destination", str(Path(self.temporary.name) / "root")])
        self.assertEqual(code, 0)
        self.assertIn("Backup verified:", output)
        code, _, _ = self._run(["restore", str(Path(self.temporary.name) / "missing")])
        self.assertEqual(code, 1)
        code, _, _ = self._run(["backup", "--port", "4311"])
        self.assertEqual(code, 2)
        code, _, _ = self._run(["restore", "missing", "--no-browser"])
        self.assertEqual(code, 2)

    def test_cli_backup_missing_live_database_is_exit_one(self):
        self.paths.archive_db_file.unlink()
        code, _, error = self._run(["backup"])
        self.assertEqual(code, 1)
        self.assertIn("database is missing", error)

    def test_cli_backup_normalizes_v0_live_database(self):
        BackupTests._create_v0_database(self)
        code, output, error = self._run(["backup"])
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertIn("Backup verified:", output)
        snapshot = Path(output.split(":", 1)[1].strip())
        self.assertEqual(verify_snapshot(snapshot).manifest["sqlite"]["user_version"], 1)
        live = sqlite3.connect(self.paths.archive_db_file)
        try:
            self.assertEqual(live.execute("PRAGMA user_version").fetchone()[0], 0)
        finally:
            live.close()

    def test_cli_all_invalid_maintenance_and_serve_combinations_exit_two(self):
        root = str(Path(self.temporary.name) / "root")
        cases = [
            ["backup", "--confirm"],
            ["backup", "unexpected-snapshot"],
            ["restore"],
            ["restore", "missing", "--destination", root],
            ["backup", "--port", "4311"],
            ["backup", "--no-browser"],
            ["backup", "--print-source-revision"],
            ["restore", "missing", "--port", "4311"],
            ["restore", "missing", "--no-browser"],
            ["restore", "missing", "--print-source-revision"],
            ["--destination", root],
            ["--confirm"],
            ["only-a-snapshot-path"],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                code, _, _ = self._run(arguments)
                self.assertEqual(code, 2)

    def test_cli_confirm_lock_refusal_is_exit_one_and_success_reports_target(self):
        snapshot = create_backup(self.paths)
        lease = DatabaseLease(self.paths.database_lock_file).acquire()
        try:
            code, _, error = self._run(["restore", str(snapshot), "--confirm"])
            self.assertEqual(code, 1)
            self.assertIn("stop-dashboard", error)
        finally:
            lease.release()
        code, output, _ = self._run(["restore", str(snapshot), "--confirm"])
        self.assertEqual(code, 0)
        self.assertIn("Restore complete:", output)
        recovery_line = next(line for line in output.splitlines() if line.startswith("Recovery snapshot:"))
        recovery = Path(recovery_line.split(":", 1)[1].strip())
        self.assertTrue(recovery.is_dir())
        verify_snapshot(recovery)

    def test_cli_server_start_refuses_held_database_lease_before_server_construction(self):
        lease = DatabaseLease(self.paths.database_lock_file).acquire()
        try:
            code, _, error = self._run(["--no-browser"])
            self.assertEqual(code, 1)
            self.assertIn("database lease", error)
        finally:
            lease.release()

    def test_server_close_cleanup_preserves_primary_error_and_releases_lease(self):
        import cg_signal.http as http

        server = mock.Mock()
        server.serve_forever.side_effect = RuntimeError("primary serve failure")
        server.server_close.side_effect = OSError("injected server_close failure")
        caught: RuntimeError | None = None
        traceback_ref = None
        with mock.patch.object(http, "DashboardServer", return_value=server):
            try:
                self._run(["--no-browser"])
            except RuntimeError as error:
                caught = error
                traceback_ref = error.__traceback__
        self.assertIsNotNone(caught)
        self.assertEqual(str(caught), "primary serve failure")
        self.assertTrue(any("Dashboard cleanup failed" in note for note in (caught.__notes__ or [])))
        self.assertIsNotNone(traceback_ref)
        lease = DatabaseLease(self.paths.database_lock_file).acquire()
        lease.release()

    def test_server_cleanup_only_failure_is_reported(self):
        import cg_signal.http as http

        server = mock.Mock()
        server.server_close.side_effect = OSError("injected server_close failure")
        with mock.patch.object(http, "DashboardServer", return_value=server):
            with self.assertRaisesRegex(RuntimeError, "Dashboard cleanup failed"):
                self._run(["--no-browser"])
        lease = DatabaseLease(self.paths.database_lock_file).acquire()
        lease.release()


if __name__ == "__main__":
    unittest.main()
