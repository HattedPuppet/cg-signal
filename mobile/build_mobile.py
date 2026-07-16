from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = Path(__file__).resolve().parent / "site"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "dist"

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
    warning_sources = []
    for warning in payload.get("warnings", []):
        if isinstance(warning, str) and warning.strip():
            warning_sources.append(warning.split(":", 1)[0].strip()[:100])
    return {
        "schema_version": 1,
        "generated_at": payload.get("generated_at", ""),
        "unique_count": len(articles),
        "duplicates_collapsed": int(payload.get("duplicates_collapsed", 0) or 0),
        "articles": articles,
        "sources": sources,
        "unavailable_sources": list(dict.fromkeys(warning_sources)),
    }


def gather_feed() -> dict[str, Any]:
    sys.path.insert(0, str(PROJECT_ROOT))
    import server  # pylint: disable=import-outside-toplevel

    with tempfile.TemporaryDirectory(prefix="cg-signal-mobile-") as temporary:
        cache = Path(temporary)
        server.CACHE_DIR = cache
        server.CACHE_FILE = cache / "feed-cache.json"
        server.IMAGE_INDEX_FILE = cache / "image-index.json"
        server.USER_STATE_FILE = cache / "user-state.json"
        server.ARCHIVE_DB_FILE = cache / "cg-signal.db"
        server.PID_FILE = cache / "server.pid"
        server.ARCHIVE_INITIALIZED = False
        return server.build_feed(force=True)


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
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    payload = (
        json.loads(arguments.source_json.read_text(encoding="utf-8"))
        if arguments.source_json
        else gather_feed()
    )
    destination = build_site(arguments.output.resolve(), payload)
    print(f"Built {len(payload.get('articles', []))} mobile articles in {destination}")


if __name__ == "__main__":
    main()
