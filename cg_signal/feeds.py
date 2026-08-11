"""Feed parsing, fetching, caching, classification, and refresh orchestration."""

from __future__ import annotations

import concurrent.futures
import email.utils
import hashlib
import html
import json
import mimetypes
import re
import sqlite3
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .classification import (
    apply_article_classification,
    classify_lane,
    classify_topic,
)
from .config import (
    CACHE_TTL_SECONDS,
    CLASSIFICATION_REVISION,
    FEED_REFRESH_RETRY_SECONDS,
    FEED_SCHEMA_VERSION,
    IMAGE_INDEX_TTL_SECONDS,
    MAX_FEED_ENTRIES,
    MAX_ITEMS_PER_SOURCE,
    RuntimePaths,
    SOURCE_CACHE_SCHEMA_VERSION,
)
from .dedupe import cluster_articles
from .safe_http import SafeHttpClient, SafeHttpError
from .storage import SQLiteRepository, normalized_http_url
from .thumbnails import (
    MAX_THUMBNAIL_BYTES,
    THUMBNAIL_NEGATIVE_TTL_SECONDS,
    THUMBNAIL_POSITIVE_TTL_SECONDS,
    canonical_thumbnail_reference,
    load_thumbnail_index,
    parse_thumbnail_reference,
    prune_thumbnail_store,
    read_verified_thumbnail,
    save_thumbnail_index,
    store_thumbnail,
    validate_thumbnail_response,
    validate_thumbnail_root,
)


TRACKING_PARAMETERS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer", "source",
}


def render_safe_article(value: Any) -> dict[str, Any]:
    """Copy an article while omitting untrusted or empty image references."""

    if not isinstance(value, dict):
        return {}
    article = dict(value)
    for key in list(article):
        if isinstance(key, str) and key.startswith("_"):
            article.pop(key, None)
    if "image" in article:
        raw_image = article.get("image", "")
        image = canonical_thumbnail_reference(raw_image)
        if image:
            article["image"] = image
        elif raw_image == "":
            # Preserve an explicit empty optional field for cache shape
            # compatibility; it cannot produce a browser request.
            article["image"] = ""
        else:
            article.pop("image", None)
    return article


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


DATE_FIELD_NAMES = ("pubdate", "published", "updated", "date", "created")


def _new_feed_diagnostics() -> dict[str, int]:
    return {
        "total": 0,
        "accepted": 0,
        "missing_date": 0,
        "invalid_date": 0,
        "missing_title_or_link": 0,
    }


def _item_date(item: ET.Element) -> tuple[datetime | None, str]:
    """Return the first valid date candidate and its diagnostic status."""

    saw_value = False
    for field_name in DATE_FIELD_NAMES:
        for child in item:
            if local_name(child.tag) != field_name:
                continue
            value = "".join(child.itertext()).strip()
            if not value:
                continue
            saw_value = True
            parsed = parse_date(value)
            if parsed is not None:
                return parsed, "valid"
    return None, "invalid" if saw_value else "missing"


def parse_date(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
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
        return None


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
    return canonical_url(child_text(item, {"guid", "id"}))


def first_image(item: ET.Element, raw_summary: str) -> str:
    for descendant in item.iter():
        name = local_name(descendant.tag)
        if name not in {"thumbnail", "content", "enclosure"}:
            continue
        candidate = descendant.attrib.get("url", "") or descendant.attrib.get("href", "")
        media_type = descendant.attrib.get("type", "")
        if candidate and (
            name == "thumbnail"
            or media_type.startswith("image/")
            or re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", candidate, re.I)
        ):
            return canonical_url(html.unescape(candidate))
    match = re.search(r"<img[^>]+src=[\"']([^\"']+)", raw_summary, re.I)
    return canonical_url(html.unescape(match.group(1))) if match else ""


def extract_page_image(markup: str, base_url: str) -> str:
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


def parse_feed_document(xml_bytes: bytes) -> ET.Element:
    leading_whitespace = re.match(
        br"^[\x09\x0a\x0c\x0d\x20]+(?=<\?xml(?:[\x09\x0a\x0c\x0d\x20]|>))",
        xml_bytes,
    )
    if leading_whitespace:
        xml_bytes = xml_bytes[leading_whitespace.end():]
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


class FeedService:
    """Build feeds for one explicit runtime and repository."""

    def __init__(self, paths: RuntimePaths, *, http_client: SafeHttpClient | None = None):
        self.paths = paths
        self.repository = SQLiteRepository(paths)
        self.http = http_client or SafeHttpClient()
        self._refresh_lock = threading.Lock()
        self._thumbnail_condition = threading.Condition()
        self._thumbnail_worker_active = False
        self._thumbnail_pending: tuple[list[dict[str, Any]], str] | None = None

    def read_cache(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.paths.cache_file.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def write_cache(self, payload: dict[str, Any]) -> None:
        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.cache_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.paths.cache_file)

    def invalidate_feed_cache(self) -> None:
        try:
            self.paths.cache_file.unlink()
        except FileNotFoundError:
            pass

    def read_feed_source_cache(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.paths.feed_source_cache_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != SOURCE_CACHE_SCHEMA_VERSION:
                return {}
            sources = payload.get("sources", {})
            return sources if isinstance(sources, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def write_feed_source_cache(self, sources: dict[str, dict[str, Any]]) -> None:
        self.paths.feed_source_cache_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.feed_source_cache_file.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"schema_version": SOURCE_CACHE_SCHEMA_VERSION, "sources": sources}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self.paths.feed_source_cache_file)

    @staticmethod
    def _cached_source_entry(source: dict[str, Any], cached: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(cached, dict) or cached.get("feed") != source["feed"]:
            return {}
        articles = cached.get("articles", [])
        if not isinstance(articles, list) or not all(isinstance(article, dict) for article in articles):
            return {}
        return cached

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(0.001, deadline - time.monotonic())

    def _fetch_thumbnail_asset(self, image_url: str, deadline: float) -> str:
        """Fetch, validate, and store one publisher image as an app asset."""

        if not image_url or time.monotonic() >= deadline:
            return ""
        try:
            response = self.http.get(
                image_url,
                headers={
                    "User-Agent": "CGSignal/1.0 (local personal RSS reader)",
                    "Accept": "image/jpeg,image/png,image/webp;q=0.9",
                },
                timeout=self._remaining(deadline),
                max_bytes=MAX_THUMBNAIL_BYTES,
            )
            validated = validate_thumbnail_response(response)
            if validated is None:
                return ""
            return store_thumbnail(
                self.paths.thumbnail_dir,
                validated,
                expected_anchor=self.paths.thumbnail_anchor,
            )
        except (SafeHttpError, TimeoutError, OSError, ValueError):
            return ""

    def fetch_page_image(self, article_url: str, deadline: float | None = None) -> str:
        """Discover an OG/Twitter candidate from a safe article page."""

        operation_deadline = deadline or (time.monotonic() + 18)
        if time.monotonic() >= operation_deadline:
            return ""
        try:
            response = self.http.get(
                article_url,
                headers={
                    "User-Agent": "CGSignal/1.0 (local personal RSS reader)",
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                },
                timeout=self._remaining(operation_deadline),
                max_bytes=2_500_000,
            )
            if response.status != 200:
                return ""
            try:
                content_type = response.headers.get_content_type()
                charset = response.headers.get_content_charset() or "utf-8"
            except AttributeError:
                raw_content_type = response.headers.get("Content-Type", "")
                content_type = str(raw_content_type).split(";", 1)[0].strip().lower()
                charset = "utf-8"
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return ""
            markup = response.body.decode(charset, errors="replace")
            return extract_page_image(markup, response.final_url)
        except (SafeHttpError, TimeoutError, OSError, UnicodeError):
            return ""

    def read_image_index(self) -> dict[str, Any]:
        try:
            validate_thumbnail_root(self.paths.thumbnail_dir, self.paths.thumbnail_anchor)
        except ValueError:
            return {"schema_version": 1, "entries": {}}
        index = load_thumbnail_index(self.paths.image_index_file)
        try:
            cleaned = prune_thumbnail_store(
                self.paths.thumbnail_dir,
                index,
                expected_anchor=self.paths.thumbnail_anchor,
            )
        except ValueError:
            return {"schema_version": 1, "entries": {}}
        if cleaned != index:
            try:
                save_thumbnail_index(self.paths.image_index_file, cleaned)
            except OSError:
                pass
        return cleaned

    def write_image_index(self, index: dict[str, Any]) -> None:
        validate_thumbnail_root(self.paths.thumbnail_dir, self.paths.thumbnail_anchor)
        save_thumbnail_index(self.paths.image_index_file, index)

    def apply_cached_images(self, articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for article in articles:
            if "image" in article:
                image = canonical_thumbnail_reference(article.get("image", ""))
                if image and read_verified_thumbnail(
                    self.paths.thumbnail_dir, image, expected_anchor=self.paths.thumbnail_anchor
                ):
                    article["image"] = image
                else:
                    article["image"] = ""
        missing = [article for article in articles if not article.get("image")]
        if not missing:
            return []
        now = time.time()
        index = self.read_image_index()
        entries = index.get("entries", {})
        unresolved: list[dict[str, Any]] = []
        for article in missing:
            cached = entries.get(article.get("url", ""), {}) if isinstance(entries, dict) else {}
            try:
                age = now - float(cached.get("checked_at", 0))
            except (TypeError, ValueError):
                age = float("inf")
            if cached.get("status") == "ok" and age < THUMBNAIL_POSITIVE_TTL_SECONDS:
                image = canonical_thumbnail_reference(cached.get("image", ""))
                if image and read_verified_thumbnail(
                    self.paths.thumbnail_dir, image, expected_anchor=self.paths.thumbnail_anchor
                ):
                    article["image"] = image
            elif cached.get("status") == "miss" and age < THUMBNAIL_NEGATIVE_TTL_SECONDS:
                continue
            else:
                unresolved.append(article)
        return unresolved

    def enrich_missing_images(self, articles: list[dict[str, Any]]) -> None:
        missing = self.apply_cached_images(articles)
        if not missing:
            return
        batch_deadline = time.monotonic() + 45
        index = self.read_image_index()
        by_url: dict[str, list[dict[str, Any]]] = {}
        for article in missing:
            if article.get("url"):
                by_url.setdefault(article["url"], []).append(article)

        def enrich_one(article: dict[str, Any]) -> str:
            operation_deadline = min(batch_deadline, time.monotonic() + 18)
            if time.monotonic() >= operation_deadline:
                return ""
            candidate = article.get("_thumbnail_candidate", "")
            reference = self._fetch_thumbnail_asset(candidate, operation_deadline)
            if reference:
                return reference
            page_candidate = self.fetch_page_image(article.get("url", ""), operation_deadline)
            return self._fetch_thumbnail_asset(page_candidate, operation_deadline)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        future_to_url = {
            executor.submit(enrich_one, article_list[0]): url
            for url, article_list in by_url.items()
            if time.monotonic() < batch_deadline
        }
        done, pending = concurrent.futures.wait(
            future_to_url,
            timeout=max(0.0, batch_deadline - time.monotonic()),
        )
        for future in pending:
            future.cancel()
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:  # pragma: no cover - older Python fallback
            executor.shutdown(wait=False)

        now = time.time()
        entries = index.setdefault("entries", {})
        for future in done:
            url = future_to_url[future]
            try:
                reference = canonical_thumbnail_reference(future.result())
            except Exception:
                reference = ""
            if reference:
                for article in by_url[url]:
                    article["image"] = reference
                entries[url] = {"status": "ok", "image": reference, "checked_at": now}
            else:
                entries[url] = {"status": "miss", "checked_at": now}
        try:
            cleaned = prune_thumbnail_store(
                self.paths.thumbnail_dir,
                index,
                expected_anchor=self.paths.thumbnail_anchor,
            )
        except ValueError:
            cleaned = {"schema_version": 1, "entries": {}}
        try:
            self.write_image_index(cleaned)
        except OSError:
            pass

    def fetch_source(self, source: dict[str, Any], cached: dict[str, Any] | None = None) -> dict[str, Any]:
        cached = self._cached_source_entry(source, cached)
        cached_articles = [
            {
                **article,
                "source": source["name"],
                "source_id": source["id"],
                "source_site": source["site"],
                "accent": source["accent"],
            }
            for article in cached.get("articles", [])
        ]
        diagnostics = _new_feed_diagnostics()
        headers = {
            "User-Agent": "CGSignal/1.0 (local personal RSS reader)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
        }
        if cached_articles and cached.get("etag"):
            headers["If-None-Match"] = str(cached["etag"])
        if cached_articles and cached.get("last_modified"):
            headers["If-Modified-Since"] = str(cached["last_modified"])
        started = time.monotonic()
        try:
            response = self.http.get(
                source["feed"],
                headers=headers,
                timeout=22,
                max_bytes=6_000_000,
            )
            if response.status == 304 and cached_articles:
                return {
                    "source": source, "articles": cached_articles, "ok": True, "message": "",
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "etag": response.headers.get("ETag", cached.get("etag", "")),
                    "last_modified": response.headers.get("Last-Modified", cached.get("last_modified", "")),
                    "not_modified": True, "used_stale_cache": False,
                    "diagnostics": diagnostics,
                }
            if not 200 <= response.status < 300:
                raise SafeHttpError(f"HTTP {response.status}")
            xml_bytes = response.body
            etag = response.headers.get("ETag", "")
            last_modified = response.headers.get("Last-Modified", "")
            root = parse_feed_document(xml_bytes)
            entries: list[ET.Element] = []
            for node in root.iter():
                if local_name(node.tag) not in {"item", "entry"}:
                    continue
                diagnostics["total"] += 1
                if diagnostics["total"] > MAX_FEED_ENTRIES:
                    raise ValueError(f"Feed contains more than {MAX_FEED_ENTRIES} entries")
                entries.append(node)
            articles: list[dict[str, Any]] = []
            item_limit = int(source.get("limit", MAX_ITEMS_PER_SOURCE))
            for item in entries:
                published, date_status = _item_date(item)
                if date_status == "missing":
                    diagnostics["missing_date"] += 1
                elif date_status == "invalid":
                    diagnostics["invalid_date"] += 1
                title = strip_markup(child_text(item, {"title"}))
                url = article_link(item)
                if not title or not url:
                    diagnostics["missing_title_or_link"] += 1
                if published is None or not title or not url:
                    continue
                raw_summary = preferred_child_text(item, ("encoded", "content", "description", "summary"))
                summary = strip_markup(raw_summary)
                thumbnail_candidate = first_image(item, raw_summary)
                article_id = hashlib.sha1(
                    f"{source['id']}|{url}".encode("utf-8")
                ).hexdigest()[:18]
                articles.append(
                    {
                        "id": article_id,
                        "title": title,
                        "url": url,
                        "summary": summary[:900],
                        "image": "",
                        "_thumbnail_candidate": thumbnail_candidate,
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
                diagnostics["accepted"] += 1
            articles.sort(key=lambda article: article["timestamp"], reverse=True)
            articles = articles[:item_limit]
            if entries and not diagnostics["accepted"]:
                raise ValueError("Feed contained no usable dated articles")
            return {
                "source": source, "articles": articles, "ok": True, "message": "",
                "duration_ms": round((time.monotonic() - started) * 1000),
                "etag": etag, "last_modified": last_modified,
                "not_modified": False, "used_stale_cache": False,
                "diagnostics": diagnostics,
            }
        except (SafeHttpError, TimeoutError, ET.ParseError, OSError, ValueError) as exc:
            error = exc
        return {
            "source": source, "articles": cached_articles, "ok": False, "message": str(error),
            "duration_ms": round((time.monotonic() - started) * 1000),
            "etag": cached.get("etag", ""), "last_modified": cached.get("last_modified", ""),
            "not_modified": False, "used_stale_cache": bool(cached_articles),
            "diagnostics": diagnostics,
        }

    def test_source(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Source test details must be an object.")
        source_id = payload.get("id")
        if isinstance(source_id, str) and source_id:
            source = self.repository.source_config(source_id)
            if not source:
                raise ValueError("Source not found.")
        else:
            feed = normalized_http_url(payload.get("feed"), "Feed URL")
            site = normalized_http_url(payload.get("site"), "Website URL", required=False)
            parsed = urllib.parse.urlsplit(feed)
            raw_name = payload.get("name", "")
            name = raw_name.strip() if isinstance(raw_name, str) else ""
            source = {
                "id": "source-test", "name": name or parsed.hostname or "Feed test",
                "site": site, "feed": feed, "accent": "#7fa9ff", "limit": 3,
            }
        result = self.fetch_source(source)
        return {
            "ok": result["ok"], "message": result["message"],
            "count": len(result["articles"]), "duration_ms": result["duration_ms"],
            "sample_titles": [article["title"] for article in result["articles"][:3]],
            "source": source,
        }

    def update_feed_source_cache(self, existing: dict[str, dict[str, Any]], results: list[dict[str, Any]]) -> None:
        checked_at = datetime.now(timezone.utc).isoformat()
        updated: dict[str, dict[str, Any]] = {}
        for result in results:
            source = result["source"]
            source_id = source["id"]
            previous = existing.get(source_id, {})
            if result["ok"] and not result["not_modified"]:
                updated[source_id] = {
                    "feed": source["feed"], "etag": result.get("etag", ""),
                    "last_modified": result.get("last_modified", ""),
                    "articles": result["articles"], "checked_at": checked_at, "last_error": "",
                }
            elif previous.get("feed") == source["feed"]:
                updated[source_id] = {
                    **previous,
                    "etag": result.get("etag") or previous.get("etag", ""),
                    "last_modified": result.get("last_modified") or previous.get("last_modified", ""),
                    "checked_at": checked_at,
                    "last_error": "" if result["ok"] else result["message"][:500],
                }
        try:
            self.write_feed_source_cache(updated)
        except OSError:
            pass

    @staticmethod
    def _versions(payload: dict[str, Any]) -> tuple[int, int]:
        schema = payload.get("feed_schema_version", payload.get("schema_version", -1))
        revision = payload.get("classification_revision", payload.get("classification_version", -1))
        try:
            schema = int(schema)
        except (TypeError, ValueError):
            schema = -1
        try:
            revision = int(revision)
        except (TypeError, ValueError):
            revision = -1
        return schema, revision

    def _render_cached_article(self, value: Any) -> dict[str, Any]:
        article = render_safe_article(value)
        image = article.get("image", "")
        if image and not read_verified_thumbnail(
            self.paths.thumbnail_dir, image, expected_anchor=self.paths.thumbnail_anchor
        ):
            article["image"] = ""
        return article

    def cache_timestamp(self, payload: dict[str, Any], field: str) -> datetime | None:
        try:
            value = datetime.fromisoformat(str(payload[field]))
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            return None

    def cached_feed_is_fresh(self, cached: dict[str, Any]) -> bool:
        generated = self.cache_timestamp(cached, "generated_at")
        schema, revision = self._versions(cached)
        return bool(
            generated and not cached.get("stale")
            and schema == FEED_SCHEMA_VERSION and revision == CLASSIFICATION_REVISION
            and (datetime.now(timezone.utc) - generated).total_seconds() < CACHE_TTL_SECONDS
        )

    def feed_refresh_is_due(self, cached: dict[str, Any]) -> bool:
        attempted = self.cache_timestamp(cached, "last_refresh_attempt_at")
        return not attempted or (datetime.now(timezone.utc) - attempted).total_seconds() >= FEED_REFRESH_RETRY_SECONDS

    def cached_feed_payload(self, cached: dict[str, Any], *, refreshing: bool = False) -> dict[str, Any]:
        payload = dict(cached)
        schema, revision = self._versions(cached)
        if schema != FEED_SCHEMA_VERSION:
            payload["feed_schema_version"] = FEED_SCHEMA_VERSION
            payload["classification_revision"] = CLASSIFICATION_REVISION
            payload["classification_version"] = CLASSIFICATION_REVISION
            payload["articles"] = []
        elif revision != CLASSIFICATION_REVISION:
            payload["classification_revision"] = CLASSIFICATION_REVISION
            payload["classification_version"] = CLASSIFICATION_REVISION
            payload["articles"] = [
                self._render_cached_article(apply_article_classification(dict(article)))
                for article in cached.get("articles", []) if isinstance(article, dict)
            ]
        else:
            payload["feed_schema_version"] = FEED_SCHEMA_VERSION
            payload["classification_revision"] = CLASSIFICATION_REVISION
            payload["classification_version"] = CLASSIFICATION_REVISION
            payload["articles"] = [
                self._render_cached_article(article)
                for article in cached.get("articles", []) if isinstance(article, dict)
            ]
        payload["cached"] = True
        payload["refreshing"] = refreshing
        if payload.get("thumbnails_refreshing"):
            with self._thumbnail_condition:
                active = self._thumbnail_worker_active
            payload["thumbnails_refreshing"] = active or self._refresh_lock.locked()
        if "archive_count" not in payload:
            try:
                payload["archive_count"] = self.repository.archive_article_count()
            except (OSError, sqlite3.Error):
                payload["archive_count"] = 0
        return payload

    def update_cached_thumbnail_images(self, articles: list[dict[str, Any]], generated_at: str) -> None:
        images_by_url = {
            article["url"]: canonical_thumbnail_reference(article.get("image", ""))
            for article in articles
            if article.get("url")
            and canonical_thumbnail_reference(article.get("image", ""))
            and read_verified_thumbnail(
                self.paths.thumbnail_dir,
                canonical_thumbnail_reference(article.get("image", "")),
                expected_anchor=self.paths.thumbnail_anchor,
            )
        }
        with self._refresh_lock:
            cached = self.read_cache()
            if not cached or cached.get("generated_at") != generated_at:
                return
            changed = False
            updated_articles = []
            for article in cached.get("articles", []):
                updated = dict(article) if isinstance(article, dict) else {}
                updated.pop("_thumbnail_candidate", None)
                raw_image = updated.get("image", "")
                normalized_image = canonical_thumbnail_reference(raw_image)
                if normalized_image and not read_verified_thumbnail(
                    self.paths.thumbnail_dir,
                    normalized_image,
                    expected_anchor=self.paths.thumbnail_anchor,
                ):
                    normalized_image = ""
                if normalized_image:
                    updated["image"] = normalized_image
                elif raw_image not in (None, ""):
                    updated.pop("image", None)
                if isinstance(article, dict) and "image" in article and "image" not in updated:
                    changed = True
                image = images_by_url.get(updated.get("url", ""))
                if image and not updated.get("image"):
                    updated["image"] = image
                    changed = True
                updated_articles.append(updated)
            if not changed and not cached.get("thumbnails_refreshing"):
                return
            try:
                self.write_cache({
                    **cached, "articles": updated_articles, "thumbnails_refreshing": False,
                    "thumbnails_updated_at": datetime.now(timezone.utc).isoformat(),
                })
            except OSError:
                pass

    def _thumbnail_worker(self) -> None:
        while True:
            with self._thumbnail_condition:
                task = self._thumbnail_pending
                self._thumbnail_pending = None
                if task is None:
                    self._thumbnail_worker_active = False
                    self._thumbnail_condition.notify_all()
                    return
            articles, generated_at = task
            try:
                self.enrich_missing_images(articles)
            except Exception as exc:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Thumbnail refresh failed: {exc}")
            try:
                self.update_cached_thumbnail_images(articles, generated_at)
            except Exception as exc:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Thumbnail cache update failed: {exc}")

    def schedule_thumbnail_enrichment(self, articles: list[dict[str, Any]], generated_at: str) -> bool:
        with self._thumbnail_condition:
            self._thumbnail_pending = (articles, generated_at)
            if self._thumbnail_worker_active:
                return True
            self._thumbnail_worker_active = True
            threading.Thread(target=self._thumbnail_worker, name="cg-signal-thumbnail-refresh", daemon=True).start()
            return True

    def wait_for_thumbnail_refresh(self, timeout_seconds: float = 50) -> bool:
        deadline = time.monotonic() + timeout_seconds
        with self._thumbnail_condition:
            while self._thumbnail_worker_active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._thumbnail_condition.wait(remaining)
            return True

    def refresh_feed(self, cached: dict[str, Any] | None = None) -> dict[str, Any]:
        configured_sources = self.repository.list_source_configs(enabled_only=True)
        source_cache = self.read_feed_source_cache()
        if configured_sources:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(configured_sources))) as executor:
                results = list(executor.map(
                    lambda source: self.fetch_source(source, source_cache.get(source["id"])),
                    configured_sources,
                ))
        else:
            results = []
        self.update_feed_source_cache(source_cache, results)
        all_articles = [article for result in results for article in result["articles"]]
        failed = [result for result in results if not result["ok"]]
        if configured_sources and not all_articles and cached:
            cached_schema, _ = self._versions(cached)
            schema_compatible = cached_schema == FEED_SCHEMA_VERSION
            fallback = dict(cached) if schema_compatible else {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "raw_count": 0,
                "unique_count": 0,
                "duplicates_collapsed": 0,
                "sources": [
                    {
                        **result["source"],
                        "ok": result["ok"],
                        "count": 0,
                        "duration_ms": result["duration_ms"],
                        "not_modified": result["not_modified"],
                        "used_stale_cache": result["used_stale_cache"],
                    }
                    for result in results
                ],
            }
            fallback.update({
                "feed_schema_version": FEED_SCHEMA_VERSION,
                "classification_revision": CLASSIFICATION_REVISION,
                "classification_version": CLASSIFICATION_REVISION,
                "articles": [
                    self._render_cached_article(apply_article_classification(dict(article)))
                    for article in cached.get("articles", [])
                    if schema_compatible and isinstance(article, dict)
                ],
                "cached": True, "stale": True, "refreshing": False,
                "last_refresh_attempt_at": datetime.now(timezone.utc).isoformat(),
                "warnings": [f"{item['source']['name']}: {item['message']}" for item in failed],
            })
            try:
                fallback["archive_count"] = self.repository.archive_article_count()
                self.write_cache(fallback)
            except (OSError, sqlite3.Error):
                pass
            return fallback
        missing_thumbnail_articles = self.apply_cached_images(all_articles)
        clusters = cluster_articles(all_articles)
        payload: dict[str, Any] = {
            "feed_schema_version": FEED_SCHEMA_VERSION,
            "classification_revision": CLASSIFICATION_REVISION,
            "classification_version": CLASSIFICATION_REVISION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cached": False, "stale": False,
            "raw_count": len(all_articles), "unique_count": len(clusters),
            "duplicates_collapsed": max(0, len(all_articles) - len(clusters)),
            "articles": clusters,
            "sources": [
                {
                    **result["source"], "ok": result["ok"], "count": len(result["articles"]),
                    "duration_ms": result["duration_ms"], "not_modified": result["not_modified"],
                    "used_stale_cache": result["used_stale_cache"],
                }
                for result in results
            ],
            "warnings": [f"{item['source']['name']}: {item['message']}" for item in failed],
        }
        try:
            payload["archive_count"] = self.repository.archive_articles(clusters)
        except (OSError, sqlite3.Error):
            payload["archive_count"] = self.repository.archive_article_count()
        payload["thumbnails_refreshing"] = bool(missing_thumbnail_articles)
        if configured_sources:
            try:
                self.write_cache(payload)
            except OSError:
                pass
        if payload["thumbnails_refreshing"]:
            self.schedule_thumbnail_enrichment(all_articles, payload["generated_at"])
        return payload

    def build_feed(self, force: bool = False) -> dict[str, Any]:
        cached = self.read_cache()
        if cached and not force and self.cached_feed_is_fresh(cached):
            return self.cached_feed_payload(cached)
        with self._refresh_lock:
            if not force:
                cached = self.read_cache()
                if cached and self.cached_feed_is_fresh(cached):
                    return self.cached_feed_payload(cached)
            return self.refresh_feed(cached)

    def refresh_feed_in_background(self) -> bool:
        if not self._refresh_lock.acquire(blocking=False):
            return False

        def work() -> None:
            try:
                self.refresh_feed(self.read_cache())
            except Exception as exc:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Background feed refresh failed: {exc}")
            finally:
                self._refresh_lock.release()

        threading.Thread(target=work, name="cg-signal-feed-refresh", daemon=True).start()
        return True

    def feed_for_request(self, force: bool = False, wait_for_refresh: bool = False, wait_for_thumbnails: bool = False) -> dict[str, Any]:
        if force:
            return self.build_feed(force=True)
        if wait_for_thumbnails:
            self.wait_for_thumbnail_refresh()
            cached = self.read_cache()
            return self.cached_feed_payload(cached) if cached else self.build_feed()
        if wait_for_refresh:
            with self._refresh_lock:
                cached = self.read_cache()
            return self.cached_feed_payload(cached) if cached else self.build_feed()
        cached = self.read_cache()
        if not cached:
            return self.build_feed()
        cached_schema, _ = self._versions(cached)
        if cached_schema != FEED_SCHEMA_VERSION:
            return self.build_feed()
        if self.cached_feed_is_fresh(cached):
            return self.cached_feed_payload(cached)
        if not self.feed_refresh_is_due(cached):
            return self.cached_feed_payload(cached)
        started = self.refresh_feed_in_background()
        return self.cached_feed_payload(cached, refreshing=started or self._refresh_lock.locked())
