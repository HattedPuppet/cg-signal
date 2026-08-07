from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
import html
import re
from typing import Any

from .classification import (
    ASCII_STOPWORDS,
    EVENT_TERM_GROUPS,
    apply_article_classification,
    classify_lane,
    classify_topic,
    classify_software,
    classify_topics,
    score_relevance,
)

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
        output.append(apply_article_classification(public))
    return output
