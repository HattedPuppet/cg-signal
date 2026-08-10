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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            connection = sqlite3.connect(self.paths.archive_db_file, timeout=10)
            connection.row_factory = sqlite3.Row
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
            with self.connection() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS articles (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        url TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT '',
                        source_id TEXT NOT NULL DEFAULT '',
                        published_at TEXT NOT NULL,
                        lane TEXT NOT NULL DEFAULT '',
                        software_group TEXT NOT NULL DEFAULT '',
                        software_tags TEXT NOT NULL DEFAULT '[]',
                        topic_tags TEXT NOT NULL DEFAULT '[]',
                        sources_text TEXT NOT NULL DEFAULT '',
                        search_text TEXT NOT NULL DEFAULT '',
                        data_json TEXT NOT NULL,
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS articles_published_idx
                        ON articles(published_at DESC);
                    CREATE INDEX IF NOT EXISTS articles_source_idx ON articles(source_id);
                    CREATE TABLE IF NOT EXISTS article_state (
                        article_id TEXT PRIMARY KEY,
                        is_read INTEGER NOT NULL DEFAULT 0,
                        is_saved INTEGER NOT NULL DEFAULT 0,
                        is_archived INTEGER NOT NULL DEFAULT 0,
                        note TEXT NOT NULL DEFAULT '',
                        feedback_value INTEGER NOT NULL DEFAULT 0,
                        feedback_source_id TEXT NOT NULL DEFAULT '',
                        feedback_software_tags TEXT NOT NULL DEFAULT '[]',
                        feedback_topic_tags TEXT NOT NULL DEFAULT '[]',
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sources (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        site TEXT NOT NULL DEFAULT '',
                        feed TEXT NOT NULL UNIQUE,
                        accent TEXT NOT NULL,
                        item_limit INTEGER NOT NULL DEFAULT 40,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        is_builtin INTEGER NOT NULL DEFAULT 0,
                        sort_order INTEGER NOT NULL DEFAULT 1000,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS source_preferences (
                        source_id TEXT PRIMARY KEY,
                        muted INTEGER NOT NULL DEFAULT 0,
                        reduced INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    """
                )
                # Older databases do not have the feedback context columns.
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(article_state)")
                }
                for name, definition in (
                    ("feedback_source_id", "TEXT NOT NULL DEFAULT ''"),
                    ("feedback_software_tags", "TEXT NOT NULL DEFAULT '[]'"),
                    ("feedback_topic_tags", "TEXT NOT NULL DEFAULT '[]'"),
                ):
                    if name not in columns:
                        connection.execute(
                            f"ALTER TABLE article_state ADD COLUMN {name} {definition}"
                        )
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
