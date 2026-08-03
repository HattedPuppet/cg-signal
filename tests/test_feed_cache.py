import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import server


class FeedCacheTests(unittest.TestCase):
    def cached_feed(self, *, age_seconds: int, retry_age_seconds: int | None = None):
        now = datetime.now(timezone.utc)
        payload = {
            "classification_version": server.ARTICLE_CLASSIFICATION_VERSION,
            "generated_at": (now - timedelta(seconds=age_seconds)).isoformat(),
            "articles": [{"id": "cached-story"}],
            "sources": [],
            "archive_count": 1,
        }
        if retry_age_seconds is not None:
            payload["last_refresh_attempt_at"] = (
                now - timedelta(seconds=retry_age_seconds)
            ).isoformat()
        return payload

    def test_fresh_cache_is_returned_without_starting_a_refresh(self):
        cached = self.cached_feed(age_seconds=30)
        with (
            mock.patch.object(server, "read_cache", return_value=cached),
            mock.patch.object(server, "refresh_feed_in_background") as refresh,
        ):
            result = server.feed_for_request()

        self.assertTrue(result["cached"])
        self.assertFalse(result["refreshing"])
        refresh.assert_not_called()

    def test_previous_classifier_cache_is_not_fresh(self):
        cached = self.cached_feed(age_seconds=30)
        cached["classification_version"] = server.ARTICLE_CLASSIFICATION_VERSION - 1

        self.assertFalse(server.cached_feed_is_fresh(cached))

    def test_previous_classifier_cache_is_relabelled_while_refreshing(self):
        cached = self.cached_feed(age_seconds=30)
        cached["classification_version"] = server.ARTICLE_CLASSIFICATION_VERSION - 1
        cached["articles"] = [
            {
                "id": "stale-gacha",
                "title": "期間限定ガチャイベントを開催",
                "summary": "新キャラを追加",
                "source_id": "automaton",
                "lane": "Tech & Development",
            }
        ]

        result = server.cached_feed_payload(cached, refreshing=True)

        self.assertEqual(result["classification_version"], server.ARTICLE_CLASSIFICATION_VERSION)
        self.assertEqual(result["articles"][0]["lane"], "Industry")
        self.assertTrue(result["refreshing"])

    def test_expired_cache_is_returned_while_refresh_starts(self):
        cached = self.cached_feed(age_seconds=server.CACHE_TTL_SECONDS + 1)
        with (
            mock.patch.object(server, "read_cache", return_value=cached),
            mock.patch.object(server, "refresh_feed_in_background", return_value=True) as refresh,
        ):
            result = server.feed_for_request()

        self.assertEqual(result["articles"], cached["articles"])
        self.assertTrue(result["refreshing"])
        refresh.assert_called_once_with()

    def test_recent_failed_attempt_uses_backoff(self):
        cached = self.cached_feed(
            age_seconds=server.CACHE_TTL_SECONDS + 1,
            retry_age_seconds=5,
        )
        cached["stale"] = True
        with (
            mock.patch.object(server, "read_cache", return_value=cached),
            mock.patch.object(server, "refresh_feed_in_background") as refresh,
        ):
            result = server.feed_for_request()

        self.assertTrue(result["stale"])
        self.assertFalse(result["refreshing"])
        refresh.assert_not_called()

    def test_wait_request_returns_completed_cache_without_an_extra_refresh(self):
        cached = self.cached_feed(age_seconds=1)
        with (
            mock.patch.object(server, "read_cache", return_value=cached),
            mock.patch.object(server, "refresh_feed_in_background") as refresh,
        ):
            result = server.feed_for_request(wait_for_refresh=True)

        self.assertEqual(result["articles"], cached["articles"])
        self.assertFalse(result["refreshing"])
        refresh.assert_not_called()

    def test_thumbnail_wait_returns_the_enriched_cache(self):
        cached = self.cached_feed(age_seconds=1)
        with (
            mock.patch.object(server, "wait_for_thumbnail_refresh") as wait,
            mock.patch.object(server, "read_cache", return_value=cached),
        ):
            result = server.feed_for_request(wait_for_thumbnails=True)

        wait.assert_called_once_with()
        self.assertEqual(result["articles"], cached["articles"])

    def test_orphaned_thumbnail_refresh_state_is_cleared_after_restart(self):
        cached = {
            **self.cached_feed(age_seconds=1),
            "thumbnails_refreshing": True,
        }
        previous_active = server.THUMBNAIL_WORKER_ACTIVE
        server.THUMBNAIL_WORKER_ACTIVE = False
        try:
            result = server.cached_feed_payload(cached)
        finally:
            server.THUMBNAIL_WORKER_ACTIVE = previous_active

        self.assertFalse(result["thumbnails_refreshing"])

    def test_active_thumbnail_refresh_state_is_preserved(self):
        cached = {
            **self.cached_feed(age_seconds=1),
            "thumbnails_refreshing": True,
        }
        previous_active = server.THUMBNAIL_WORKER_ACTIVE
        server.THUMBNAIL_WORKER_ACTIVE = True
        try:
            result = server.cached_feed_payload(cached)
        finally:
            server.THUMBNAIL_WORKER_ACTIVE = previous_active

        self.assertTrue(result["thumbnails_refreshing"])


if __name__ == "__main__":
    unittest.main()
