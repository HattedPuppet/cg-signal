"""SQLite repositories for archive, source configuration, and user state."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path
import urllib.parse
from typing import Any, Iterator

from .classification import apply_article_classification
from .config import (
    CLASSIFICATION_REVISION,
    FEEDS,
    MAX_ARCHIVE_PAGE_SIZE,
    MAX_FEEDBACK_ITEMS,
    MAX_ITEMS_PER_SOURCE,
    MAX_NOTE_LENGTH,
    MAX_SOURCE_NAME_LENGTH,
    MAX_SOURCE_URL_LENGTH,
    MAX_STATE_IDS,
    MAX_STATE_NOTES,
    MAX_STATE_SOURCES,
    RuntimePaths,
    SOURCE_ACCENTS,
)
STATE_IMPORT_MARKER = "user_state_json_imported"
CLASSIFICATION_METADATA_KEY = "article_classification_revision"
STORAGE_SCHEMA_VERSION = 1


class StorageSchemaError(RuntimeError):
    """The SQLite file is not one of the supported CG Signal schemas."""


_APPLICATION_TABLES = {"articles", "article_state", "sources", "source_preferences", "metadata"}
_HISTORICAL_TABLES = {"articles", "article_state", "sources", "metadata"}
_FEEDBACK_COLUMNS = {
    "feedback_source_id": ("TEXT", 1, "''", 0),
    "feedback_software_tags": ("TEXT", 1, "'[]'", 0),
    "feedback_topic_tags": ("TEXT", 1, "'[]'", 0),
}

# These statements are the owned on-disk schema contract.  The tokenizer below
# deliberately compares persisted sqlite_schema.sql tokens rather than using a
# fragile forbidden-word blacklist.  Whitespace/comments and a non-persisted
# IF NOT EXISTS are normalized; all other tokens remain exact.
_DDL_ARTICLES = (
    "CREATE TABLE articles (id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL, "
    "summary TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', source_id TEXT NOT NULL DEFAULT '', "
    "published_at TEXT NOT NULL, lane TEXT NOT NULL DEFAULT '', software_group TEXT NOT NULL DEFAULT '', "
    "software_tags TEXT NOT NULL DEFAULT '[]', topic_tags TEXT NOT NULL DEFAULT '[]', "
    "sources_text TEXT NOT NULL DEFAULT '', search_text TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL, "
    "first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL)"
)
_DDL_ARTICLE_STATE_BASE = (
    "CREATE TABLE article_state (article_id TEXT PRIMARY KEY, is_read INTEGER NOT NULL DEFAULT 0, "
    "is_saved INTEGER NOT NULL DEFAULT 0, is_archived INTEGER NOT NULL DEFAULT 0, "
    "note TEXT NOT NULL DEFAULT '', feedback_value INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)"
)
_DDL_FEEDBACK = (
    "feedback_source_id TEXT NOT NULL DEFAULT '', "
    "feedback_software_tags TEXT NOT NULL DEFAULT '[]', "
    "feedback_topic_tags TEXT NOT NULL DEFAULT '[]'"
)
_DDL_ARTICLE_STATE_NEW = (
    "CREATE TABLE article_state (article_id TEXT PRIMARY KEY, is_read INTEGER NOT NULL DEFAULT 0, "
    "is_saved INTEGER NOT NULL DEFAULT 0, is_archived INTEGER NOT NULL DEFAULT 0, "
    "note TEXT NOT NULL DEFAULT '', feedback_value INTEGER NOT NULL DEFAULT 0, "
    "feedback_source_id TEXT NOT NULL DEFAULT '', feedback_software_tags TEXT NOT NULL DEFAULT '[]', "
    "feedback_topic_tags TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL)"
)
_DDL_SOURCES = (
    "CREATE TABLE sources (id TEXT PRIMARY KEY, name TEXT NOT NULL, site TEXT NOT NULL DEFAULT '', "
    "feed TEXT NOT NULL UNIQUE, accent TEXT NOT NULL, item_limit INTEGER NOT NULL DEFAULT 40, "
    "enabled INTEGER NOT NULL DEFAULT 1, is_builtin INTEGER NOT NULL DEFAULT 0, "
    "sort_order INTEGER NOT NULL DEFAULT 1000, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
)
_DDL_SOURCE_PREFERENCES = (
    "CREATE TABLE source_preferences (source_id TEXT PRIMARY KEY, muted INTEGER NOT NULL DEFAULT 0, "
    "reduced INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)"
)
_DDL_METADATA = "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
_DDL_ARTICLES_PUBLISHED_INDEX = "CREATE INDEX articles_published_idx ON articles(published_at DESC)"
_DDL_ARTICLES_SOURCE_INDEX = "CREATE INDEX articles_source_idx ON articles(source_id)"


def _tokenize_ddl(sql: str) -> tuple[str, ...]:
    """Tokenize one persisted DDL statement without applying SQL semantics."""

    tokens: list[str] = []
    index = 0
    length = len(sql)
    while index < length:
        character = sql[index]
        if character.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                raise StorageSchemaError("Malformed DDL comment.")
            index = end + 2
            continue
        if character in "'\"`[":
            opener = character
            closer = "]" if opener == "[" else opener
            start = index
            index += 1
            closed = False
            while index < length:
                if sql[index] == closer:
                    if index + 1 < length and sql[index + 1] == closer and closer != "]":
                        index += 2
                        continue
                    if closer == "]" and index + 1 < length and sql[index + 1] == "]":
                        index += 2
                        continue
                    index += 1
                    closed = True
                    break
                index += 1
            if not closed:
                raise StorageSchemaError("Malformed quoted DDL token.")
            tokens.append(sql[start:index])
            continue
        if character.isascii() and (character.isalpha() or character == "_"):
            start = index
            index += 1
            while index < length:
                next_character = sql[index]
                if not next_character.isascii() or not (
                    next_character.isalnum() or next_character in "_$"
                ):
                    break
                index += 1
            tokens.append(sql[start:index].upper())
            continue
        if character.isascii() and (character.isdigit() or (character == "." and index + 1 < length and sql[index + 1].isdigit())):
            start = index
            index += 1
            while index < length and (sql[index].isascii() and (sql[index].isalnum() or sql[index] in ".xX+-")):
                index += 1
            tokens.append(sql[start:index])
            continue
        if character == ";":
            tokens.append(character)
            index += 1
            continue
        if character.isascii() and character in "(),.*=+-/%<>!|&~?:":
            tokens.append(character)
            index += 1
            continue
        raise StorageSchemaError(f"Malformed or unknown DDL token: {character!r}")

    if tokens and tokens[-1] == ";":
        tokens.pop()
        if tokens and tokens[-1] == ";":
            raise StorageSchemaError("Only one terminal DDL semicolon is allowed.")
    if ";" in tokens:
        raise StorageSchemaError("Only a terminal DDL semicolon is allowed.")
    normalized: list[str] = []
    index = 0
    while index < len(tokens):
        if index + 2 < len(tokens) and tokens[index:index + 3] == ["IF", "NOT", "EXISTS"]:
            index += 3
            continue
        normalized.append(tokens[index])
        index += 1
    return tuple(normalized)


def _historical_state_ddl(feedback_count: int) -> str:
    if not 0 <= feedback_count <= 2:
        raise ValueError("Historical feedback count must be between zero and two.")
    if not feedback_count:
        return _DDL_ARTICLE_STATE_BASE
    feedback = _DDL_FEEDBACK.split(", ")[:feedback_count]
    return _DDL_ARTICLE_STATE_BASE[:-1] + ", " + ", ".join(feedback) + ")"


def _schema_signature(shape: str) -> dict[tuple[str, str, str], str]:
    if shape == "empty":
        return {}
    if shape == "historical":
        tables = {
            ("table", "articles", "articles"): _DDL_ARTICLES,
            ("table", "article_state", "article_state"): _DDL_ARTICLE_STATE_BASE,
            ("table", "sources", "sources"): _DDL_SOURCES,
            ("table", "metadata", "metadata"): _DDL_METADATA,
        }
    else:
        state = _DDL_ARTICLE_STATE_NEW if shape in {"current_new", "current"} else _DDL_ARTICLE_STATE_BASE
        if shape.startswith("prefix"):
            state = _historical_state_ddl(int(shape[-1]))
        tables = {
            ("table", "articles", "articles"): _DDL_ARTICLES,
            ("table", "article_state", "article_state"): state,
            ("table", "sources", "sources"): _DDL_SOURCES,
            ("table", "source_preferences", "source_preferences"): _DDL_SOURCE_PREFERENCES,
            ("table", "metadata", "metadata"): _DDL_METADATA,
        }
    return {
        **tables,
        ("index", "articles_published_idx", "articles"): _DDL_ARTICLES_PUBLISHED_INDEX,
        ("index", "articles_source_idx", "articles"): _DDL_ARTICLES_SOURCE_INDEX,
    }


def _column_specs() -> dict[str, dict[str, tuple[str, int, str | None, int]]]:
    """Return the semantic v1 table signature (column order is irrelevant)."""

    return {
        "articles": {
            "id": ("TEXT", 0, None, 1),
            "title": ("TEXT", 1, None, 0), "url": ("TEXT", 1, None, 0),
            "summary": ("TEXT", 1, "''", 0), "source": ("TEXT", 1, "''", 0),
            "source_id": ("TEXT", 1, "''", 0), "published_at": ("TEXT", 1, None, 0),
            "lane": ("TEXT", 1, "''", 0), "software_group": ("TEXT", 1, "''", 0),
            "software_tags": ("TEXT", 1, "'[]'", 0), "topic_tags": ("TEXT", 1, "'[]'", 0),
            "sources_text": ("TEXT", 1, "''", 0), "search_text": ("TEXT", 1, "''", 0),
            "data_json": ("TEXT", 1, None, 0), "first_seen_at": ("TEXT", 1, None, 0),
            "last_seen_at": ("TEXT", 1, None, 0),
        },
        "article_state": {
            "article_id": ("TEXT", 0, None, 1),
            "is_read": ("INTEGER", 1, "0", 0), "is_saved": ("INTEGER", 1, "0", 0),
            "is_archived": ("INTEGER", 1, "0", 0), "note": ("TEXT", 1, "''", 0),
            "feedback_value": ("INTEGER", 1, "0", 0),
            "feedback_source_id": ("TEXT", 1, "''", 0),
            "feedback_software_tags": ("TEXT", 1, "'[]'", 0),
            "feedback_topic_tags": ("TEXT", 1, "'[]'", 0),
            "updated_at": ("TEXT", 1, None, 0),
        },
        "sources": {
            "id": ("TEXT", 0, None, 1), "name": ("TEXT", 1, None, 0),
            "site": ("TEXT", 1, "''", 0), "feed": ("TEXT", 1, None, 0),
            "accent": ("TEXT", 1, None, 0), "item_limit": ("INTEGER", 1, "40", 0),
            "enabled": ("INTEGER", 1, "1", 0), "is_builtin": ("INTEGER", 1, "0", 0),
            "sort_order": ("INTEGER", 1, "1000", 0), "created_at": ("TEXT", 1, None, 0),
            "updated_at": ("TEXT", 1, None, 0),
        },
        "source_preferences": {
            "source_id": ("TEXT", 0, None, 1), "muted": ("INTEGER", 1, "0", 0),
            "reduced": ("INTEGER", 1, "0", 0), "updated_at": ("TEXT", 1, None, 0),
        },
        "metadata": {"key": ("TEXT", 0, None, 1), "value": ("TEXT", 1, None, 0)},
    }


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _schema_inventory(connection: sqlite3.Connection) -> set[tuple[str, str, str]]:
    rows = connection.execute(
        "SELECT type, name, tbl_name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {(str(row[0]), str(row[1]), str(row[2])) for row in rows}


def _validate_schema_signature(connection: sqlite3.Connection, shape: str) -> None:
    actual = _schema_inventory(connection)
    candidates = [_schema_signature(shape)]
    if shape == "current":
        migrated = _schema_signature("current")
        migrated[("table", "article_state", "article_state")] = (
            _DDL_ARTICLE_STATE_BASE[:-1] + ", " + _DDL_FEEDBACK + ")"
        )
        candidates.append(migrated)
    for expected in candidates:
        if actual != set(expected):
            continue
        try:
            for object_key, expected_sql in expected.items():
                actual_sql = connection.execute(
                    "SELECT sql FROM sqlite_schema WHERE type = ? AND name = ? AND tbl_name = ?",
                    object_key,
                ).fetchone()
                if actual_sql is None or not isinstance(actual_sql[0], str):
                    raise StorageSchemaError(f"Missing persisted DDL for {object_key[1]}.")
                if _tokenize_ddl(actual_sql[0]) != _tokenize_ddl(expected_sql):
                    raise StorageSchemaError(f"Persisted DDL for {object_key[1]} is not supported.")
            return
        except StorageSchemaError:
            continue
    raise StorageSchemaError("SQLite persisted DDL is not supported.")


def _normalized_default(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).replace(" ", "")


def _table_columns(connection: sqlite3.Connection, table: str) -> dict[str, tuple[str, int, str | None, int]]:
    rows = connection.execute(f'PRAGMA table_xinfo("{table}")').fetchall()
    columns: dict[str, tuple[str, int, str | None, int]] = {}
    for row in rows:
        # table_xinfo exposes generated/hidden columns through the final flag.
        if len(row) >= 7 and int(row[6]) != 0:
            raise StorageSchemaError(f"Hidden or generated column in {table}.")
        name = str(row[1])
        columns[name] = (str(row[2]).upper(), int(row[3]), _normalized_default(row[4]), int(row[5]))
    return columns


def _index_columns(connection: sqlite3.Connection, name: str) -> list[tuple[str, int]]:
    rows = connection.execute(f'PRAGMA index_xinfo("{name}")').fetchall()
    return [(str(row[2]), int(row[3])) for row in rows if int(row[5]) == 1 and row[2] is not None]


def _validate_indexes(
    connection: sqlite3.Connection,
    *,
    tables: set[str] | None = None,
) -> None:
    # User-created article indexes have contractual names; SQLite-generated
    # primary-key/UNIQUE index names do not.  Validate generated indexes by
    # their PRAGMA origin and key columns instead of depending on names such as
    # ``sqlite_autoindex_sources_2``.
    expected_user = {"articles_published_idx", "articles_source_idx"}
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_autoindex_%'"
    ).fetchall()
    actual_user = {str(row[0]) for row in rows}
    if actual_user != expected_user or any(not isinstance(row[1], str) for row in rows):
        raise StorageSchemaError("Required user indexes are missing or unexpected indexes exist.")

    expected_pk = {
        "articles": "id",
        "article_state": "article_id",
        "sources": "id",
        "source_preferences": "source_id",
        "metadata": "key",
    }
    if tables is not None:
        expected_pk = {table: column for table, column in expected_pk.items() if table in tables}
    for table, pk_column in expected_pk.items():
        index_rows = connection.execute(f'PRAGMA index_list("{table}")').fetchall()
        if not index_rows:
            raise StorageSchemaError(f"Indexes for {table} are missing.")
        pk_rows: list[Any] = []
        unique_rows: list[Any] = []
        user_rows: list[Any] = []
        for row in index_rows:
            name, unique, origin, partial = str(row[1]), int(row[2]), str(row[3]), int(row[4])
            if partial:
                raise StorageSchemaError(f"Partial index is not supported: {name}")
            if origin == "pk":
                pk_rows.append(row)
                if unique != 1 or _index_columns(connection, name) != [(pk_column, 0)]:
                    raise StorageSchemaError(f"Primary-key index for {table} has wrong semantics.")
            elif origin == "u":
                unique_rows.append(row)
                if table != "sources" or unique != 1 or _index_columns(connection, name) != [("feed", 0)]:
                    raise StorageSchemaError(f"Unexpected UNIQUE index for {table}.")
            elif origin == "c":
                user_rows.append(row)
                if unique != 0:
                    raise StorageSchemaError(f"User index {name} must be non-unique.")
            else:
                raise StorageSchemaError(f"Unexpected index origin for {table}: {origin}")
        if len(pk_rows) != 1:
            raise StorageSchemaError(f"Primary-key index for {table} is missing or duplicated.")
        if table == "sources":
            if len(unique_rows) != 1:
                raise StorageSchemaError("sources.feed uniqueness is missing or duplicated.")
        elif unique_rows:
            raise StorageSchemaError(f"Unexpected UNIQUE index for {table}.")
        expected_names = {
            "articles": expected_user,
            "article_state": set(),
            "sources": set(),
            "source_preferences": set(),
            "metadata": set(),
        }[table]
        if {str(row[1]) for row in user_rows} != expected_names:
            raise StorageSchemaError(f"Unexpected user indexes for {table}.")
        for row in user_rows:
            name = str(row[1])
            columns = _index_columns(connection, name)
            if name == "articles_published_idx" and columns != [("published_at", 1)]:
                raise StorageSchemaError("articles_published_idx has wrong semantics.")
            if name == "articles_source_idx" and columns != [("source_id", 0)]:
                raise StorageSchemaError("articles_source_idx has wrong semantics.")


def validate_current_schema(connection: sqlite3.Connection, *, require_version: bool = True) -> None:
    """Validate the exact application schema without changing the database."""

    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if require_version and version != STORAGE_SCHEMA_VERSION:
        raise StorageSchemaError(f"Unsupported storage schema version {version}.")
    if _table_names(connection) != _APPLICATION_TABLES:
        raise StorageSchemaError("Application tables do not match the supported schema.")
    _validate_schema_signature(connection, "current")
    specs = _column_specs()
    for table, expected in specs.items():
        actual = _table_columns(connection, table)
        if actual != expected:
            raise StorageSchemaError(f"Columns for {table} do not match the supported schema.")
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchone() is not None:
            raise StorageSchemaError("Foreign keys are not part of the supported schema.")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1").fetchone() is not None:
        raise StorageSchemaError("Views and triggers are not part of the supported schema.")
    _validate_indexes(connection)


def _validate_v0_shape(connection: sqlite3.Connection) -> str:
    """Classify one of the finite historical shapes before any migration DDL."""

    tables = _table_names(connection)
    if not tables:
        if _schema_inventory(connection):
            raise StorageSchemaError("Unknown version-0 schema objects.")
        return "empty"
    specs = _column_specs()
    base_article_state = {name: spec for name, spec in specs["article_state"].items() if name not in _FEEDBACK_COLUMNS}
    historical_sets = (_HISTORICAL_TABLES, _APPLICATION_TABLES)
    if tables not in historical_sets:
        raise StorageSchemaError("Unknown version-0 schema shape.")
    for table in ("articles", "sources"):
        if _table_columns(connection, table) != specs[table]:
            raise StorageSchemaError(f"Unknown version-0 columns for {table}.")
    state_columns = _table_columns(connection, "article_state")
    if tables == _HISTORICAL_TABLES:
        if state_columns != base_article_state:
            raise StorageSchemaError("Unknown historical article_state columns.")
        _validate_schema_signature(connection, "historical")
        _validate_indexes(connection, tables=_HISTORICAL_TABLES)
        return "historical"
    if tables == _APPLICATION_TABLES:
        if state_columns == specs["article_state"]:
            _validate_schema_signature(connection, "current")
            _validate_indexes(connection)
            return "current"
        feedback_names = list(_FEEDBACK_COLUMNS)
        allowed = [
            {
                **base_article_state,
                **{name: specs["article_state"][name] for name in feedback_names[:count]},
            }
            for count in (0, 1, 2)
        ]
        # The old initializer can have stopped after zero, one, or two ALTERs;
        # an already-complete three-column shape is handled above.
        if state_columns not in allowed:
            raise StorageSchemaError("Unknown version-0 article_state columns.")
        count = next(
            count for count in (0, 1, 2)
            if state_columns == {
                **base_article_state,
                **{name: specs["article_state"][name] for name in feedback_names[:count]},
            }
        )
        _validate_schema_signature(connection, f"prefix{count}")
        _validate_indexes(connection)
        return "prefix"
    raise StorageSchemaError("Unknown version-0 schema shape.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def migrate_storage_schema(connection: sqlite3.Connection) -> None:
    """Migrate a known v0 database to v1 without owning runtime maintenance.

    The caller owns the connection and remains responsible for closing it and
    selecting journal mode.  This API performs schema DDL only, under its own
    transaction, and stamps ``user_version`` only after exact v1 validation.
    """

    if connection.in_transaction:
        raise StorageSchemaError("Storage schema migration requires no active transaction.")
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version == STORAGE_SCHEMA_VERSION:
        validate_current_schema(connection)
        return
    if version != 0:
        raise StorageSchemaError(f"Unsupported storage schema version {version}.")
    shape = _validate_v0_shape(connection)
    connection.execute("BEGIN IMMEDIATE")
    try:
        if shape == "empty":
            connection.execute(_DDL_ARTICLES)
            connection.execute(_DDL_ARTICLES_PUBLISHED_INDEX)
            connection.execute(_DDL_ARTICLES_SOURCE_INDEX)
            connection.execute(_DDL_ARTICLE_STATE_NEW)
            connection.execute(_DDL_SOURCES)
            connection.execute(_DDL_SOURCE_PREFERENCES)
            connection.execute(_DDL_METADATA)
        elif shape == "historical":
            connection.execute(_DDL_SOURCE_PREFERENCES)
            for name, definition in _FEEDBACK_COLUMNS.items():
                connection.execute(
                    f"ALTER TABLE article_state ADD COLUMN {name} {definition[0]} NOT NULL DEFAULT {definition[2]}"
                )
        elif shape.startswith("prefix"):
            columns = _table_columns(connection, "article_state")
            for name, definition in _FEEDBACK_COLUMNS.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE article_state ADD COLUMN {name} {definition[0]} NOT NULL DEFAULT {definition[2]}"
                    )
        elif shape == "current":
            pass
        else:
            raise StorageSchemaError(f"Unknown migration shape: {shape}")
        validate_current_schema(connection, require_version=False)
        connection.execute(f"PRAGMA user_version={STORAGE_SCHEMA_VERSION}")
        validate_current_schema(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def normalize_string_list(values: Any, limit: int, max_length: int = 80) -> list[str]:
    if not isinstance(values, list):
        return []
    valid = [
        value for value in values
        if isinstance(value, str) and 1 <= len(value) <= max_length
    ]
    return list(dict.fromkeys(valid))[-limit:]


def normalize_feedback(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    by_id: dict[str, dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        article_id = item.get("id")
        value = item.get("value")
        if not isinstance(article_id, str) or not 1 <= len(article_id) <= 80:
            continue
        if isinstance(value, bool) or value not in {-1, 1}:
            continue
        source_id = item.get("source_id", "")
        if not isinstance(source_id, str) or len(source_id) > 80:
            source_id = ""
        by_id[article_id] = {
            "id": article_id,
            "value": value,
            "source_id": source_id,
            "software_tags": normalize_string_list(item.get("software_tags", []), 12),
            "topic_tags": normalize_string_list(item.get("topic_tags", []), 12),
        }
    return list(by_id.values())[-MAX_FEEDBACK_ITEMS:]


def normalize_user_state(payload: Any) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    normalized: dict[str, Any] = {
        key: normalize_string_list(source.get(key, []), MAX_STATE_IDS)
        for key in ("saved", "archived")
    }
    notes = source.get("notes", {})
    normalized_notes: dict[str, str] = {}
    if isinstance(notes, dict):
        for article_id, note in notes.items():
            if not isinstance(article_id, str) or not 1 <= len(article_id) <= 80:
                continue
            if not isinstance(note, str):
                continue
            clean = note.strip()[:MAX_NOTE_LENGTH]
            if clean:
                normalized_notes[article_id] = clean
    normalized["notes"] = dict(list(normalized_notes.items())[-MAX_STATE_NOTES:])
    normalized["feedback"] = normalize_feedback(source.get("feedback", []))
    normalized["muted_sources"] = normalize_string_list(
        source.get("muted_sources", []), MAX_STATE_SOURCES
    )
    muted = set(normalized["muted_sources"])
    normalized["reduced_sources"] = [
        source_id
        for source_id in normalize_string_list(
            source.get("reduced_sources", []), MAX_STATE_SOURCES
        )
        if source_id not in muted
    ]
    updated_at = source.get("updated_at")
    if not isinstance(updated_at, str) or len(updated_at) > 64:
        updated_at = _now()
    else:
        try:
            datetime.fromisoformat(updated_at)
        except ValueError:
            updated_at = _now()
    normalized["updated_at"] = updated_at
    return normalized


def normalized_http_url(value: Any, label: str, required: bool = True) -> str:
    """Normalize URL syntax for storage; network safety is enforced at fetch time."""
    if value in (None, "") and not required:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a URL.")
    clean = value.strip()
    if not clean or len(clean) > MAX_SOURCE_URL_LENGTH:
        raise ValueError(f"{label} must be a valid HTTP or HTTPS URL.")
    parsed = None
    try:
        parsed = urllib.parse.urlsplit(clean)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        hostname = None
        port = None
    if (
        parsed is None
        or parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or (port is not None and not 1 <= port <= 65535)
        or any(ord(character) < 32 or character.isspace() for character in parsed.netloc)
    ):
        raise ValueError(f"{label} must be a valid HTTP or HTTPS URL.")
    return urllib.parse.urlunsplit(parsed)


def source_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "site": row["site"],
        "feed": row["feed"],
        "accent": row["accent"],
        "limit": int(row["item_limit"]),
        "enabled": bool(row["enabled"]),
        "is_builtin": bool(row["is_builtin"]),
    }


class SQLiteRepository:
    """Own one SQLite database and serialize all state/archive operations."""

    def __init__(self, paths: RuntimePaths):
        self.paths = paths
        self._lock = threading.RLock()
        self._initialized = False

    @contextmanager
    def connection(self, *, set_wal: bool = True) -> Iterator[sqlite3.Connection]:
        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            connection = sqlite3.connect(self.paths.archive_db_file, timeout=10)
            connection.row_factory = sqlite3.Row
            if set_wal:
                connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def initialize(self, force: bool = False) -> None:
        with self._lock:
            if self._initialized and not force:
                return
            # Inspect and migrate the persisted schema before enabling the
            # runtime WAL mode.  Rejected version-0 shapes therefore receive
            # no migration DDL or journal-mode mutation.
            with self.connection(set_wal=False) as connection:
                migrate_storage_schema(connection)
                connection.execute("PRAGMA journal_mode=WAL")
                now = _now()
                for order, source in enumerate(FEEDS):
                    connection.execute(
                        """
                        INSERT INTO sources (
                            id, name, site, feed, accent, item_limit, enabled,
                            is_builtin, sort_order, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            name = excluded.name, site = excluded.site,
                            feed = excluded.feed, accent = excluded.accent,
                            item_limit = excluded.item_limit, is_builtin = 1,
                            sort_order = excluded.sort_order, updated_at = excluded.updated_at
                        """,
                        (
                            source["id"], source["name"], source["site"], source["feed"],
                            source["accent"], int(source.get("limit", MAX_ITEMS_PER_SOURCE)),
                            order, now, now,
                        ),
                    )
                self._import_legacy_state(connection)
                self._reclassify_if_needed(connection)
            self._initialized = True

    def _import_legacy_state(self, connection: sqlite3.Connection) -> None:
        """Import user-state.json exactly once, without ever writing it."""

        marker = connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (STATE_IMPORT_MARKER,)
        ).fetchone()
        if marker is not None:
            return
        try:
            raw = self.paths.user_state_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            connection.execute(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                (STATE_IMPORT_MARKER, "no_file"),
            )
            return
        except (OSError, UnicodeDecodeError):
            connection.execute(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                (STATE_IMPORT_MARKER, "invalid"),
            )
            return
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            connection.execute(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                (STATE_IMPORT_MARKER, "invalid"),
            )
            return
        if not isinstance(state, dict):
            connection.execute(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                (STATE_IMPORT_MARKER, "invalid"),
            )
            return
        normalized = normalize_user_state(state)
        self._replace_state(connection, normalized)
        connection.execute(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            (STATE_IMPORT_MARKER, "imported"),
        )

    def _reclassify_if_needed(self, connection: sqlite3.Connection) -> int:
        stored = connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (CLASSIFICATION_METADATA_KEY,)
        ).fetchone()
        if stored and stored["value"] == str(CLASSIFICATION_REVISION):
            return 0
        updates = []
        for row in connection.execute("SELECT * FROM articles").fetchall():
            try:
                article = json.loads(row["data_json"])
            except (TypeError, json.JSONDecodeError):
                article = {}
            if not isinstance(article, dict):
                article = {}
            article.update(
                {
                    "id": row["id"], "title": row["title"], "url": row["url"],
                    "summary": row["summary"], "source": row["source"],
                    "source_id": row["source_id"], "published_at": row["published_at"],
                }
            )
            apply_article_classification(article)
            updates.append(
                (
                    article.get("lane", ""), article.get("software_group", ""),
                    json.dumps(article.get("software_tags", []), ensure_ascii=False),
                    json.dumps(article.get("topic_tags", []), ensure_ascii=False),
                    archive_search_text(article), json.dumps(article, ensure_ascii=False),
                    row["id"],
                )
            )
        if updates:
            connection.executemany(
                """
                UPDATE articles SET lane = ?, software_group = ?, software_tags = ?,
                    topic_tags = ?, search_text = ?, data_json = ? WHERE id = ?
                """,
                updates,
            )
        connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (CLASSIFICATION_METADATA_KEY, str(CLASSIFICATION_REVISION)),
        )
        return len(updates)

    def list_source_configs(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        self.initialize()
        query = "SELECT * FROM sources"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY sort_order, name COLLATE NOCASE"
        with self.connection() as connection:
            return [source_row_to_dict(row) for row in connection.execute(query).fetchall()]

    def source_config(self, source_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return source_row_to_dict(row) if row else None

    def add_source_config(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Source details must be an object.")
        feed = normalized_http_url(payload.get("feed"), "Feed URL")
        site = normalized_http_url(payload.get("site"), "Website URL", required=False)
        parsed = urllib.parse.urlsplit(feed)
        raw_name = payload.get("name", "")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not name:
            name = parsed.hostname or "Custom feed"
        if len(name) > MAX_SOURCE_NAME_LENGTH:
            raise ValueError("Source name is too long.")
        base_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:45] or "custom"
        digest = hashlib.sha1(feed.encode("utf-8")).hexdigest()[:8]
        source_id = f"{base_slug}-{digest}"
        accent = SOURCE_ACCENTS[int(digest[:2], 16) % len(SOURCE_ACCENTS)]
        now = _now()
        self.initialize()
        try:
            with self.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO sources (
                        id, name, site, feed, accent, item_limit, enabled,
                        is_builtin, sort_order, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, 1000, ?, ?)
                    """,
                    (source_id, name, site, feed, accent, MAX_ITEMS_PER_SOURCE, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("That feed URL is already configured.") from exc
        return self.source_config(source_id) or {}

    def set_source_enabled(self, source_id: Any, enabled: Any) -> dict[str, Any]:
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("A source id is required.")
        if not isinstance(enabled, bool):
            raise ValueError("Enabled must be true or false.")
        self.initialize()
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE sources SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), _now(), source_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Source not found.")
        return self.source_config(source_id) or {}

    def _replace_state(self, connection: sqlite3.Connection, state: dict[str, Any]) -> None:
        saved_ids = set(state.get("saved", []))
        archived_ids = set(state.get("archived", []))
        notes = state.get("notes", {})
        feedback = {
            item["id"]: item for item in state.get("feedback", [])
            if isinstance(item, dict) and item.get("id")
        }
        article_ids = saved_ids | archived_ids | set(notes) | set(feedback)
        updated_at = state.get("updated_at") or _now()
        rows = []
        for article_id in article_ids:
            item = feedback.get(article_id, {})
            rows.append(
                (
                    article_id,
                    0,
                    int(article_id in saved_ids),
                    int(article_id in archived_ids),
                    notes.get(article_id, ""),
                    int(item.get("value", 0) or 0),
                    item.get("source_id", ""),
                    json.dumps(item.get("software_tags", []), ensure_ascii=False),
                    json.dumps(item.get("topic_tags", []), ensure_ascii=False),
                    updated_at,
                )
            )
        connection.execute("DELETE FROM article_state")
        if rows:
            connection.executemany(
                """
                INSERT INTO article_state (
                    article_id, is_read, is_saved, is_archived, note,
                    feedback_value, feedback_source_id, feedback_software_tags,
                    feedback_topic_tags, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        connection.execute("DELETE FROM source_preferences")
        preference_rows = [
            (source_id, 1, 0, updated_at)
            for source_id in state.get("muted_sources", [])
        ]
        preference_rows.extend(
            (source_id, 0, 1, updated_at)
            for source_id in state.get("reduced_sources", [])
            if source_id not in set(state.get("muted_sources", []))
        )
        if preference_rows:
            connection.executemany(
                """
                INSERT INTO source_preferences(source_id, muted, reduced, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                preference_rows,
            )
        connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES ('user_state_updated_at', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (updated_at,),
        )

    def write_state(self, payload: Any) -> dict[str, Any]:
        state = normalize_user_state(payload)
        self.initialize()
        with self.connection() as connection:
            self._replace_state(connection, state)
        return state

    def read_state(self) -> dict[str, Any]:
        self.initialize()
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM article_state").fetchall()
            preferences = connection.execute("SELECT * FROM source_preferences").fetchall()
            updated = connection.execute(
                "SELECT value FROM metadata WHERE key = 'user_state_updated_at'"
            ).fetchone()
        saved = [row["article_id"] for row in rows if row["is_saved"]]
        archived = [row["article_id"] for row in rows if row["is_archived"]]
        notes = {row["article_id"]: row["note"] for row in rows if row["note"]}
        feedback = []
        for row in rows:
            value = int(row["feedback_value"] or 0)
            if value not in {-1, 1}:
                continue
            try:
                software_tags = json.loads(row["feedback_software_tags"] or "[]")
                topic_tags = json.loads(row["feedback_topic_tags"] or "[]")
            except json.JSONDecodeError:
                software_tags, topic_tags = [], []
            feedback.append(
                {
                    "id": row["article_id"],
                    "value": value,
                    "source_id": row["feedback_source_id"] or "",
                    "software_tags": software_tags if isinstance(software_tags, list) else [],
                    "topic_tags": topic_tags if isinstance(topic_tags, list) else [],
                }
            )
        muted = [row["source_id"] for row in preferences if row["muted"]]
        reduced = [row["source_id"] for row in preferences if row["reduced"] and not row["muted"]]
        return normalize_user_state(
            {
                "saved": saved,
                "archived": archived,
                "notes": notes,
                "feedback": feedback,
                "muted_sources": muted,
                "reduced_sources": reduced,
                "updated_at": updated["value"] if updated else _now(),
            }
        )

    def archive_articles(self, articles: list[dict[str, Any]]) -> int:
        self.initialize()
        if not articles:
            return self.archive_article_count()
        now = _now()
        rows = []
        for article in articles:
            rows.append(
                (
                    article["id"], article.get("title", ""), article.get("url", ""),
                    article.get("summary", ""), article.get("source", ""),
                    article.get("source_id", ""), article.get("published_at", now),
                    article.get("lane", ""), article.get("software_group", ""),
                    json.dumps(article.get("software_tags", []), ensure_ascii=False),
                    json.dumps(article.get("topic_tags", []), ensure_ascii=False),
                    " ".join(
                        f"{source.get('id', '')} {source.get('name', '')}"
                        for source in article.get("sources", [])
                    ),
                    archive_search_text(article), json.dumps(article, ensure_ascii=False), now, now,
                )
            )
        with self.connection() as connection:
            connection.executemany(
                """
                INSERT INTO articles (
                    id, title, url, summary, source, source_id, published_at, lane,
                    software_group, software_tags, topic_tags, sources_text,
                    search_text, data_json, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title, url = excluded.url, summary = excluded.summary,
                    source = excluded.source, source_id = excluded.source_id,
                    published_at = excluded.published_at, lane = excluded.lane,
                    software_group = excluded.software_group,
                    software_tags = excluded.software_tags, topic_tags = excluded.topic_tags,
                    sources_text = excluded.sources_text, search_text = excluded.search_text,
                    data_json = excluded.data_json, last_seen_at = excluded.last_seen_at
                """,
                rows,
            )
            count = connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        return int(count)

    def archive_article_count(self) -> int:
        self.initialize()
        with self.connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0])

    def query_archive(
        self,
        query: str = "",
        lane: str = "All",
        source_ids: list[str] | None = None,
        limit: int = 60,
        offset: int = 0,
        new_after: str = "",
    ) -> dict[str, Any]:
        self.initialize()
        limit = max(1, min(MAX_ARCHIVE_PAGE_SIZE, int(limit)))
        offset = max(0, int(offset))
        clauses: list[str] = []
        parameters: list[Any] = []
        for token in parse_archive_search(query):
            clause, values = archive_token_sql(token, new_after)
            clauses.append(clause)
            parameters.extend(values)
        if lane in {"Tech & Development", "Industry", "Business"}:
            clauses.append("a.lane = ?")
            parameters.append(lane)
        valid_source_ids = [source_id for source_id in (source_ids or []) if source_id]
        if valid_source_ids:
            source_clauses = []
            for source_id in valid_source_ids[:MAX_STATE_SOURCES]:
                source_clauses.append("(a.source_id = ? OR (' ' || a.sources_text || ' ') LIKE ?)")
                parameters.extend([source_id, f"% {source_id} %"])
            clauses.append(f"({' OR '.join(source_clauses)})")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        base = "FROM articles a LEFT JOIN article_state s ON s.article_id = a.id"
        with self.connection() as connection:
            total = int(connection.execute(
                f"SELECT COUNT(*) {base} {where}", parameters
            ).fetchone()[0])
            rows = connection.execute(
                f"""
                SELECT a.data_json, a.first_seen_at, a.last_seen_at
                {base} {where} ORDER BY a.published_at DESC LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
        articles = []
        for row in rows:
            try:
                article = json.loads(row["data_json"])
            except json.JSONDecodeError:
                article = {}
            article["archive_first_seen_at"] = row["first_seen_at"]
            article["archive_last_seen_at"] = row["last_seen_at"]
            articles.append(article)
        return {
            "articles": articles,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(articles) < total,
            "archive_count": self.archive_article_count(),
        }


ARCHIVE_SEARCH_ALIASES = {
    "unreal": ("software", "unreal engine"),
    "unreal-engine": ("software", "unreal engine"),
    "ue": ("software", "unreal engine"),
    "ue5": ("software", "unreal engine"),
    "unity": ("software", "unity"),
    "blender": ("software", "blender"),
    "houdini": ("software", "houdini"),
    "painter": ("software", "substance 3d"),
    "designer": ("software", "substance 3d"),
    "substance": ("software", "substance 3d"),
    "substance-painter": ("software", "substance 3d"),
    "substance-designer": ("software", "substance 3d"),
    "production": ("software", "production techniques"),
    "production-techniques": ("software", "production techniques"),
    "industry": ("software", "industry context"),
    "industry-context": ("software", "industry context"),
    "business": ("software", "business context"),
    "business-context": ("software", "business context"),
    "ai": ("software", "ai"),
    "genai": ("software", "ai"),
}


def parse_archive_search(query: str) -> list[dict[str, Any]]:
    import shlex

    normalized = re.sub(r"#unreal\s+engine\b", '#software:"Unreal Engine"', query, flags=re.I)
    normalized = re.sub(
        r"#substance\s+(?:painter|designer|3d)\b",
        '#software:"Substance 3D"', normalized, flags=re.I,
    )
    normalized = re.sub(r"#production\s+techniques\b", '#software:"Production techniques"', normalized, flags=re.I)
    normalized = re.sub(r"#industry\s+context\b", '#software:"Industry context"', normalized, flags=re.I)
    normalized = re.sub(r"#business\s+context\b", '#software:"Business context"', normalized, flags=re.I)
    try:
        raw_tokens = shlex.split(normalized)
    except ValueError:
        raw_tokens = normalized.split()
    tokens: list[dict[str, Any]] = []
    for raw in raw_tokens:
        negative = raw.startswith("-")
        token = raw[1:] if negative else raw
        field = "text"
        value = token
        if token.startswith("#"):
            token = token[1:]
            if ":" in token:
                candidate_field, candidate_value = token.split(":", 1)
                if candidate_field.lower() in {"software", "topic", "source", "is"}:
                    field, value = candidate_field.lower(), candidate_value
            else:
                alias = ARCHIVE_SEARCH_ALIASES.get(token.lower().replace("_", "-"))
                if alias:
                    field, value = alias
                else:
                    value = token
        value = value.strip().lower()
        if field == "software" and value in {"substance painter", "substance designer"}:
            value = "substance 3d"
        if value:
            tokens.append({"negative": negative, "field": field, "value": value})
    return tokens


def archive_like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def archive_token_sql(token: dict[str, Any], new_after: str) -> tuple[str, list[Any]]:
    field, value = token["field"], token["value"]
    pattern = archive_like_pattern(value)
    if field == "software":
        clause = "(LOWER(a.software_group) LIKE ? ESCAPE '\\' OR LOWER(a.software_tags) LIKE ? ESCAPE '\\')"
        parameters: list[Any] = [pattern, pattern]
    elif field == "topic":
        clause = "LOWER(a.topic_tags) LIKE ? ESCAPE '\\'"
        parameters = [pattern]
    elif field == "source":
        clause = "(LOWER(a.source) LIKE ? ESCAPE '\\' OR LOWER(a.source_id) LIKE ? ESCAPE '\\' OR LOWER(a.sources_text) LIKE ? ESCAPE '\\')"
        parameters = [pattern, pattern, pattern]
    elif field == "is":
        status_clauses = {
            "saved": "COALESCE(s.is_saved, 0) = 1",
            "library": "COALESCE(s.is_saved, 0) = 1",
            "archived": "COALESCE(s.is_archived, 0) = 1",
            "liked": "COALESCE(s.feedback_value, 0) = 1",
            "reduced": "COALESCE(s.feedback_value, 0) = -1",
        }
        if value == "new":
            clause = "a.published_at > ?" if new_after else "0 = 1"
            parameters = [new_after] if new_after else []
        else:
            clause = status_clauses.get(value, "0 = 1")
            parameters = []
    else:
        clause = "(a.search_text LIKE ? ESCAPE '\\' OR LOWER(COALESCE(s.note, '')) LIKE ? ESCAPE '\\')"
        parameters = [pattern, pattern]
    if token["negative"]:
        clause = f"NOT ({clause})"
    return clause, parameters


def archive_search_text(article: dict[str, Any]) -> str:
    values: list[str] = [
        article.get("title", ""), article.get("summary", ""), article.get("source", ""),
        article.get("source_id", ""), article.get("lane", ""), article.get("software_group", ""),
    ]
    values.extend(article.get("software_tags", []))
    values.extend(article.get("topic_tags", []))
    values.extend(article.get("priority_reasons", []))
    values.extend(
        f"{item.get('source', '')} {item.get('title', '')}"
        for item in article.get("related", [])
    )
    return " ".join(str(value) for value in values if value).lower()
