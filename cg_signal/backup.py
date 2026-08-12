"""Verified SQLite snapshot backup and restore support.

The dashboard keeps its durable state in one SQLite database, while feeds,
thumbnails, and PID files remain disposable runtime artifacts.  This module
deliberately treats a backup as a *database snapshot*, never as a copy of the
live ``.db``, ``-wal``, or ``-shm`` files.

Only the Python standard library is used here.  The public helpers are small
enough for the command-line entrypoint and for isolated tests to use without
starting the HTTP server.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import uuid
from typing import Any

from .config import RuntimePaths
from .storage import (
    STORAGE_SCHEMA_VERSION,
    StorageSchemaError,
    _validate_v1_schema,
    migrate_storage_schema,
    validate_current_schema,
)


APP_NAME = "cg-signal"
MANIFEST_FORMAT_VERSION = 2
DATABASE_FILENAME = "cg-signal.db"
BACKUP_BUSY_TIMEOUT_SECONDS = 5.0

_REQUIRED_LOGICAL_KEYS = {
    "articles",
    "saved",
    "configured_sources",
    "muted_sources",
}
_FORMAT1_LOGICAL_KEYS = {
    "articles",
    "saved",
    "nonempty_notes",
    "configured_sources",
    "muted_sources",
}


class BackupError(RuntimeError):
    """An operational backup or restore error."""


class SnapshotVerificationError(BackupError):
    """A snapshot is not a supported, self-consistent snapshot."""


class SnapshotCorruptionError(SnapshotVerificationError):
    """The snapshot bytes differ from the manifest checksum or size."""


class SnapshotFormatError(SnapshotVerificationError):
    """The snapshot layout or manifest is not the supported format."""


class DatabaseLeaseError(BackupError):
    """The runtime database lease could not be acquired or released."""


class DatabaseLeaseHeldError(DatabaseLeaseError):
    """Another dashboard process currently owns the database lease."""


class RestoreError(BackupError):
    """A verified restore could not be completed."""


@dataclass(frozen=True)
class SnapshotVerification:
    """A verified snapshot and its privacy-safe manifest."""

    path: Path
    manifest: dict[str, Any]

    @property
    def database_path(self) -> Path:
        return self.path / DATABASE_FILENAME

    @property
    def logical_counts(self) -> dict[str, int]:
        return dict(self.manifest.get("logical_counts", {}))


@dataclass(frozen=True)
class RestoreResult:
    """Completed restore target and retained automatic recovery snapshot."""

    target: Path
    recovery_snapshot: Path | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime | None = None) -> str:
    current = value or _utc_now()
    # ``Z`` is unambiguous in a human-readable manifest and is accepted by
    # ``datetime.fromisoformat`` after replacing it with ``+00:00``.
    return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_id() -> str:
    return uuid.uuid4().hex[:10]


def _snapshot_name(reason: str) -> str:
    timestamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    prefix = "pre-restore-" if reason == "pre_restore" else ""
    return f"{prefix}{timestamp}-{_safe_id()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _db_connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        # ``mode=ro`` avoids accidentally creating a file while verifying a
        # candidate stage.  ``Path.as_uri`` percent-encodes spaces, ``#``,
        # apostrophes, percent signs, and non-ASCII path components correctly.
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=BACKUP_BUSY_TIMEOUT_SECONDS,
        )
    else:
        connection = sqlite3.connect(path, timeout=BACKUP_BUSY_TIMEOUT_SECONDS)
    connection.execute(f"PRAGMA busy_timeout={int(BACKUP_BUSY_TIMEOUT_SECONDS * 1000)}")
    return connection


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        name = str(row[0])
        counts[name] = int(connection.execute(f"SELECT COUNT(*) FROM {_identifier(name)}").fetchone()[0])
    return counts


def _logical_counts(connection: sqlite3.Connection) -> dict[str, int]:
    # Keep these queries explicit.  They are a stable, privacy-safe summary of
    # the useful state in the database and never expose IDs or text values.
    return {
        "articles": int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]),
        "saved": int(
            connection.execute(
                "SELECT COUNT(*) FROM article_state WHERE is_saved = 1"
            ).fetchone()[0]
        ),
        "configured_sources": int(connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]),
        "muted_sources": int(
            connection.execute(
                "SELECT COUNT(*) FROM source_preferences WHERE muted = 1"
            ).fetchone()[0]
        ),
    }


def _logical_counts_v1(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "articles": int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]),
        "saved": int(connection.execute("SELECT COUNT(*) FROM article_state WHERE is_saved = 1").fetchone()[0]),
        "nonempty_notes": int(connection.execute(
            "SELECT COUNT(*) FROM article_state WHERE note IS NOT NULL AND trim(note) <> ''"
        ).fetchone()[0]),
        "configured_sources": int(connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]),
        "muted_sources": int(connection.execute("SELECT COUNT(*) FROM source_preferences WHERE muted = 1").fetchone()[0]),
    }


def _sqlite_metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    journal_row = connection.execute("PRAGMA journal_mode").fetchone()
    journal_mode = str(journal_row[0]).lower() if journal_row else ""
    return {
        "library_version": sqlite3.sqlite_version,
        "user_version": user_version,
        "page_size": page_size,
        "page_count": page_count,
        "journal_mode": journal_mode,
    }


def _check_integrity(connection: sqlite3.Connection) -> None:
    integrity = [str(row[0]).lower() for row in connection.execute("PRAGMA integrity_check").fetchall()]
    if integrity != ["ok"]:
        detail = "; ".join(integrity) or "no result"
        raise SnapshotVerificationError(f"SQLite integrity_check failed: {detail}")
    foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign:
        raise SnapshotVerificationError("SQLite foreign_key_check reported violations.")


def _validate_schema(
    sqlite_metadata: dict[str, Any],
    logical_counts: dict[str, int],
    *,
    expected_version: int = STORAGE_SCHEMA_VERSION,
    expected_keys: set[str] = _REQUIRED_LOGICAL_KEYS,
) -> None:
    if sqlite_metadata["user_version"] != expected_version:
        raise SnapshotFormatError(
            f"Unsupported SQLite schema version {sqlite_metadata['user_version']}; "
            f"supported version is {expected_version}."
        )
    if sqlite_metadata["journal_mode"] != "delete":
        raise SnapshotFormatError("SQLite snapshot must use journal_mode=DELETE.")
    if set(logical_counts) != expected_keys:
        raise SnapshotFormatError("Logical count summary is incomplete.")


def _collect_database_summary(path: Path, *, read_only: bool) -> tuple[dict[str, Any], dict[str, int], dict[str, int]]:
    if not path.is_file() or path.is_symlink():
        raise BackupError(f"SQLite database does not exist: {path}")
    connection: sqlite3.Connection | None = None
    try:
        connection = _db_connect(path, read_only=read_only)
        # A source connection must not accidentally participate in a write
        # transaction while sqlite3.Connection.backup() is running.
        if read_only:
            connection.execute("PRAGMA query_only=ON")
        sqlite_metadata = _sqlite_metadata(connection)
        try:
            validate_current_schema(connection)
        except StorageSchemaError as exc:
            raise SnapshotFormatError(str(exc)) from exc
        table_counts = _table_counts(connection)
        logical_counts = _logical_counts(connection)
        _validate_schema(sqlite_metadata, logical_counts)
        _check_integrity(connection)
        return sqlite_metadata, table_counts, logical_counts
    except sqlite3.Error as exc:
        raise BackupError(f"Unable to inspect SQLite database {path}: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def _manifest(
    *,
    snapshot_id: str,
    created_at: str,
    reason: str,
    database_size: int,
    database_sha256: str,
    sqlite_metadata: dict[str, Any],
    table_counts: dict[str, int],
    logical_counts: dict[str, int],
) -> dict[str, Any]:
    if reason not in {"manual", "pre_restore"}:
        raise ValueError("Backup reason must be 'manual' or 'pre_restore'.")
    # Do not add paths, URLs, IDs, private text, titles, or article content here.
    # Counts and SQLite structural metadata are sufficient for verification.
    sqlite_summary = {
        "library_version": sqlite_metadata["library_version"],
        "user_version": sqlite_metadata["user_version"],
        "page_size": sqlite_metadata["page_size"],
        "page_count": sqlite_metadata["page_count"],
    }
    return {
        "app": APP_NAME,
        "format_version": MANIFEST_FORMAT_VERSION,
        "snapshot_id": snapshot_id,
        "created_at": created_at,
        "reason": reason,
        "database": {
            "filename": DATABASE_FILENAME,
            "size_bytes": int(database_size),
            "sha256": database_sha256,
        },
        "sqlite": sqlite_summary,
        "table_counts": dict(table_counts),
        "logical_counts": dict(logical_counts),
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + f".tmp-{_safe_id()}")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _assert_snapshot_entries(snapshot_dir: Path) -> None:
    try:
        entries = list(snapshot_dir.iterdir())
    except OSError as exc:
        raise SnapshotVerificationError(f"Unable to read snapshot directory: {snapshot_dir}") from exc
    names = {entry.name for entry in entries}
    expected = {DATABASE_FILENAME, "manifest.json"}
    if names != expected:
        unexpected = sorted(names - expected)
        missing = sorted(expected - names)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if unexpected:
            detail.append("unexpected " + ", ".join(unexpected))
        raise SnapshotFormatError("Snapshot directory must contain exactly cg-signal.db and manifest.json (" + "; ".join(detail) + ").")
    for name in expected:
        entry = snapshot_dir / name
        if entry.is_symlink() or not entry.is_file():
            raise SnapshotFormatError(f"Snapshot entry is not a regular file: {name}")


def _manifest_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SnapshotFormatError(f"Manifest field {label} must be a non-negative integer.")
    return value


class _DuplicateManifestKey(ValueError):
    pass


def _manifest_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateManifestKey(key)
        result[key] = value
    return result


def _load_manifest(snapshot_dir: Path, *, expected_snapshot_id: str | None = None) -> dict[str, Any]:
    manifest_path = snapshot_dir / "manifest.json"
    try:
        if manifest_path.stat().st_size > 64 * 1024:
            raise SnapshotFormatError("Snapshot manifest exceeds 64 KiB.")
        raw = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_manifest_object,
        )
    except SnapshotFormatError:
        raise
    except (_DuplicateManifestKey, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotFormatError("Snapshot manifest is missing or invalid JSON.") from exc
    if not isinstance(raw, dict):
        raise SnapshotFormatError("Snapshot manifest must be an object.")
    if set(raw) != {
        "app", "format_version", "snapshot_id", "created_at", "reason",
        "database", "sqlite", "table_counts", "logical_counts",
    }:
        raise SnapshotFormatError("Snapshot manifest has unexpected or missing fields.")
    format_value = raw["format_version"]
    if (
        raw["app"] != APP_NAME
        or isinstance(format_value, bool)
        or not isinstance(format_value, int)
        or format_value not in {1, MANIFEST_FORMAT_VERSION}
    ):
        raise SnapshotFormatError("Unsupported snapshot manifest format.")
    format_version = format_value
    snapshot_id = raw["snapshot_id"]
    if (
        not isinstance(snapshot_id, str)
        or snapshot_id != (expected_snapshot_id or snapshot_dir.name)
        or not snapshot_id
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in snapshot_id)
    ):
        raise SnapshotFormatError("Snapshot id is invalid.")
    created_at = raw["created_at"]
    if not isinstance(created_at, str):
        raise SnapshotFormatError("Snapshot created_at is invalid.")
    try:
        parsed_created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotFormatError("Snapshot created_at is invalid.") from exc
    if (
        parsed_created.tzinfo is None
        or parsed_created.utcoffset() is None
        or parsed_created.utcoffset().total_seconds() != 0
        or not created_at.endswith("Z")
    ):
        raise SnapshotFormatError("Snapshot created_at must be UTC.")
    if raw["reason"] not in {"manual", "pre_restore"}:
        raise SnapshotFormatError("Snapshot reason is invalid.")
    database = raw["database"]
    if not isinstance(database, dict) or set(database) != {"filename", "size_bytes", "sha256"}:
        raise SnapshotFormatError("Snapshot database metadata is invalid.")
    if database["filename"] != DATABASE_FILENAME:
        raise SnapshotFormatError("Snapshot database filename is invalid.")
    size = _manifest_int(database["size_bytes"], "database.size_bytes")
    sha256 = database["sha256"]
    if not isinstance(sha256, str) or len(sha256) != 64 or any(
        character not in "0123456789abcdefABCDEF" for character in sha256
    ):
        raise SnapshotFormatError("Snapshot database checksum is invalid.")
    sqlite_summary = raw["sqlite"]
    if not isinstance(sqlite_summary, dict) or set(sqlite_summary) != {
        "library_version", "user_version", "page_size", "page_count"
    }:
        raise SnapshotFormatError("Snapshot SQLite metadata is invalid.")
    library_version = sqlite_summary["library_version"]
    if not isinstance(library_version, str) or not library_version:
        raise SnapshotFormatError("Snapshot SQLite library version is invalid.")
    user_version = _manifest_int(sqlite_summary["user_version"], "sqlite.user_version")
    page_size = _manifest_int(sqlite_summary["page_size"], "sqlite.page_size")
    page_count = _manifest_int(sqlite_summary["page_count"], "sqlite.page_count")
    expected_version = 1 if format_version == 1 else STORAGE_SCHEMA_VERSION
    if user_version != expected_version:
        raise SnapshotFormatError("Snapshot storage schema version is unsupported.")
    table_counts = raw["table_counts"]
    logical_counts = raw["logical_counts"]
    if not isinstance(table_counts, dict) or not isinstance(logical_counts, dict):
        raise SnapshotFormatError("Snapshot row-count summaries are missing.")
    normalized_tables: dict[str, int] = {}
    for name, count in table_counts.items():
        if not isinstance(name, str) or not name or name.startswith("sqlite_"):
            raise SnapshotFormatError("Snapshot table-count key is invalid.")
        normalized_tables[name] = _manifest_int(count, f"table_counts.{name}")
    normalized_logical: dict[str, int] = {}
    for name, count in logical_counts.items():
        if not isinstance(name, str):
            raise SnapshotFormatError("Snapshot logical-count key is invalid.")
        normalized_logical[name] = _manifest_int(count, f"logical_counts.{name}")
    expected_keys = _FORMAT1_LOGICAL_KEYS if format_version == 1 else _REQUIRED_LOGICAL_KEYS
    if set(normalized_logical) != expected_keys:
        raise SnapshotFormatError("Snapshot logical-count summary is incomplete.")
    raw["database"] = {
        "filename": DATABASE_FILENAME,
        "size_bytes": size,
        "sha256": sha256.lower(),
    }
    raw["sqlite"] = {
        "library_version": library_version,
        "user_version": user_version,
        "page_size": page_size,
        "page_count": page_count,
    }
    raw["table_counts"] = normalized_tables
    raw["logical_counts"] = normalized_logical
    return raw


def _verify_database(path: Path, manifest: dict[str, Any], *, check_sidecars: bool = False) -> None:
    if not path.is_file() or path.is_symlink():
        raise SnapshotVerificationError(f"SQLite database is missing: {path}")
    if check_sidecars:
        for suffix in ("-wal", "-shm"):
            sidecar = path.with_name(path.name + suffix)
            if sidecar.is_symlink() or sidecar.exists():
                raise SnapshotFormatError(f"Unexpected SQLite sidecar: {path.name + suffix}")
    expected_db = manifest["database"]
    actual_size = path.stat().st_size
    expected_size = int(expected_db["size_bytes"])
    if actual_size != expected_size:
        raise SnapshotCorruptionError(
            f"Snapshot database size mismatch (expected {expected_size}, got {actual_size})."
        )
    actual_sha = _sha256(path)
    if not hmac.compare_digest(actual_sha, str(expected_db["sha256"]).lower()):
        raise SnapshotCorruptionError("Snapshot database checksum mismatch.")
    connection: sqlite3.Connection | None = None
    try:
        connection = _db_connect(path, read_only=True)
        connection.execute("PRAGMA query_only=ON")
        sqlite_metadata = _sqlite_metadata(connection)
        try:
            is_v1 = int(manifest["format_version"]) == 1
            if is_v1:
                _validate_v1_schema(connection)
            else:
                validate_current_schema(connection)
        except StorageSchemaError as exc:
            raise SnapshotFormatError(str(exc)) from exc
        table_counts = _table_counts(connection)
        logical_counts = _logical_counts_v1(connection) if is_v1 else _logical_counts(connection)
        _validate_schema(
            sqlite_metadata,
            logical_counts,
            expected_version=1 if is_v1 else STORAGE_SCHEMA_VERSION,
            expected_keys=_FORMAT1_LOGICAL_KEYS if is_v1 else _REQUIRED_LOGICAL_KEYS,
        )
        _check_integrity(connection)
    except sqlite3.Error as exc:
        raise SnapshotVerificationError(f"Unable to verify SQLite snapshot: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
    expected_sqlite = manifest["sqlite"]
    for key in ("user_version", "page_size", "page_count"):
        if sqlite_metadata[key] != int(expected_sqlite[key]):
            raise SnapshotVerificationError(f"Snapshot SQLite {key} does not match its manifest.")
    if table_counts != manifest["table_counts"]:
        raise SnapshotVerificationError("Snapshot table row counts do not match its manifest.")
    if logical_counts != manifest["logical_counts"]:
        raise SnapshotVerificationError("Snapshot logical counts do not match its manifest.")


def _verify_snapshot(
    snapshot: Path | str,
    *,
    expected_snapshot_id: str | None = None,
) -> SnapshotVerification:
    supplied_dir = Path(snapshot).expanduser()
    if supplied_dir.is_symlink():
        raise SnapshotFormatError(f"Snapshot directory must not be a symlink: {supplied_dir}")
    snapshot_dir = supplied_dir.resolve()
    if not snapshot_dir.is_dir():
        raise SnapshotFormatError(f"Snapshot directory does not exist: {snapshot_dir}")
    _assert_snapshot_entries(snapshot_dir)
    manifest = _load_manifest(snapshot_dir, expected_snapshot_id=expected_snapshot_id)
    _verify_database(snapshot_dir / DATABASE_FILENAME, manifest, check_sidecars=True)
    return SnapshotVerification(snapshot_dir, manifest)


def verify_snapshot(snapshot: Path | str) -> SnapshotVerification:
    """Verify a snapshot and return its privacy-safe manifest.

    The returned object contains only structural metadata and counts; the
    database itself is never loaded into memory as article content.
    """

    return _verify_snapshot(snapshot)


def create_backup(
    paths: RuntimePaths,
    destination: Path | str | None = None,
    *,
    reason: str = "manual",
) -> Path:
    """Create and atomically publish a verified SQLite snapshot.

    ``destination`` is the backup root.  A unique timestamped child is always
    created beneath that root.  When omitted, ``RuntimePaths.backup_dir`` is
    used.
    """

    if reason not in {"manual", "pre_restore"}:
        raise BackupError("Backup reason must be 'manual' or 'pre_restore'.")
    live_db = paths.history_db_file
    if not live_db.is_file() or live_db.is_symlink():
        raise BackupError(f"Live SQLite database is missing: {live_db}")
    root = paths.backup_dir if destination is None else Path(destination).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    snapshot_id = _snapshot_name(reason)
    final_dir = root / snapshot_id
    if final_dir.exists():
        raise BackupError(f"Backup destination already exists: {final_dir}")
    parent = root
    temporary_dir = parent / f".{final_dir.name}.tmp-{_safe_id()}"
    if temporary_dir.exists():
        raise BackupError(f"Temporary backup path already exists: {temporary_dir}")
    temporary_dir.mkdir()
    temporary_db = temporary_dir / DATABASE_FILENAME
    source: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        try:
            source = _db_connect(live_db, read_only=True)
            source.execute("PRAGMA query_only=ON")
            destination_connection = _db_connect(temporary_db)
            # Connection.backup() reads the source's committed state, including
            # committed WAL pages, without copying live sidecar files.
            source.backup(destination_connection)
            destination_connection.commit()
            source.close()
            source = None
            # Published schema 1 databases are normalized only in the temporary
            # copy; the live read-only source is never migrated in place.
            migrate_storage_schema(destination_connection)
            destination_connection.execute("PRAGMA journal_mode=DELETE")
            destination_connection.commit()
        except StorageSchemaError as exc:
            raise BackupError(f"Unable to normalize live SQLite database snapshot: {exc}") from exc
        except sqlite3.Error as exc:
            raise BackupError(f"Unable to snapshot live SQLite database: {exc}") from exc
        finally:
            if destination_connection is not None:
                destination_connection.close()
                destination_connection = None
            if source is not None:
                source.close()
                source = None

        sqlite_metadata, table_counts, logical_counts = _collect_database_summary(
            temporary_db, read_only=True
        )
        database_size = temporary_db.stat().st_size
        database_sha = _sha256(temporary_db)
        manifest = _manifest(
            snapshot_id=snapshot_id,
            created_at=_utc_text(),
            reason=reason,
            database_size=database_size,
            database_sha256=database_sha,
            sqlite_metadata=sqlite_metadata,
            table_counts=table_counts,
            logical_counts=logical_counts,
        )
        _write_manifest(temporary_dir / "manifest.json", manifest)
        # Verify the complete temporary directory before it becomes visible.
        _verify_snapshot(temporary_dir, expected_snapshot_id=snapshot_id)
        if final_dir.exists():
            raise BackupError(f"Backup destination already exists: {final_dir}")
        os.replace(temporary_dir, final_dir)
        return final_dir
    except BackupError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"Unable to publish SQLite backup: {exc}") from exc
    finally:
        if source is not None:
            source.close()
        if destination_connection is not None:
            destination_connection.close()
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir, ignore_errors=True)


def _checkpoint_live(path: Path) -> None:
    if not path.exists():
        return
    connection: sqlite3.Connection | None = None
    try:
        connection = _db_connect(path)
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        # A busy writer can leave a non-zero busy count.  Treat that as an
        # operational failure rather than deleting sidecars that may still be
        # in use.
        if result and len(result[0]) >= 1 and int(result[0][0]) != 0:
            raise RestoreError("SQLite WAL checkpoint was busy; restore was not applied.")
    except sqlite3.Error as exc:
        raise RestoreError(f"Unable to checkpoint live SQLite database: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def _remove_safe_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.is_symlink():
            raise RestoreError(f"Refusing to remove unexpected SQLite sidecar: {sidecar}")
        if not sidecar.exists():
            continue
        if not sidecar.is_file():
            raise RestoreError(f"Refusing to remove unexpected SQLite sidecar: {sidecar}")
        try:
            sidecar.unlink()
        except OSError as exc:
            raise RestoreError(f"Unable to remove SQLite sidecar {sidecar}: {exc}") from exc


def _regular_file_identity(path: Path) -> tuple[int, int]:
    """Return the identity of a regular, non-symlink file for rollback safety."""

    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise RestoreError(f"Installed SQLite database is missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RestoreError(f"Refusing to operate on a non-regular SQLite database: {path}")
    return int(info.st_dev), int(info.st_ino)


def _require_file_identity(path: Path, identity: tuple[int, int]) -> None:
    if _regular_file_identity(path) != identity:
        raise RestoreError(
            "The installed SQLite database changed unexpectedly; refusing rollback cleanup."
        )


def _verify_staged_database(path: Path, manifest: dict[str, Any]) -> None:
    # A stage lives beside the live database, so its filename is intentionally
    # not required to be ``cg-signal.db``.  Its bytes and all structural checks
    # still have to match the candidate manifest exactly.
    _verify_database(path, manifest, check_sidecars=True)


def _normalize_staged_v1(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Migrate a verified v1 restore stage and derive its exact v2 manifest."""

    if int(manifest.get("format_version", 0)) != 1:
        return manifest
    connection: sqlite3.Connection | None = None
    try:
        connection = _db_connect(path)
        migrate_storage_schema(connection)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.commit()
    except (sqlite3.Error, StorageSchemaError) as exc:
        raise RestoreError(f"Unable to migrate staged schema1 snapshot to schema2: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
    sqlite_metadata, table_counts, logical_counts = _collect_database_summary(path, read_only=True)
    return _manifest(
        snapshot_id=str(manifest["snapshot_id"]),
        created_at=str(manifest["created_at"]),
        reason=str(manifest["reason"]),
        database_size=path.stat().st_size,
        database_sha256=_sha256(path),
        sqlite_metadata=sqlite_metadata,
        table_counts=table_counts,
        logical_counts=logical_counts,
    )


def _rollback(
    *,
    live_db: Path,
    recovery: SnapshotVerification,
    installed_identity: tuple[int, int],
) -> None:
    rollback_stage = live_db.with_name(f".{live_db.name}.rollback-{_safe_id()}")
    try:
        shutil.copyfile(recovery.database_path, rollback_stage)
        _verify_staged_database(rollback_stage, recovery.manifest)
        # Never open or inspect the failed installed database.  Confirm that it
        # is still the exact artifact produced by the initial replacement.
        _require_file_identity(live_db, installed_identity)
        _remove_safe_sidecars(live_db)
        _require_file_identity(live_db, installed_identity)
        os.replace(rollback_stage, live_db)
        _verify_database(live_db, recovery.manifest, check_sidecars=True)
    finally:
        try:
            rollback_stage.unlink()
        except FileNotFoundError:
            pass


def _remove_failed_install(
    *,
    live_db: Path,
    installed_identity: tuple[int, int],
) -> None:
    """Restore the originally absent state after a failed post-install check."""

    _require_file_identity(live_db, installed_identity)
    _remove_safe_sidecars(live_db)
    _require_file_identity(live_db, installed_identity)
    try:
        live_db.unlink()
    except FileNotFoundError as exc:
        raise RestoreError(f"Installed SQLite database disappeared unexpectedly: {live_db}") from exc
    except OSError as exc:
        raise RestoreError(f"Unable to remove failed installed SQLite database: {exc}") from exc
    if live_db.exists() or live_db.is_symlink():
        raise RestoreError(f"Failed installed SQLite database remains: {live_db}")
    for suffix in ("-wal", "-shm"):
        sidecar = live_db.with_name(live_db.name + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise RestoreError(f"Failed installed SQLite sidecar remains: {sidecar}")


def restore_snapshot(
    paths: RuntimePaths,
    snapshot: Path | str,
) -> RestoreResult:
    """Install a previously verified SQLite snapshot after explicit CLI confirmation."""

    candidate = _verify_snapshot(snapshot)
    lease = DatabaseLease(paths.database_lock_file)
    try:
        lease.acquire()
    except DatabaseLeaseHeldError as exc:
        raise BackupError(
            "The CG Signal database is in use by another process. "
            "Stop the dashboard with stop-dashboard.ps1, then retry restore."
        ) from exc
    live_db = paths.history_db_file
    recovery: SnapshotVerification | None = None
    stage = live_db.with_name(f".{live_db.name}.restore-{_safe_id()}")
    installed_identity: tuple[int, int] | None = None
    install_manifest = candidate.manifest
    try:
        # A symlink (including a broken one) is not an absent database.  Let
        # create_backup reject it before any replacement can follow its target.
        if live_db.exists() or live_db.is_symlink():
            try:
                recovery_path = create_backup(paths, reason="pre_restore")
                recovery = _verify_snapshot(recovery_path)
            except (BackupError, OSError, sqlite3.Error) as exc:
                raise RestoreError(f"Automatic pre-restore backup failed; restore aborted: {exc}") from exc
        live_db.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(candidate.database_path, stage)
            _verify_staged_database(stage, candidate.manifest)
            install_manifest = _normalize_staged_v1(stage, candidate.manifest)
            _verify_staged_database(stage, install_manifest)
            _checkpoint_live(live_db)
            _remove_safe_sidecars(live_db)
            installed_identity = _regular_file_identity(stage)
            os.replace(stage, live_db)
        except RestoreError:
            raise
        except (OSError, sqlite3.Error, SnapshotVerificationError) as exc:
            raise RestoreError(f"Unable to install verified SQLite snapshot: {exc}") from exc
        try:
            _verify_database(live_db, install_manifest, check_sidecars=True)
        except Exception as install_error:
            if installed_identity is None:
                raise RestoreError(
                    f"Post-restore verification failed ({install_error}); installed artifact identity unavailable."
                ) from install_error
            if recovery is None:
                try:
                    _remove_failed_install(live_db=live_db, installed_identity=installed_identity)
                except Exception as cleanup_error:
                    raise RestoreError(
                        f"Post-restore verification failed ({install_error}); originally absent database "
                        f"could not be removed ({cleanup_error}); failed database may remain."
                    ) from install_error
                raise RestoreError(
                    f"Post-restore verification failed ({install_error}); originally absent database "
                    "and sidecars were removed."
                ) from install_error
            try:
                _rollback(
                    live_db=live_db,
                    recovery=recovery,
                    installed_identity=installed_identity,
                )
            except Exception as rollback_error:
                raise RestoreError(
                    f"Post-restore verification failed ({install_error}); "
                    f"rollback failed ({rollback_error}). Recovery snapshot retained at {recovery.path}."
                ) from install_error
            raise RestoreError(
                f"Post-restore verification failed ({install_error}); "
                f"rollback succeeded. Recovery snapshot retained at {recovery.path}."
            ) from install_error
        return RestoreResult(live_db, recovery.path if recovery else None)
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_error: BaseException | None = None
        try:
            stage.unlink()
        except FileNotFoundError:
            pass
        except BaseException as exc:
            cleanup_error = exc
        finally:
            try:
                lease.release()
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        # A restore failure is the authoritative result.  Cleanup failures
        # must not mask it, but remain visible as an exception note.  A
        # cleanup failure on an otherwise successful restore is still an
        # operational error for the caller.
        if cleanup_error is not None:
            if primary_error is not None:
                try:
                    primary_error.add_note(f"Restore cleanup failed: {cleanup_error}")
                except Exception:
                    pass
            else:
                raise RestoreError(f"Unable to clean up restore stage: {cleanup_error}") from cleanup_error

class DatabaseLease:
    """An OS-backed exclusive lease for the live SQLite database.

    The lock file is intentionally separate from SQLite.  It prevents a
    dashboard startup and a confirmed restore from racing while leaving online
    read/backup operations possible for an already-running process.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self._handle: Any | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> "DatabaseLease":
        if self._handle is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise DatabaseLeaseHeldError(
                        f"Database lease is held: {self.path}"
                    ) from exc
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise DatabaseLeaseHeldError(
                        f"Database lease is held: {self.path}"
                    ) from exc
            self._handle = handle
            return self
        except DatabaseLeaseError:
            handle.close()
            raise
        except Exception:
            handle.close()
            raise

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            handle.close()

    def __enter__(self) -> "DatabaseLease":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.release()


def format_preview(verification: SnapshotVerification, target: Path) -> str:
    """Render the stable, privacy-safe restore preview used by the CLI."""

    manifest = verification.manifest
    counts = manifest.get("logical_counts", {})
    keys = ("articles", "saved", "configured_sources", "muted_sources")
    if int(manifest.get("format_version", MANIFEST_FORMAT_VERSION)) == 1:
        keys = ("articles", "saved", "nonempty_notes", "configured_sources", "muted_sources")
    count_text = ", ".join(f"{key}={counts[key]}" for key in keys)
    schema_note = (
        "Schema 1 installs as schema 2; obsolete state is discarded.\n"
        if int(manifest.get("format_version", MANIFEST_FORMAT_VERSION)) == 1 else ""
    )
    return (
        f"Snapshot verified\n"
        f"Created: {manifest['created_at']}\n"
        f"Schema: {manifest['sqlite']['user_version']}\n"
        f"Counts: {count_text}\n"
        f"{schema_note}"
        f"Target: {target}\n"
        f"No changes made. Re-run: python server.py restore \"{verification.path}\" --confirm"
    )


__all__ = [
    "APP_NAME",
    "MANIFEST_FORMAT_VERSION",
    "DATABASE_FILENAME",
    "BackupError",
    "SnapshotVerificationError",
    "SnapshotCorruptionError",
    "SnapshotFormatError",
    "DatabaseLeaseError",
    "DatabaseLeaseHeldError",
    "RestoreError",
    "SnapshotVerification",
    "RestoreResult",
    "DatabaseLease",
    "create_backup",
    "verify_snapshot",
    "restore_snapshot",
    "format_preview",
]
