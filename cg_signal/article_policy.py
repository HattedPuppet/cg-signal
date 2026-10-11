"""Eligibility for the news timeline, including previously cached articles."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import urllib.parse


# Publishers' clocks can be slightly ahead. Event dates months ahead are not
# publication dates and must not consume the newest-first feed's item budget.
PUBLICATION_CLOCK_SKEW = timedelta(minutes=10)


def article_is_publishable(article: Any, *, now: datetime | None = None) -> bool:
    if not isinstance(article, dict):
        return False
    try:
        published = datetime.fromisoformat(str(article.get("published_at", "")))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        if published > reference + PUBLICATION_CLOCK_SKEW:
            return False
    except (TypeError, ValueError, OverflowError):
        return False

    if article.get("source_id") == "game-developer":
        # This publisher mixes a calendar into its RSS. Legacy snapshots also
        # contain broken concatenations of its hostname and event website URLs.
        try:
            url = urllib.parse.urlsplit(str(article.get("url", "")))
            if url.hostname not in {"gamedeveloper.com", "www.gamedeveloper.com"}:
                return False
            path = urllib.parse.unquote(url.path).lower().rstrip("/")
            if path == "/events" or path.startswith("/events/"):
                return False
        except ValueError:
            return False
    return True
