from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import math
import urllib.parse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cg_signal.thumbnails import (  # noqa: E402
    canonical_thumbnail_reference,
    read_verified_thumbnail,
    validate_thumbnail_root,
)
SITE_DIR = Path(__file__).resolve().parent / "site"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "dist"
MOBILE_HISTORY_DAYS = 100
MAX_MOBILE_ARTICLES = 1500
MAX_MOBILE_SOURCES = 300
MAX_MOBILE_UNAVAILABLE_SOURCES = 300
# The feed already publishes at most 1,500 cards.  Keep all verified assets
# for those cards; the client still transfers them lazily as cards enter view.
MAX_MOBILE_THUMBNAILS = MAX_MOBILE_ARTICLES
MAX_MOBILE_THUMBNAIL_BYTES = 256_000_000

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

ARTICLE_LANES = {"Tech & Development", "Industry", "Business"}
MAX_STRING_LENGTH = 5000
MAX_TAG_LENGTH = 120
MAX_TAGS = 32
MAX_RELATED = 8
# Keep the shorter names local to the sanitizer while exposing the mobile
# limits above for parity tests and callers that need to size their inputs.
MAX_SOURCES = MAX_MOBILE_SOURCES
MAX_UNAVAILABLE_SOURCES = MAX_MOBILE_UNAVAILABLE_SOURCES


def bounded_string(value: Any, *, required: bool = False, maximum: int = MAX_STRING_LENGTH) -> str | None:
    if not isinstance(value, str) or len(value) > maximum:
        return None
    if required and not value.strip():
        return None
    return value


def public_http_url(value: Any, *, required: bool = False) -> str | None:
    if value in (None, ""):
        return "" if not required else None
    value = bounded_string(value, required=True, maximum=4096)
    if value is None:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return None
    except ValueError:
        return None
    return value


def thumbnail_reference(value: Any) -> str:
    return canonical_thumbnail_reference(value)


def string_array(value: Any, *, maximum: int = MAX_TAGS) -> list[str] | None:
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > maximum:
        return []
    result = []
    for item in value:
        clean = bounded_string(item, required=True, maximum=MAX_TAG_LENGTH)
        if clean is not None:
            result.append(clean)
    return result


def safe_count(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return int(max(0, value))


def allow_fields(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {field: value[field] for field in fields if field in value}


def sanitize_article(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    title_value = bounded_string(value.get("title"), required=True)
    if title_value is None:
        return {}
    summary_value = value.get("summary", title_value)
    lane_value = value.get("lane") or "Tech & Development"
    group_value = value.get("software_group")
    if group_value is None:
        group_value = {
            "Industry": "Industry context",
            "Business": "Business context",
        }.get(lane_value, "Production techniques")
    required = {
        "id": bounded_string(value.get("id"), required=True),
        "title": title_value,
        "summary": bounded_string(summary_value, required=True),
        "published_at": bounded_string(value.get("published_at"), required=True),
        "source": bounded_string(value.get("source"), required=True),
        "source_id": bounded_string(value.get("source_id"), required=True),
        "lane": bounded_string(lane_value, required=True),
        "software_group": bounded_string(group_value, required=True),
    }
    if any(item is None for item in required.values()) or required["lane"] not in ARTICLE_LANES:
        return {}
    url = public_http_url(value.get("url"), required=True)
    if url is None:
        return {}
    image = thumbnail_reference(value.get("image"))
    source_site = public_http_url(value.get("source_site"), required=False)
    source_site = source_site or ""
    arrays = {
        field: string_array(value.get(field), maximum=MAX_TAGS)
        for field in ("priority_reasons", "software_tags", "topic_tags")
    }
    related = []
    for item in value.get("related", []) if isinstance(value.get("related", []), list) else []:
        if not isinstance(item, dict):
            continue
        item_url = public_http_url(item.get("url"), required=True)
        title = bounded_string(item.get("title"), required=True)
        source = bounded_string(item.get("source"), required=True, maximum=300)
        published = bounded_string(item.get("published_at"), required=True, maximum=128)
        if item_url is None or title is None or source is None or published is None:
            continue
        related.append({
            "source": source,
            "title": title,
            "url": item_url,
            "published_at": published,
        })
    sources = []
    for item in value.get("sources", []) if isinstance(value.get("sources", []), list) else []:
        if not isinstance(item, dict):
            continue
        source_id = bounded_string(item.get("id"), required=True, maximum=200)
        name = bounded_string(item.get("name"), required=True, maximum=300)
        site = public_http_url(item.get("site"), required=False)
        accent = bounded_string(item.get("accent"), maximum=100)
        if source_id is None or name is None or site is None:
            continue
        sources.append({"id": source_id, "name": name, "site": site, "accent": accent or ""})
    numeric = {}
    for field, default in (("source_count", len(sources) or 1), ("cluster_size", 1), ("priority_score", 0)):
        raw = value.get(field, default)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            raw = default
        numeric[field] = raw
    return {
        "id": required["id"],
        "title": required["title"],
        "url": url,
        "summary": required["summary"],
        "image": image,
        "published_at": required["published_at"],
        "source": required["source"],
        "source_id": required["source_id"],
        "source_site": source_site,
        "accent": bounded_string(value.get("accent"), maximum=100) or "",
        "topic": bounded_string(value.get("topic"), maximum=300) or "",
        "lane": required["lane"],
        **numeric,
        "priority_reasons": arrays["priority_reasons"],
        "software_tags": arrays["software_tags"],
        "software_group": required["software_group"],
        "topic_tags": arrays["topic_tags"],
        "related": related[:MAX_RELATED],
        "sources": sources[:MAX_RELATED],
    }


def sanitize_feed(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Feed payload must be an object.")
    articles = []
    for article in payload.get("articles", []) if isinstance(payload.get("articles", []), list) else []:
        sanitized = sanitize_article(article)
        if sanitized:
            articles.append(sanitized)
    # Keep output deterministic and within the shared browser validator's
    # bounded article count.  Preserve source order so repeated builds agree.
    articles = articles[:MAX_MOBILE_ARTICLES]
    if not articles:
        raise ValueError("The mobile build received no valid articles; keeping the previous deployment is safer.")
    sources = []
    for source in payload.get("sources", []) if isinstance(payload.get("sources", []), list) else []:
        if not isinstance(source, dict):
            continue
        source_id = bounded_string(source.get("id"), required=True, maximum=200)
        name = bounded_string(source.get("name"), required=True, maximum=300)
        site = public_http_url(source.get("site"), required=False)
        accent = bounded_string(source.get("accent"), maximum=100)
        if source_id is None or name is None or site is None:
            continue
        sources.append({"id": source_id, "name": name, "site": site, "accent": accent or "", "ok": source.get("ok") is True, "count": safe_count(source.get("count", 0))})
    sources = sources[:MAX_SOURCES]
    warning_sources = [
        source.strip()[:100]
        for source in (payload.get("unavailable_sources", []) if isinstance(payload.get("unavailable_sources", []), list) else [])
        if isinstance(source, str) and source.strip()
    ]
    for warning in (payload.get("warnings", []) if isinstance(payload.get("warnings", []), list) else []):
        if isinstance(warning, str) and warning.strip():
            source_name = warning.split(":", 1)[0].strip()[:100]
            if source_name:
                warning_sources.append(source_name)
    try:
        feed_schema_version = int(
            payload.get("feed_schema_version", payload.get("schema_version", 2)) or 2
        )
    except (TypeError, ValueError):
        feed_schema_version = 2
    try:
        classification_revision = int(
            payload.get(
                "classification_revision",
                payload.get("classification_version", 1),
            )
            or 1
        )
    except (TypeError, ValueError):
        classification_revision = 1
    return {
        "schema_version": feed_schema_version,
        "feed_schema_version": feed_schema_version,
        "classification_revision": classification_revision,
        # Existing hosted consumers still read this alias during the rollout.
        "classification_version": classification_revision,
        "generated_at": bounded_string(payload.get("generated_at"), required=True, maximum=128)
        or datetime.now(timezone.utc).isoformat(),
        "unique_count": len(articles),
        "duplicates_collapsed": safe_count(payload.get("duplicates_collapsed", 0)),
        "carried_forward_count": safe_count(payload.get("carried_forward_count", 0)),
        "articles": articles,
        "sources": sources,
        "unavailable_sources": list(dict.fromkeys(warning_sources))[:MAX_UNAVAILABLE_SOURCES],
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
    if previous["feed_schema_version"] != current["feed_schema_version"]:
        return current
    if previous["classification_revision"] != current["classification_revision"]:
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
        for source in current["sources"]
        if source.get("id")
    }
    sources_by_id.update(
        {
            source["id"]: source
            for source in previous["sources"]
            if source.get("id") and source["id"] not in sources_by_id
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
    ][:MAX_SOURCES]

    return {
        **current,
        "unique_count": len(merged_articles),
        "carried_forward_count": len(carried_ids.intersection(retained_ids)),
        "articles": merged_articles,
        "sources": sources,
    }


def gather_feed(request_cache_dir: Path | None = None) -> dict[str, Any]:
    sys.path.insert(0, str(PROJECT_ROOT))
    from cg_signal.config import RuntimePaths  # pylint: disable=import-outside-toplevel
    from cg_signal.feeds import FeedService  # pylint: disable=import-outside-toplevel

    with tempfile.TemporaryDirectory(prefix="cg-signal-mobile-") as temporary:
        cache = Path(temporary).resolve()
        request_cache = request_cache_dir.absolute() if request_cache_dir else cache
        request_thumbnail_root = request_cache / "thumbnails"
        validate_thumbnail_root(request_thumbnail_root, request_cache)
        request_cache.mkdir(parents=True, exist_ok=True)
        validate_thumbnail_root(request_thumbnail_root, request_cache)
        paths = RuntimePaths.for_root(PROJECT_ROOT).with_cache_dir(cache)
        paths = replace(
            paths,
            feed_source_cache_file=request_cache / "feed-source-cache.json",
            image_index_file=request_cache / "image-index.json",
            thumbnail_dir=request_cache / "thumbnails",
            thumbnail_anchor=request_cache,
        )
        service = FeedService(paths)
        payload = service.build_feed(force=True)
        if payload.get("thumbnails_refreshing"):
            service.wait_for_thumbnail_refresh()
            return service.read_cache() or payload
        return payload


def bundle_thumbnails(
    payload: dict[str, Any],
    output: Path,
    thumbnail_root: Path | None,
    *,
    thumbnail_anchor: Path | None = None,
) -> dict[str, Any]:
    """Copy only verified, referenced thumbnail assets into a fresh bundle."""

    trusted_thumbnail_root = None
    if thumbnail_root is not None:
        trusted_thumbnail_root = validate_thumbnail_root(thumbnail_root, thumbnail_anchor)
    sanitized = sanitize_feed(payload)
    rewritten_articles = []
    copied: set[str] = set()
    total_bytes = 0
    destination = output / "thumbnails"
    if destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    destination.mkdir(parents=True, exist_ok=True)
    for article in sanitized["articles"]:
        rewritten = dict(article)
        reference = canonical_thumbnail_reference(rewritten.get("image", ""))
        verified = (
            read_verified_thumbnail(
                trusted_thumbnail_root,
                reference,
                expected_anchor=thumbnail_anchor,
            )
            if trusted_thumbnail_root is not None and reference
            else None
        )
        if verified is None:
            rewritten["image"] = ""
        elif reference not in copied:
            if (
                len(copied) >= MAX_MOBILE_THUMBNAILS
                or total_bytes + len(verified.body) > MAX_MOBILE_THUMBNAIL_BYTES
            ):
                rewritten["image"] = ""
            else:
                target = destination / Path(reference).name
                target.write_bytes(verified.body)
                copied.add(reference)
                total_bytes += len(verified.body)
        if rewritten.get("image") and reference not in copied:
            rewritten["image"] = ""
        rewritten_articles.append(rewritten)
    sanitized["articles"] = rewritten_articles
    return sanitized


def build_site(
    output: Path,
    payload: dict[str, Any],
    *,
    thumbnail_root: Path | None = None,
    thumbnail_anchor: Path | None = None,
) -> Path:
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(SITE_DIR, output)
    shutil.copy2(PROJECT_ROOT / "static" / "domain.mjs", output / "domain.mjs")
    for icon_name in ("favicon.ico", "icon-180.png", "icon-192.png", "icon-512.png"):
        shutil.copy2(PROJECT_ROOT / "static" / icon_name, output / icon_name)
    (output / ".nojekyll").write_text("", encoding="utf-8")
    final_payload = bundle_thumbnails(
        payload,
        output,
        thumbnail_root,
        thumbnail_anchor=thumbnail_anchor,
    )
    (output / "feed.json").write_text(
        json.dumps(final_payload, ensure_ascii=False, separators=(",", ":")),
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
    thumbnail_root = (
        arguments.request_cache_dir.absolute() / "thumbnails"
        if arguments.request_cache_dir
        else None
    )
    thumbnail_anchor = arguments.request_cache_dir.absolute() if arguments.request_cache_dir else None
    destination = build_site(
        arguments.output.resolve(),
        payload,
        thumbnail_root=thumbnail_root,
        thumbnail_anchor=thumbnail_anchor,
    )
    print(f"Built {len(sanitize_feed(payload)['articles'])} mobile articles in {destination}")


if __name__ == "__main__":
    main()
