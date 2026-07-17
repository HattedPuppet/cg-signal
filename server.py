from __future__ import annotations

import argparse
from contextlib import contextmanager
import concurrent.futures
import email.utils
import hashlib
import html
import json
import mimetypes
import os
import re
import shlex
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CACHE_DIR = BASE_DIR / ".cache"
CACHE_FILE = CACHE_DIR / "feed-cache.json"
IMAGE_INDEX_FILE = CACHE_DIR / "image-index.json"
USER_STATE_FILE = CACHE_DIR / "user-state.json"
ARCHIVE_DB_FILE = CACHE_DIR / "cg-signal.db"
PID_FILE = CACHE_DIR / "server.pid"
CACHE_TTL_SECONDS = 15 * 60
IMAGE_INDEX_TTL_SECONDS = 30 * 86400
MAX_ITEMS_PER_SOURCE = 40
MAX_STATE_IDS = 5000
MAX_STATE_NOTES = 1200
MAX_NOTE_LENGTH = 4000
MAX_FEEDBACK_ITEMS = 500
MAX_STATE_SOURCES = 200
USER_STATE_LOCK = threading.Lock()
ARCHIVE_INIT_LOCK = threading.Lock()
ARCHIVE_INITIALIZED = False
MAX_ARCHIVE_PAGE_SIZE = 200
MAX_SOURCE_NAME_LENGTH = 100
MAX_SOURCE_URL_LENGTH = 2048

FEEDS = (
    {
        "id": "80-level",
        "name": "80 Level",
        "site": "https://80.lv/",
        "feed": "https://80.lv/feed",
        "accent": "#f4b400",
    },
    {
        "id": "cgworld",
        "name": "CGWORLD",
        "site": "https://cgworld.jp/",
        "feed": "https://cgworld.jp/atom.xml",
        "accent": "#ef5350",
    },
    {
        "id": "gamemakers",
        "name": "Game Makers",
        "site": "https://gamemakers.jp/",
        "feed": "https://gamemakers.jp/feed/",
        "accent": "#3d8bfd",
    },
    {
        "id": "3dnchu",
        "name": "3D人",
        "site": "https://3dnchu.com/",
        "feed": "https://3dnchu.com/feed/",
        "accent": "#ef7c3b",
    },
    {
        "id": "cginterest",
        "name": "CGinterest",
        "site": "https://cginterest.com/",
        "feed": "https://cginterest.com/feed/",
        "accent": "#2bb673",
    },
    {
        "id": "befores-afters",
        "name": "befores & afters",
        "site": "https://beforesandafters.com/",
        "feed": "https://beforesandafters.com/feed/",
        "accent": "#df4661",
        "limit": 20,
    },
    {
        "id": "game-developer",
        "name": "Game Developer",
        "site": "https://www.gamedeveloper.com/",
        "feed": "https://www.gamedeveloper.com/rss.xml",
        "accent": "#7357ff",
        "limit": 20,
    },
    {
        "id": "cartoon-brew",
        "name": "Cartoon Brew",
        "site": "https://www.cartoonbrew.com/",
        "feed": "https://www.cartoonbrew.com/feed/",
        "accent": "#f15a2a",
        "limit": 20,
    },
    {
        "id": "siggraph",
        "name": "ACM SIGGRAPH",
        "site": "https://blog.siggraph.org/",
        "feed": "https://blog.siggraph.org/feed/",
        "accent": "#008f95",
        "limit": 20,
    },
    {
        "id": "gamebusiness",
        "name": "GameBusiness.jp",
        "site": "https://www.gamebusiness.jp/category/development/",
        "feed": "https://www.gamebusiness.jp/rss/index.rdf",
        "accent": "#d14b3f",
        "limit": 20,
    },
    {
        "id": "automaton-interviews",
        "name": "AUTOMATON Interviews",
        "site": "https://automaton-media.com/devlog/interview/",
        "feed": "https://automaton-media.com/devlog/interview/feed/",
        "accent": "#5b6472",
        "limit": 20,
    },
    {
        "id": "automaton",
        "name": "AUTOMATON",
        "site": "https://automaton-media.com/",
        "feed": "https://automaton-media.com/feed/",
        "accent": "#e6504f",
        "limit": 20,
    },
    {
        "id": "unreal-engine",
        "name": "Unreal Engine",
        "site": "https://www.unrealengine.com/",
        "feed": "https://www.unrealengine.com/rss?lang=en-US",
        "accent": "#4b75ff",
        "limit": 20,
    },
    {
        "id": "blender-developers",
        "name": "Blender Developers",
        "site": "https://code.blender.org/",
        "feed": "https://code.blender.org/feed/",
        "accent": "#f18a21",
        "limit": 20,
    },
)

SOURCE_ACCENTS = ("#4b75ff", "#f18a21", "#61d0c8", "#ff7857", "#a77bff", "#d7ff57")


@contextmanager
def archive_connection() -> Iterator[sqlite3.Connection]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(ARCHIVE_DB_FILE, timeout=10)
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


def initialize_archive_db(force: bool = False) -> None:
    global ARCHIVE_INITIALIZED
    if ARCHIVE_INITIALIZED and not force:
        return
    with ARCHIVE_INIT_LOCK:
        if ARCHIVE_INITIALIZED and not force:
            return
        with archive_connection() as connection:
            connection.execute(
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
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS articles_published_idx ON articles(published_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS articles_source_idx ON articles(source_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS article_state (
                    article_id TEXT PRIMARY KEY,
                    is_read INTEGER NOT NULL DEFAULT 0,
                    is_saved INTEGER NOT NULL DEFAULT 0,
                    is_archived INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    feedback_value INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
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
                )
                """
            )
            now = datetime.now(timezone.utc).isoformat()
            for order, source in enumerate(FEEDS):
                connection.execute(
                    """
                    INSERT INTO sources (
                        id, name, site, feed, accent, item_limit, enabled,
                        is_builtin, sort_order, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        site = excluded.site,
                        feed = excluded.feed,
                        accent = excluded.accent,
                        item_limit = excluded.item_limit,
                        is_builtin = 1,
                        sort_order = excluded.sort_order,
                        updated_at = excluded.updated_at
                    """,
                    (
                        source["id"], source["name"], source["site"], source["feed"],
                        source["accent"], int(source.get("limit", MAX_ITEMS_PER_SOURCE)),
                        order, now, now,
                    ),
                )
        ARCHIVE_INITIALIZED = True


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


def list_source_configs(enabled_only: bool = False) -> list[dict[str, Any]]:
    initialize_archive_db()
    query = "SELECT * FROM sources"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY sort_order, name COLLATE NOCASE"
    with archive_connection() as connection:
        return [source_row_to_dict(row) for row in connection.execute(query).fetchall()]


def source_config(source_id: str) -> dict[str, Any] | None:
    initialize_archive_db()
    with archive_connection() as connection:
        row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return source_row_to_dict(row) if row else None


def validated_http_url(value: Any, label: str, required: bool = True) -> str:
    if value in (None, "") and not required:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a URL.")
    clean = value.strip()
    if not clean or len(clean) > MAX_SOURCE_URL_LENGTH:
        raise ValueError(f"{label} must be a valid HTTP or HTTPS URL.")
    parsed = urllib.parse.urlsplit(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be a valid HTTP or HTTPS URL.")
    return urllib.parse.urlunsplit(parsed)


def invalidate_feed_cache() -> None:
    try:
        CACHE_FILE.unlink()
    except FileNotFoundError:
        pass


def add_source_config(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Source details must be an object.")
    feed = validated_http_url(payload.get("feed"), "Feed URL")
    site = validated_http_url(payload.get("site"), "Website URL", required=False)
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
    now = datetime.now(timezone.utc).isoformat()
    initialize_archive_db()
    try:
        with archive_connection() as connection:
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
    invalidate_feed_cache()
    return source_config(source_id) or {}


def set_source_enabled(source_id: Any, enabled: Any) -> dict[str, Any]:
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("A source id is required.")
    if not isinstance(enabled, bool):
        raise ValueError("Enabled must be true or false.")
    initialize_archive_db()
    now = datetime.now(timezone.utc).isoformat()
    with archive_connection() as connection:
        cursor = connection.execute(
            "UPDATE sources SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), now, source_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Source not found.")
    invalidate_feed_cache()
    return source_config(source_id) or {}

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "referrer",
    "source",
}

ASCII_STOPWORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "asset",
    "assets",
    "available",
    "best",
    "cg",
    "dev",
    "development",
    "engine",
    "for",
    "free",
    "from",
    "game",
    "games",
    "gets",
    "how",
    "into",
    "latest",
    "new",
    "news",
    "now",
    "release",
    "released",
    "software",
    "the",
    "this",
    "tool",
    "tools",
    "using",
    "version",
    "video",
    "with",
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def child_text(element: ET.Element, names: set[str]) -> str:
    for child in element:
        if local_name(child.tag) in names:
            value = "".join(child.itertext()).strip()
            if value:
                return value
    return ""


def preferred_child_text(element: ET.Element, names: tuple[str, ...]) -> str:
    """Return the richest matching field instead of relying on feed order."""
    children = list(element)
    for name in names:
        for child in children:
            if local_name(child.tag) == name:
                value = "".join(child.itertext()).strip()
                if value:
                    return value
    return ""


def strip_markup(value: str) -> str:
    if not value:
        return ""
    value = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def parse_date(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass

    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def canonical_url(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"}:
            return ""
        query = [
            (key, item)
            for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
        ]
        path = parsed.path.rstrip("/") or "/"
        return urllib.parse.urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), path, urllib.parse.urlencode(query), "")
        )
    except ValueError:
        return value.strip()


def article_link(item: ET.Element) -> str:
    text_link = child_text(item, {"link"})
    if text_link.startswith(("http://", "https://")):
        return canonical_url(text_link)
    for child in item:
        if local_name(child.tag) == "link":
            href = child.attrib.get("href", "")
            rel = child.attrib.get("rel", "alternate")
            if href and rel in {"alternate", ""}:
                return canonical_url(href)
    guid = child_text(item, {"guid", "id"})
    return canonical_url(guid)


def first_image(item: ET.Element, raw_summary: str) -> str:
    for descendant in item.iter():
        name = local_name(descendant.tag)
        if name not in {"thumbnail", "content", "enclosure"}:
            continue
        candidate = descendant.attrib.get("url", "") or descendant.attrib.get("href", "")
        media_type = descendant.attrib.get("type", "")
        if candidate and (name == "thumbnail" or media_type.startswith("image/") or re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", candidate, re.I)):
            return canonical_url(html.unescape(candidate))

    match = re.search(r"<img[^>]+src=[\"']([^\"']+)", raw_summary, re.I)
    if match:
        return canonical_url(html.unescape(match.group(1)))
    return ""


def extract_page_image(markup: str, base_url: str) -> str:
    """Read the standard social-preview image regardless of attribute order."""
    preferred: dict[str, str] = {}
    for tag in re.findall(r"<meta\b[^>]*>", markup, flags=re.I):
        attributes = {
            name.lower(): html.unescape(value.strip())
            for name, _, value in re.findall(
                r"([\w:-]+)\s*=\s*([\"'])(.*?)\2", tag, flags=re.I | re.S
            )
        }
        key = attributes.get("property", attributes.get("name", "")).lower()
        content = attributes.get("content", "")
        if key and content:
            preferred[key] = content

    for key in ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src"):
        if preferred.get(key):
            return canonical_url(urllib.parse.urljoin(base_url, preferred[key]))

    for tag in re.findall(r"<link\b[^>]*>", markup, flags=re.I):
        attributes = {
            name.lower(): html.unescape(value.strip())
            for name, _, value in re.findall(
                r"([\w:-]+)\s*=\s*([\"'])(.*?)\2", tag, flags=re.I | re.S
            )
        }
        if "image_src" in attributes.get("rel", "").lower() and attributes.get("href"):
            return canonical_url(urllib.parse.urljoin(base_url, attributes["href"]))
    return ""


def fetch_page_image(article_url: str) -> str:
    request = urllib.request.Request(
        article_url,
        headers={
            "User-Agent": "CGSignal/1.0 (local personal RSS reader)",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=18) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return ""
            charset = response.headers.get_content_charset() or "utf-8"
            markup = response.read(2_500_000).decode(charset, errors="replace")
        return extract_page_image(markup, article_url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, UnicodeError):
        return ""


def read_image_index() -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(IMAGE_INDEX_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def write_image_index(index: dict[str, dict[str, Any]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = IMAGE_INDEX_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    temporary.replace(IMAGE_INDEX_FILE)


def enrich_missing_images(articles: list[dict[str, Any]]) -> None:
    missing = [article for article in articles if not article.get("image")]
    if not missing:
        return

    now = time.time()
    index = read_image_index()
    to_fetch: dict[str, list[dict[str, Any]]] = {}
    for article in missing:
        url = article["url"]
        cached = index.get(url, {})
        age = now - float(cached.get("checked_at", 0))
        if age < IMAGE_INDEX_TTL_SECONDS:
            article["image"] = cached.get("image", "")
        else:
            to_fetch.setdefault(url, []).append(article)

    if not to_fetch:
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        fetched = dict(zip(to_fetch, executor.map(fetch_page_image, to_fetch)))

    for url, image_url in fetched.items():
        index[url] = {"image": image_url, "checked_at": now}
        for article in to_fetch[url]:
            article["image"] = image_url

    try:
        write_image_index(index)
    except OSError:
        pass


def outbound_links(raw_summary: str, article_url: str) -> list[str]:
    own_domain = urllib.parse.urlsplit(article_url).netloc.lower()
    results: list[str] = []
    for match in re.finditer(r"href=[\"']([^\"']+)", raw_summary, re.I):
        candidate = canonical_url(html.unescape(match.group(1)))
        if not candidate:
            continue
        domain = urllib.parse.urlsplit(candidate).netloc.lower()
        if domain and domain != own_domain and candidate not in results:
            results.append(candidate)
    return results[:8]


def classify_topic(title: str, summary: str) -> str:
    value = f"{title} {summary}".lower()
    groups = (
        ("Engines", ("unreal", "unity", "godot", "ue5", "ue 5", "ゲームエンジン")),
        ("3D & Art", ("blender", "maya", "houdini", "zbrush", "substance 3d", "substance painter", "substance designer", "spine 2d", "esoteric software", "animation", "vfx", "render", "modeling", "modelling", "sculpt", "3dcg", "アニメーション", "モデリング", "レンダリング")),
        ("Tools & Assets", ("plugin", "asset", "software", "tool", "adobe", "substance", "nuke", "プラグイン", "アセット", "ツール", "ソフト")),
        ("Game Development", ("game dev", "gamedev", "indie", "steam", "nintendo", "playstation", "xbox", "ゲーム開発", "インディー", "ゲーム制作")),
        ("Industry", ("studio", "career", "jobs", "business", "event", "interview", "スタジオ", "求人", "イベント", "インタビュー")),
    )
    for topic, keywords in groups:
        if any(keyword in value for keyword in keywords):
            return topic
    return "General"


INDUSTRY_TERMS = (
    "acquisition", "bankruptcy", "business", "ceo", "company", "copyright",
    "deal", "earnings", "executive", "funding", "hiring", "industry",
    "investment", "job", "jobs", "labor", "lawsuit", "layoff", "legal", "market",
    "merger", "partnership", "policy", "price", "pricing", "profit",
    "publisher", "revenue", "sales", "shutdown", "studio closes", "union",
    "事業", "企業", "価格", "値上げ", "労働", "合併", "売上", "契約", "市場",
    "投資", "採用", "提携", "株主", "決算", "利益", "業界", "求人", "社長",
    "経営", "著作権", "解雇", "訴訟", "設立", "買収", "資金", "閉鎖", "倒産",
)

TECH_TERMS = (
    "animation", "beta", "blender", "breakdown", "developer", "engine",
    "feature", "gameplay", "houdini", "modeling", "modelling", "open source",
    "performance", "pipeline", "plugin", "release", "render", "rigging",
    "shader", "spine", "substance", "technique", "technical", "technology",
    "tool", "tutorial", "unreal", "update", "version", "vfx", "workflow",
    "アニメーション", "アップデート", "エンジン", "オープンソース", "ゲーム制作",
    "シェーダー", "チュートリアル", "ツール", "テクニック", "バージョン", "プラグイン",
    "ベータ", "メイキング", "モデリング", "リギング", "リリース", "レンダリング",
    "ワークフロー", "制作", "技術", "機能", "開発",
)

INDUSTRY_SOURCE_PRIOR = {
    "cartoon-brew": 1,
    "gamebusiness": 2,
}

TECH_SOURCE_PRIOR = {
    "80-level": 1,
    "cgworld": 1,
    "gamemakers": 2,
    "3dnchu": 2,
    "cginterest": 2,
    "befores-afters": 2,
    "game-developer": 1,
    "siggraph": 2,
    "automaton-interviews": 1,
    "automaton": 2,
    "unreal-engine": 3,
    "blender-developers": 3,
}

INTEREST_TERMS = (
    ("Unreal Engine", ("unreal engine", "unreal", "ue5", "ue 5"), 28),
    (
        "Substance 3D",
        (
            "substance 3d", "adobe substance", "substance painter",
            "substance 3d painter", "substance designer", "substance 3d designer",
        ),
        25,
    ),
    ("Blender", ("blender",), 25),
    ("Houdini", ("houdini", "sidefx"), 22),
    ("Spine", ("spine 2d", "esoteric software", "spine animation"), 22),
    ("Unity", ("unity", "unity engine", "unity 6", "unity technologies", "unity editor", "ユニティ"), 20),
    (
        "AI",
        (
            "ai", "artificial intelligence", "generative ai", "genai", "machine learning",
            "neural network", "diffusion model", "large language model", "生成ai", "生成 ai",
            "人工知能", "機械学習",
        ),
        12,
    ),
)

SOFTWARE_MATCH_TERMS = (
    *((label, terms) for label, terms, _points in INTEREST_TERMS if label != "Spine"),
)

PRODUCTION_TOPIC_TERMS = (
    (
        "Modeling & sculpting",
        (
            "modeling", "modelling", "sculpt", "zbrush", "retopology",
            "photogrammetry", "モデリング", "スカルプト", "造形", "フォトグラメトリ",
        ),
    ),
    (
        "Materials & texturing",
        (
            "material", "texture", "texturing", "substance", "lookdev",
            "look development", "材質", "質感", "テクスチャ", "マテリアル", "ルックデブ",
        ),
    ),
    (
        "Animation, rigging & mocap",
        (
            "animation", "animating", "rigging", "motion capture", "mocap",
            "facial capture", "facial animation", "character motion", "アニメーション",
            "リギング", "モーションキャプチャ", "フェイシャル", "モーション制作",
        ),
    ),
    (
        "Lighting & rendering",
        (
            "lighting", "render", "ray tracing", "path tracing", "global illumination",
            "lumen", "cycles", "ライティング", "レンダリング", "レイトレーシング",
            "パストレーシング", "照明",
        ),
    ),
    (
        "VFX, simulation & procedural",
        (
            "vfx", "visual effects", "simulation", "procedural", "particle", "fluid",
            "destruction", "niagara", "geometry nodes", "houdini", "エフェクト",
            "シミュレーション", "プロシージャル", "パーティクル", "流体", "破壊表現",
        ),
    ),
    (
        "Technical art & optimization",
        (
            "technical art", "tech art", "optimization", "optimisation", "performance",
            "shader", "benchmark", "profiling", "frame rate", "lod", "nanite",
            "テクニカルアート", "最適化", "パフォーマンス", "シェーダー", "ベンチマーク",
        ),
    ),
    (
        "Pipeline, tools & automation",
        (
            "pipeline", "workflow", "plugin", "add-on", "addon", "automation", "scripting",
            "export", "import", "integration", "tool development", "パイプライン",
            "ワークフロー", "プラグイン", "アドオン", "自動化", "スクリプト", "連携",
        ),
    ),
    (
        "Game design & development",
        (
            "game design", "game development", "gameplay", "level design", "multiplayer",
            "prototype", "postmortem", "development diary", "ゲームデザイン", "ゲーム開発",
            "ゲーム制作", "ゲームプレイ", "レベルデザイン", "プロトタイプ", "開発日誌",
        ),
    ),
    (
        "Breakdowns & production stories",
        (
            "breakdown", "making of", "behind the scenes", "case study", "production story",
            "production diary", "dev diary", "メイキング", "制作事例", "制作の裏側", "事例紹介",
            "開発秘話", "制作工程", "インタビュー",
        ),
    ),
    (
        "Research & emerging tech",
        (
            "research", "researcher", "paper", "siggraph", "machine learning", "neural",
            "gaussian splatting", "radiance field", "generative", "artificial intelligence",
            "研究", "論文", "機械学習", "ニューラル", "生成ai", "生成 ai", "新技術",
        ),
    ),
    (
        "Releases & product updates",
        (
            "release", "released", "update", "version", "beta", "roadmap", "new feature",
            "now available", "リリース", "アップデート", "バージョン", "ベータ", "新機能",
            "提供開始", "公開", "ロードマップ",
        ),
    ),
    (
        "Assets & inspiration",
        (
            "character art", "environment art", "concept art", "asset pack", "showcase",
            "gallery", "portfolio", "artstation", "inspiration", "キャラクターアート",
            "背景アート", "コンセプトアート", "アセット", "作品紹介", "ショーケース",
        ),
    ),
)

EVENT_TERM_GROUPS = (
    ("release", ("release", "released", "launch", "リリース", "発売", "公開")),
    ("update", ("update", "version", "アップデート", "バージョン", "新機能")),
    ("acquisition", ("acquisition", "acquires", "acquired", "merger", "買収", "合併")),
    ("layoffs", ("layoff", "job cuts", "workforce reduction", "解雇", "人員削減")),
    ("closure", ("shutdown", "shuts down", "closure", "closes", "閉鎖", "終了", "倒産")),
    ("retirement", ("retires", "retirement", "steps down", "引退", "退任")),
    ("legal", ("lawsuit", "legal action", "copyright", "訴訟", "著作権", "権利侵害")),
    ("funding", ("funding", "investment", "資金調達", "投資")),
    ("partnership", ("partnership", "partners with", "collaboration", "提携", "協業")),
    ("pricing", ("price increase", "pricing", "値上げ", "価格改定")),
    ("delay", ("delayed", "postponed", "延期")),
    ("cancellation", ("cancelled", "canceled", "discontinued", "中止", "開発中止")),
)

DEPTH_TERMS = (
    ("Tutorial or breakdown", ("tutorial", "breakdown", "how to", "making of", "チュートリアル", "メイキング", "解説"), 10),
    ("Workflow or pipeline", ("workflow", "pipeline", "ワークフロー", "パイプライン"), 8),
    ("Rendering or shaders", ("render", "shader", "lighting", "レンダリング", "シェーダー", "ライティング"), 6),
    ("Performance", ("performance", "optimization", "optimisation", "benchmark", "最適化", "パフォーマンス"), 6),
    ("Procedural technique", ("procedural", "simulation", "node", "プロシージャル", "シミュレーション", "ノード"), 6),
    ("Product update", ("release", "update", "version", "beta", "リリース", "アップデート", "バージョン", "ベータ"), 3),
)

PRIORITY_SOURCE_BONUS = {
    "unreal-engine": 8,
    "blender-developers": 8,
    "siggraph": 6,
    "gamemakers": 3,
    "game-developer": 3,
}

PROMOTIONAL_TERMS = (
    "sale", "discount", "giveaway", "sponsored", "job digest", "job picks",
    "bundle", "セール", "割引", "求人まとめ", "プレゼント",
)


def classify_lane(title: str, summary: str, source_id: str) -> str:
    value = f"{title} {summary}".lower()
    industry_matches = sum(term in value for term in INDUSTRY_TERMS)
    technical_matches = sum(term in value for term in TECH_TERMS)
    industry_score = INDUSTRY_SOURCE_PRIOR.get(source_id, 0) + industry_matches
    technical_score = TECH_SOURCE_PRIOR.get(source_id, 0) + technical_matches
    if industry_score > technical_score or (
        industry_score == technical_score and industry_matches > 0
    ):
        return "Industry & Business"
    return "Tech & Development"


def term_position(value: str, term: str) -> int:
    """Find a term without treating ASCII word fragments as product names."""

    if term.isascii():
        match = re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", value)
        return match.start() if match else -1
    return value.find(term)


def contains_term(value: str, term: str) -> bool:
    return term_position(value, term) >= 0


def score_relevance(
    title: str,
    summary: str,
    source_id: str,
    lane: str,
    source_count: int = 1,
) -> tuple[int, list[str]]:
    """Return a transparent, local priority score tailored to the user's tools."""

    title_value = title.lower()
    value = f"{title} {summary}".lower()
    score = 18 if lane == "Tech & Development" else 6
    reasons: list[str] = []

    for label, terms, points in INTEREST_TERMS:
        if any(contains_term(value, term) for term in terms):
            score += points
            if any(contains_term(title_value, term) for term in terms):
                score += 4
            reasons.append(label)

    for label, terms, points in DEPTH_TERMS:
        if any(term in value for term in terms):
            score += points
            reasons.append(label)

    source_bonus = PRIORITY_SOURCE_BONUS.get(source_id, 0)
    if source_bonus:
        score += source_bonus
        if source_bonus >= 6:
            reasons.append("First-party or research source")

    if source_count > 1:
        score += min(6, (source_count - 1) * 3)
        reasons.append("Multiple sources")

    if any(term in value for term in PROMOTIONAL_TERMS):
        score -= 18

    # Keep explanations compact and deterministic while preserving order.
    unique_reasons = list(dict.fromkeys(reasons))[:3]
    return max(0, min(100, score)), unique_reasons


def classify_software(title: str, summary: str) -> list[str]:
    """Return software tags ordered by prominence without duplicating cards."""

    title_value = title.lower()
    summary_value = summary.lower()
    matches: list[tuple[tuple[int, int, int], str]] = []
    for order, (label, terms) in enumerate(SOFTWARE_MATCH_TERMS):
        title_positions = [position for term in terms if (position := term_position(title_value, term)) >= 0]
        summary_positions = [position for term in terms if (position := term_position(summary_value, term)) >= 0]
        if title_positions:
            matches.append(((0, min(title_positions), order), label))
        elif summary_positions:
            matches.append(((1, min(summary_positions), order), label))

    labels = [label for _rank, label in sorted(matches)]
    return labels


def classify_topics(title: str, summary: str, lane: str) -> list[str]:
    """Return overlapping subcategories only for general production coverage."""

    if lane != "Tech & Development":
        return []
    value = f"{title} {summary}".lower()
    labels = [label for label, terms in PRODUCTION_TOPIC_TERMS if any(term in value for term in terms)]
    if labels:
        return labels
    return ["Other production"]


def parse_feed_document(xml_bytes: bytes) -> ET.Element:
    """Parse a feed, ignoring content incorrectly appended after its root element."""

    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError as original_error:
        for closing_tag in (b"</rss>", b"</feed>", b"</rdf:RDF>"):
            end = xml_bytes.rfind(closing_tag)
            if end < 0:
                continue
            candidate = xml_bytes[: end + len(closing_tag)]
            try:
                return ET.fromstring(candidate)
            except ET.ParseError:
                continue
        raise original_error


def fetch_source(source: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        source["feed"],
        headers={
            "User-Agent": "CGSignal/1.0 (local personal RSS reader)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=22) as response:
            xml_bytes = response.read(6_000_000)
        root = parse_feed_document(xml_bytes)
        entries = [node for node in root.iter() if local_name(node.tag) in {"item", "entry"}]
        articles: list[dict[str, Any]] = []
        item_limit = int(source.get("limit", MAX_ITEMS_PER_SOURCE))
        for item in entries[:item_limit]:
            title = strip_markup(child_text(item, {"title"}))
            url = article_link(item)
            if not title or not url:
                continue
            raw_summary = preferred_child_text(item, ("encoded", "content", "description", "summary"))
            summary = strip_markup(raw_summary)
            published_text = child_text(item, {"pubdate", "published", "updated", "date", "created"})
            published = parse_date(published_text)
            article_id = hashlib.sha1(f"{source['id']}|{url}".encode("utf-8")).hexdigest()[:18]
            articles.append(
                {
                    "id": article_id,
                    "title": title,
                    "url": url,
                    "summary": summary[:900],
                    "image": first_image(item, raw_summary),
                    "published_at": published.isoformat(),
                    "timestamp": published.timestamp(),
                    "source": source["name"],
                    "source_id": source["id"],
                    "source_site": source["site"],
                    "accent": source["accent"],
                    "topic": classify_topic(title, summary),
                    "lane": classify_lane(title, summary, source["id"]),
                    "_refs": outbound_links(raw_summary, url),
                }
            )
        return {
            "source": source,
            "articles": articles,
            "ok": True,
            "message": "",
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ET.ParseError, OSError) as exc:
        return {
            "source": source,
            "articles": [],
            "ok": False,
            "message": str(exc),
            "duration_ms": round((time.monotonic() - started) * 1000),
        }


def test_source(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Source test details must be an object.")
    source_id = payload.get("id")
    if isinstance(source_id, str) and source_id:
        source = source_config(source_id)
        if not source:
            raise ValueError("Source not found.")
    else:
        feed = validated_http_url(payload.get("feed"), "Feed URL")
        site = validated_http_url(payload.get("site"), "Website URL", required=False)
        parsed = urllib.parse.urlsplit(feed)
        raw_name = payload.get("name", "")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        source = {
            "id": "source-test",
            "name": name or parsed.hostname or "Feed test",
            "site": site,
            "feed": feed,
            "accent": "#7fa9ff",
            "limit": 3,
        }
    result = fetch_source(source)
    return {
        "ok": result["ok"],
        "message": result["message"],
        "count": len(result["articles"]),
        "duration_ms": result["duration_ms"],
        "sample_titles": [article["title"] for article in result["articles"][:3]],
        "source": source,
    }


def normalized_title(value: str) -> str:
    value = html.unescape(value).lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^\w\u3040-\u30ff\u3400-\u9fff]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def word_tokens(value: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9][a-z0-9.+-]{2,}|[\u3040-\u30ff\u3400-\u9fff]{2,}", normalized_title(value)))
    return {token for token in tokens if token not in ASCII_STOPWORDS}


def ascii_signature(value: str) -> set[str]:
    tokens = set(re.findall(r"[a-z][a-z0-9.+-]{2,}|\d+(?:\.\d+)+", value.lower()))
    return {token.strip("-+.") for token in tokens if token.strip("-+.") not in ASCII_STOPWORDS}


def event_signatures(value: str) -> set[str]:
    normalized = value.lower()
    return {
        label
        for label, terms in EVENT_TERM_GROUPS
        if any(term in normalized for term in terms)
    }


def same_story(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["url"] == right["url"]:
        return True

    left_date = datetime.fromisoformat(left["published_at"])
    right_date = datetime.fromisoformat(right["published_at"])
    if abs((left_date - right_date).total_seconds()) > 5 * 86400:
        return False

    if set(left.get("_refs", [])) & set(right.get("_refs", [])):
        return True

    left_title = normalized_title(left["title"])
    right_title = normalized_title(right["title"])
    if left_title == right_title:
        return True

    ratio = SequenceMatcher(None, left_title, right_title).ratio()
    if min(len(left_title), len(right_title)) >= 16 and ratio >= 0.84:
        return True

    left_tokens = word_tokens(left_title)
    right_tokens = word_tokens(right_title)
    shared = left_tokens & right_tokens
    union = left_tokens | right_tokens
    if len(shared) >= 4 and union and len(shared) / len(union) >= 0.68:
        return True

    # Japanese/English coverage often shares product names and version numbers.
    ascii_shared = ascii_signature(left["title"]) & ascii_signature(right["title"])
    version_match = any(any(character.isdigit() for character in token) for token in ascii_shared)
    distinctive = sum(len(token) >= 6 for token in ascii_shared)
    event_shared = event_signatures(left["title"]) & event_signatures(right["title"])
    event_noise = {
        "announcement", "announces", "creator", "developer", "official", "industry",
        "latest", "major", "release", "released", "update", "version",
    }
    event_entities = {token for token in ascii_shared if len(token) >= 4 and token not in event_noise}
    if event_shared and (len(event_entities) >= 2 or version_match):
        return True
    # Three shared tokens keeps common product/version pairs from collapsing an
    # unrelated tutorial published near a release announcement.
    return len(ascii_shared) >= 3 and (version_match or distinctive >= 2)


def public_article(article: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in article.items() if not key.startswith("_") and key != "timestamp"}


def cluster_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for article in sorted(articles, key=lambda item: item["timestamp"], reverse=True):
        match: dict[str, Any] | None = None
        for cluster in clusters:
            if abs(article["timestamp"] - cluster["_primary"]["timestamp"]) > 5 * 86400:
                continue
            if same_story(article, cluster["_primary"]):
                match = cluster
                break

        if match is None:
            clusters.append({"_primary": article, "_members": [article]})
            continue

        match["_members"].append(article)
        primary = match["_primary"]
        if not primary.get("image") and article.get("image"):
            primary["image"] = article["image"]
        if len(article.get("summary", "")) > len(primary.get("summary", "")):
            primary["summary"] = article["summary"]

    output: list[dict[str, Any]] = []
    for cluster in clusters:
        members = cluster["_members"]
        primary = dict(cluster["_primary"])
        unique_sources: dict[str, dict[str, Any]] = {}
        for member in members:
            unique_sources[member["source_id"]] = member
        related = [
            {
                "title": member["title"],
                "url": member["url"],
                "source": member["source"],
                "source_id": member["source_id"],
                "accent": member["accent"],
                "published_at": member["published_at"],
            }
            for member in members
            if member["id"] != primary["id"]
        ]
        public = public_article(primary)
        public["related"] = related
        public["source_count"] = len(unique_sources)
        public["cluster_size"] = len(members)
        public["sources"] = [
            {
                "id": member["source_id"],
                "name": member["source"],
                "accent": member["accent"],
            }
            for member in unique_sources.values()
        ]
        priority_score, priority_reasons = score_relevance(
            public["title"],
            public.get("summary", ""),
            public["source_id"],
            public.get("lane", "Tech & Development"),
            len(unique_sources),
        )
        public["priority_score"] = priority_score
        public["priority_reasons"] = priority_reasons
        software_tags = classify_software(public["title"], public.get("summary", ""))
        public["software_tags"] = software_tags
        public["software_group"] = software_tags[0] if software_tags else (
            "Industry context" if public.get("lane") == "Industry & Business" else "Production techniques"
        )
        public["topic_tags"] = (
            classify_topics(
                public["title"],
                public.get("summary", ""),
                public.get("lane", "Tech & Development"),
            )
            if public["software_group"] == "Production techniques"
            else []
        )
        output.append(public)
    return output


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


def archive_articles(articles: list[dict[str, Any]]) -> int:
    if not articles:
        return archive_article_count()
    initialize_archive_db()
    now = datetime.now(timezone.utc).isoformat()
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
    with archive_connection() as connection:
        connection.executemany(
            """
            INSERT INTO articles (
                id, title, url, summary, source, source_id, published_at, lane,
                software_group, software_tags, topic_tags, sources_text,
                search_text, data_json, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                url = excluded.url,
                summary = excluded.summary,
                source = excluded.source,
                source_id = excluded.source_id,
                published_at = excluded.published_at,
                lane = excluded.lane,
                software_group = excluded.software_group,
                software_tags = excluded.software_tags,
                topic_tags = excluded.topic_tags,
                sources_text = excluded.sources_text,
                search_text = excluded.search_text,
                data_json = excluded.data_json,
                last_seen_at = excluded.last_seen_at
            """,
            rows,
        )
        count = connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    return int(count)


def archive_article_count() -> int:
    initialize_archive_db()
    with archive_connection() as connection:
        return int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0])


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
    "ai": ("software", "ai"),
    "genai": ("software", "ai"),
}


def parse_archive_search(query: str) -> list[dict[str, Any]]:
    normalized = re.sub(
        r"#unreal\s+engine\b", '#software:"Unreal Engine"', query, flags=re.IGNORECASE
    )
    normalized = re.sub(
        r"#substance\s+(?:painter|designer|3d)\b",
        '#software:"Substance 3D"', normalized, flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"#production\s+techniques\b",
        '#software:"Production techniques"', normalized, flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"#industry\s+context\b",
        '#software:"Industry context"', normalized, flags=re.IGNORECASE,
    )
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
                    field = candidate_field.lower()
                    value = candidate_value
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
    field = token["field"]
    value = token["value"]
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
            "read": "COALESCE(s.is_read, 0) = 1",
            "unread": "COALESCE(s.is_read, 0) = 0",
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


def query_archive(
    query: str = "",
    lane: str = "All",
    source_ids: list[str] | None = None,
    limit: int = 60,
    offset: int = 0,
    new_after: str = "",
) -> dict[str, Any]:
    initialize_archive_db()
    limit = max(1, min(MAX_ARCHIVE_PAGE_SIZE, int(limit)))
    offset = max(0, int(offset))
    clauses: list[str] = []
    parameters: list[Any] = []
    for token in parse_archive_search(query):
        clause, token_parameters = archive_token_sql(token, new_after)
        clauses.append(clause)
        parameters.extend(token_parameters)
    if lane in {"Tech & Development", "Industry & Business"}:
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
    with archive_connection() as connection:
        total = int(connection.execute(f"SELECT COUNT(*) {base} {where}", parameters).fetchone()[0])
        rows = connection.execute(
            f"""
            SELECT a.data_json, a.first_seen_at, a.last_seen_at
            {base} {where}
            ORDER BY a.published_at DESC
            LIMIT ? OFFSET ?
            """,
            [*parameters, limit, offset],
        ).fetchall()
    articles = []
    for row in rows:
        article = json.loads(row["data_json"])
        article["archive_first_seen_at"] = row["first_seen_at"]
        article["archive_last_seen_at"] = row["last_seen_at"]
        articles.append(article)
    return {
        "articles": articles,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(articles) < total,
        "archive_count": archive_article_count(),
    }


def read_cache() -> dict[str, Any] | None:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def write_cache(payload: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CACHE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(CACHE_FILE)


def normalize_string_list(values: Any, limit: int, max_length: int = 80) -> list[str]:
    if not isinstance(values, list):
        return []
    valid = [value for value in values if isinstance(value, str) and 1 <= len(value) <= max_length]
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
        normalized_item = {
            "id": article_id,
            "value": value,
            "source_id": source_id,
            "software_tags": normalize_string_list(item.get("software_tags", []), 12),
            "topic_tags": normalize_string_list(item.get("topic_tags", []), 12),
        }
        by_id.pop(article_id, None)
        by_id[article_id] = normalized_item
    return list(by_id.values())[-MAX_FEEDBACK_ITEMS:]


def normalize_user_state(payload: Any) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    normalized: dict[str, Any] = {}
    for key in ("read", "saved", "archived"):
        normalized[key] = normalize_string_list(source.get(key, []), MAX_STATE_IDS)

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
        for source_id in normalize_string_list(source.get("reduced_sources", []), MAX_STATE_SOURCES)
        if source_id not in muted
    ]
    normalized["updated_at"] = datetime.now(timezone.utc).isoformat()
    return normalized


def sync_user_state_to_archive(state: dict[str, Any]) -> None:
    initialize_archive_db()
    read_ids = set(state.get("read", []))
    saved_ids = set(state.get("saved", []))
    archived_ids = set(state.get("archived", []))
    notes = state.get("notes", {})
    feedback = {item["id"]: item["value"] for item in state.get("feedback", [])}
    article_ids = read_ids | saved_ids | archived_ids | set(notes) | set(feedback)
    updated_at = state.get("updated_at") or datetime.now(timezone.utc).isoformat()
    rows = [
        (
            article_id,
            int(article_id in read_ids),
            int(article_id in saved_ids),
            int(article_id in archived_ids),
            notes.get(article_id, ""),
            int(feedback.get(article_id, 0)),
            updated_at,
        )
        for article_id in article_ids
    ]
    with archive_connection() as connection:
        connection.execute("DELETE FROM article_state")
        if rows:
            connection.executemany(
                """
                INSERT INTO article_state (
                    article_id, is_read, is_saved, is_archived,
                    note, feedback_value, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )


def read_user_state() -> dict[str, Any]:
    with USER_STATE_LOCK:
        try:
            state = normalize_user_state(json.loads(USER_STATE_FILE.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            state = normalize_user_state({})
    sync_user_state_to_archive(state)
    return state


def write_user_state(payload: Any) -> dict[str, Any]:
    normalized = normalize_user_state(payload)
    with USER_STATE_LOCK:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = USER_STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(normalized, ensure_ascii=False), encoding="utf-8")
        temporary.replace(USER_STATE_FILE)
    sync_user_state_to_archive(normalized)
    return normalized


def build_feed(force: bool = False) -> dict[str, Any]:
    cached = read_cache()
    if cached and not force:
        generated = datetime.fromisoformat(cached["generated_at"])
        if (datetime.now(timezone.utc) - generated).total_seconds() < CACHE_TTL_SECONDS:
            try:
                cached["archive_count"] = archive_articles(cached.get("articles", []))
            except (OSError, sqlite3.Error):
                cached["archive_count"] = 0
            cached["cached"] = True
            return cached

    configured_sources = list_source_configs(enabled_only=True)
    if configured_sources:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(configured_sources))) as executor:
            results = list(executor.map(fetch_source, configured_sources))
    else:
        results = []

    all_articles = [article for result in results for article in result["articles"]]
    failed = [result for result in results if not result["ok"]]
    if configured_sources and not all_articles and cached:
        cached["cached"] = True
        cached["stale"] = True
        cached["warnings"] = [f"{item['source']['name']}: {item['message']}" for item in failed]
        cached["archive_count"] = archive_article_count()
        return cached

    enrich_missing_images(all_articles)

    clusters = cluster_articles(all_articles)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
        "stale": False,
        "raw_count": len(all_articles),
        "unique_count": len(clusters),
        "duplicates_collapsed": max(0, len(all_articles) - len(clusters)),
        "articles": clusters,
        "sources": [
            {
                **result["source"],
                "ok": result["ok"],
                "count": len(result["articles"]),
                "duration_ms": result["duration_ms"],
            }
            for result in results
        ],
        "warnings": [f"{item['source']['name']}: {item['message']}" for item in failed],
    }
    try:
        payload["archive_count"] = archive_articles(clusters)
    except (OSError, sqlite3.Error):
        payload["archive_count"] = archive_article_count()
    if configured_sources:
        try:
            write_cache(payload)
        except OSError:
            pass
    return payload


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "CGSignal/1.0"

    def log_message(self, format_string: str, *args: Any) -> None:
        message = format_string % args
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self, maximum_size: int = 50_000) -> Any:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid content length.") from exc
        if content_length <= 0 or content_length > maximum_size:
            raise ValueError("Invalid request payload size.")
        try:
            return json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid JSON request payload.") from exc

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "service": "CG Signal"})
            return
        if parsed.path == "/api/feed":
            force = urllib.parse.parse_qs(parsed.query).get("refresh", ["0"])[0] == "1"
            try:
                self.send_json(build_feed(force=force))
            except Exception as exc:  # Keep the local UI useful even when one feed is malformed.
                self.send_json({"error": "Unable to gather feeds", "detail": str(exc)}, status=500)
            return
        if parsed.path == "/api/state":
            self.send_json(read_user_state())
            return
        if parsed.path == "/api/archive":
            parameters = urllib.parse.parse_qs(parsed.query)
            try:
                source_ids = [
                    source_id
                    for value in parameters.get("sources", [])
                    for source_id in value.split(",")
                    if source_id
                ]
                self.send_json(
                    query_archive(
                        query=parameters.get("q", [""])[0],
                        lane=parameters.get("lane", ["All"])[0],
                        source_ids=source_ids,
                        limit=int(parameters.get("limit", ["60"])[0]),
                        offset=int(parameters.get("offset", ["0"])[0]),
                        new_after=parameters.get("new_after", [""])[0],
                    )
                )
            except (ValueError, sqlite3.Error) as exc:
                self.send_json({"error": "Unable to search archive", "detail": str(exc)}, status=400)
            return
        if parsed.path == "/api/sources":
            self.send_json({"sources": list_source_configs()})
            return

        relative_path = "index.html" if parsed.path in {"", "/"} else parsed.path.lstrip("/")
        candidate = (STATIC_DIR / relative_path).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not candidate.is_file():
            self.send_error(404)
            return

        body = candidate.read_bytes()
        content_type, _ = mimetypes.guess_type(candidate.name)
        if content_type and content_type.startswith("text/"):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        supported_paths = {
            "/api/state", "/api/sources", "/api/sources/test", "/api/sources/toggle"
        }
        if parsed.path not in supported_paths:
            self.send_error(404)
            return
        try:
            maximum_size = 750_000 if parsed.path == "/api/state" else 50_000
            payload = self.read_json_body(maximum_size)
            if parsed.path == "/api/state":
                self.send_json(write_user_state(payload))
            elif parsed.path == "/api/sources":
                self.send_json({"source": add_source_config(payload)}, status=201)
            elif parsed.path == "/api/sources/test":
                self.send_json(test_source(payload))
            else:
                if not isinstance(payload, dict):
                    raise ValueError("Source toggle details must be an object.")
                self.send_json(
                    {"source": set_source_enabled(payload.get("id"), payload.get("enabled"))}
                )
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
        except (OSError, sqlite3.Error) as exc:
            self.send_json({"error": "Unable to update local data", "detail": str(exc)}, status=500)


class DashboardServer(ThreadingHTTPServer):
    """Use an exclusive listener so repeated launches cannot start duplicates."""

    allow_reuse_address = False
    allow_reuse_port = False
    daemon_threads = True

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the private CG Signal RSS dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4310)
    parser.add_argument("--no-browser", action="store_true")
    arguments = parser.parse_args()

    try:
        server = DashboardServer((arguments.host, arguments.port), DashboardHandler)
    except OSError as error:
        raise SystemExit(
            f"CG Signal could not use {arguments.host}:{arguments.port}. "
            "It may already be running."
        ) from error

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    url = f"http://{arguments.host}:{arguments.port}"
    print("\nCG Signal is ready")
    print(f"Open: {url}")
    print("Press Ctrl+C to stop.\n")
    if not arguments.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CG Signal.")
    finally:
        server.server_close()
        try:
            if PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                PID_FILE.unlink()
        except (FileNotFoundError, OSError):
            pass


if __name__ == "__main__":
    main()
