from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = Path(__file__).resolve().parent / "site"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "dist"
MOBILE_HISTORY_DAYS = 100
MAX_MOBILE_ARTICLES = 1500

ARTICLE_FIELDS = (
    "id",
    "title",
    "url",
    "summary",
    "image",
    "published_at",
    "source",
    "source_id",
    "source_site",
    "accent",
    "topic",
    "lane",
    "source_count",
    "cluster_size",
    "priority_score",
    "priority_reasons",
    "software_tags",
    "software_group",
    "topic_tags",
)
SOURCE_FIELDS = ("id", "name", "site", "accent", "ok", "count")
RELATED_FIELDS = ("source", "title", "url", "published_at")
CLUSTER_SOURCE_FIELDS = ("id", "name", "site", "accent")


def allow_fields(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {field: value[field] for field in fields if field in value}


def sanitize_article(value: Any) -> dict[str, Any]:
    article = allow_fields(value, ARTICLE_FIELDS)
    raw = value if isinstance(value, dict) else {}
    article["related"] = [
        allow_fields(item, RELATED_FIELDS)
        for item in raw.get("related", [])
        if isinstance(item, dict)
    ][:8]
    article["sources"] = [
        allow_fields(item, CLUSTER_SOURCE_FIELDS)
        for item in raw.get("sources", [])
        if isinstance(item, dict)
    ][:8]
    return article


def sanitize_feed(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Feed payload must be an object.")
    articles = [
        sanitize_article(article)
        for article in payload.get("articles", [])
        if isinstance(article, dict) and article.get("id") and article.get("title")
    ]
    if not articles:
        raise ValueError("The mobile build received no valid articles; keeping the previous deployment is safer.")
    sources = [
        allow_fields(source, SOURCE_FIELDS)
        for source in payload.get("sources", [])
        if isinstance(source, dict) and source.get("id") and source.get("name")
    ]
    warning_sources = [
        source.strip()[:100]
        for source in payload.get("unavailable_sources", [])
        if isinstance(source, str) and source.strip()
    ]
    for warning in payload.get("warnings", []):
        if isinstance(warning, str) and warning.strip():
            warning_sources.append(warning.split(":", 1)[0].strip()[:100])
    return {
        "schema_version": 1,
        "classification_version": int(payload.get("classification_version", 1) or 1),
        "generated_at": payload.get("generated_at", ""),
        "unique_count": len(articles),
        "duplicates_collapsed": int(payload.get("duplicates_collapsed", 0) or 0),
        "carried_forward_count": int(payload.get("carried_forward_count", 0) or 0),
        "articles": articles,
        "sources": sources,
        "unavailable_sources": list(dict.fromkeys(warning_sources)),
    }


def parsed_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def article_urls(article: dict[str, Any]) -> set[str]:
    urls = {article.get("url", "")}
    urls.update(
        related.get("url", "")
        for related in article.get("related", [])
        if isinstance(related, dict)
    )
    return {url for url in urls if isinstance(url, str) and url}


def merge_feed_history(
    current_payload: Any,
    previous_payload: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Retain a bounded public history when a feed rotates or briefly fails."""
    current = sanitize_feed(current_payload)
    try:
        previous = sanitize_feed(previous_payload)
    except ValueError:
        return current
    if previous["classification_version"] != current["classification_version"]:
        return current

    reference_time = now or parsed_datetime(current.get("generated_at")) or datetime.now(timezone.utc)
    if not reference_time.tzinfo:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    cutoff = reference_time - timedelta(days=MOBILE_HISTORY_DAYS)

    merged_articles = list(current["articles"])
    seen_ids = {article["id"] for article in merged_articles}
    seen_urls: set[str] = set()
    for article in merged_articles:
        seen_urls.update(article_urls(article))

    carried_ids: set[str] = set()
    for article in previous["articles"]:
        published = parsed_datetime(article.get("published_at"))
        urls = article_urls(article)
        if published and published < cutoff:
            continue
        if article["id"] in seen_ids or (urls and urls.intersection(seen_urls)):
            continue
        merged_articles.append(article)
        carried_ids.add(article["id"])
        seen_ids.add(article["id"])
        seen_urls.update(urls)

    merged_articles.sort(
        key=lambda article: parsed_datetime(article.get("published_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    merged_articles = merged_articles[:MAX_MOBILE_ARTICLES]
    retained_ids = {article["id"] for article in merged_articles}

    sources_by_id = {
        source["id"]: source
        for source in previous["sources"]
        if source.get("id")
    }
    sources_by_id.update(
        {
            source["id"]: source
            for source in current["sources"]
            if source.get("id")
        }
    )
    source_counts: dict[str, int] = {}
    for article in merged_articles:
        source_ids = {
            source.get("id")
            for source in article.get("sources", [])
            if isinstance(source, dict) and source.get("id")
        }
        if article.get("source_id"):
            source_ids.add(article["source_id"])
        for source_id in source_ids:
            source_counts[source_id] = source_counts.get(source_id, 0) + 1
    sources = [
        {**source, "count": source_counts.get(source_id, 0)}
        for source_id, source in sources_by_id.items()
    ]

    return {
        **current,
        "unique_count": len(merged_articles),
        "carried_forward_count": len(carried_ids.intersection(retained_ids)),
        "articles": merged_articles,
        "sources": sources,
    }


def gather_feed(request_cache_dir: Path | None = None) -> dict[str, Any]:
    sys.path.insert(0, str(PROJECT_ROOT))
    import server  # pylint: disable=import-outside-toplevel

    with tempfile.TemporaryDirectory(prefix="cg-signal-mobile-") as temporary:
        cache = Path(temporary)
        request_cache = request_cache_dir.resolve() if request_cache_dir else cache
        request_cache.mkdir(parents=True, exist_ok=True)
        server.CACHE_DIR = cache
        server.CACHE_FILE = cache / "feed-cache.json"
        server.FEED_SOURCE_CACHE_FILE = request_cache / "feed-source-cache.json"
        server.IMAGE_INDEX_FILE = request_cache / "image-index.json"
        server.USER_STATE_FILE = cache / "user-state.json"
        server.ARCHIVE_DB_FILE = cache / "cg-signal.db"
        server.PID_FILE = cache / "server.pid"
        server.ARCHIVE_INITIALIZED = False
        payload = server.build_feed(force=True)
        if payload.get("thumbnails_refreshing"):
            server.wait_for_thumbnail_refresh()
            return server.read_cache() or payload
        return payload


def build_site(output: Path, payload: dict[str, Any]) -> Path:
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(SITE_DIR, output)
    for icon_name in ("favicon.ico", "icon-180.png", "icon-192.png", "icon-512.png"):
        shutil.copy2(PROJECT_ROOT / "static" / icon_name, output / icon_name)
    (output / ".nojekyll").write_text("", encoding="utf-8")
    (output / "feed.json").write_text(
        json.dumps(sanitize_feed(payload), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the public-safe CG Signal mobile companion.")
    parser.add_argument("--source-json", type=Path, help="Use an existing feed payload instead of fetching live feeds.")
    parser.add_argument(
        "--previous-json",
        type=Path,
        help="Merge a previous public feed so transient failures do not remove recent articles.",
    )
    parser.add_argument(
        "--request-cache-dir",
        type=Path,
        help="Persist public feed validators and thumbnail lookups between scheduled builds.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    payload = (
        json.loads(arguments.source_json.read_text(encoding="utf-8"))
        if arguments.source_json
        else gather_feed(arguments.request_cache_dir)
    )
    if arguments.previous_json and arguments.previous_json.is_file():
        try:
            previous = json.loads(arguments.previous_json.read_text(encoding="utf-8"))
            payload = merge_feed_history(payload, previous)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"Ignoring unusable previous mobile feed: {exc}", file=sys.stderr)
    destination = build_site(arguments.output.resolve(), payload)
    print(f"Built {len(sanitize_feed(payload)['articles'])} mobile articles in {destination}")


if __name__ == "__main__":
    main()
