import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cg_signal.config import (
    CACHE_TTL_SECONDS,
    CLASSIFICATION_REVISION,
    FEED_SCHEMA_VERSION,
    RuntimePaths,
)
from cg_signal.feeds import FeedService


class FeedCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = FeedService(RuntimePaths.for_root(Path(self.temporary.name)))

    def tearDown(self):
        self.temporary.cleanup()

    def cached_feed(self, *, age_seconds: int, retry_age_seconds: int | None = None):
        now = datetime.now(timezone.utc)
        payload = {
            "feed_schema_version": FEED_SCHEMA_VERSION,
            "classification_revision": CLASSIFICATION_REVISION,
            "classification_version": CLASSIFICATION_REVISION,
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
            mock.patch.object(self.service, "read_cache", return_value=cached),
            mock.patch.object(self.service, "refresh_feed_in_background") as refresh,
        ):
            result = self.service.feed_for_request()
        self.assertTrue(result["cached"])
        self.assertFalse(result["refreshing"])
        refresh.assert_not_called()

    def test_incompatible_cache_is_rebuilt_synchronously_without_background_refresh(self):
        cached = self.cached_feed(age_seconds=30)
        cached.pop("feed_schema_version")
        rebuilt = {"articles": [{"id": "rebuilt-story"}], "cached": False}
        with (
            mock.patch.object(self.service, "read_cache", return_value=cached),
            mock.patch.object(self.service, "build_feed", return_value=rebuilt) as build,
            mock.patch.object(self.service, "refresh_feed_in_background") as refresh,
        ):
            result = self.service.feed_for_request()
        self.assertEqual(result["articles"], rebuilt["articles"])
        build.assert_called_once_with()
        refresh.assert_not_called()

    def test_concurrent_incompatible_cache_callers_share_one_rebuild(self):
        cached = self.cached_feed(age_seconds=30)
        cached.pop("feed_schema_version")
        rebuilt = {
            **self.cached_feed(age_seconds=0),
            "articles": [{"id": "rebuilt-story"}],
            "cached": False,
            "stale": False,
        }
        cache = {"payload": cached}
        cache_lock = threading.Lock()
        initial_reads = threading.Barrier(2)
        build_reads = threading.Barrier(2)
        read_count = 0
        refresh_started = threading.Event()
        release_refresh = threading.Event()

        def read_cache():
            nonlocal read_count
            with cache_lock:
                read_count += 1
                call_number = read_count
                payload = cache["payload"]
            if call_number <= 2:
                initial_reads.wait(timeout=5)
            elif call_number <= 4:
                build_reads.wait(timeout=5)
            return payload

        def refresh_feed(_cached):
            refresh_started.set()
            if not release_refresh.wait(timeout=5):
                raise AssertionError("timed out waiting to release the shared refresh")
            with cache_lock:
                cache["payload"] = rebuilt
            return rebuilt

        results = [None, None]
        errors = []

        def request(index):
            try:
                results[index] = self.service.feed_for_request()
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        with (
            mock.patch.object(self.service, "read_cache", side_effect=read_cache),
            mock.patch.object(self.service, "refresh_feed", side_effect=refresh_feed) as refresh,
            mock.patch.object(self.service, "refresh_feed_in_background") as background,
        ):
            threads = [threading.Thread(target=request, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            self.assertTrue(refresh_started.wait(timeout=5))
            release_refresh.set()
            for thread in threads:
                thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        if errors:
            raise errors[0]
        self.assertEqual([result["articles"] for result in results], [rebuilt["articles"]] * 2)
        refresh.assert_called_once()
        background.assert_not_called()

    def test_previous_classifier_cache_is_not_fresh(self):
        cached = self.cached_feed(age_seconds=30)
        cached["classification_revision"] = CLASSIFICATION_REVISION - 1
        self.assertFalse(self.service.cached_feed_is_fresh(cached))

    def test_previous_classifier_cache_is_relabelled_while_refreshing(self):
        cached = self.cached_feed(age_seconds=30)
        cached["classification_revision"] = CLASSIFICATION_REVISION - 1
        cached["articles"] = [{
            "id": "stale-gacha",
            "title": "期間限定ガチャイベントを開催",
            "summary": "新キャラを追加",
            "source_id": "automaton",
            "lane": "Tech & Development",
        }]
        result = self.service.cached_feed_payload(cached, refreshing=True)
        self.assertEqual(result["classification_revision"], CLASSIFICATION_REVISION)
        self.assertEqual(result["articles"][0]["lane"], "Industry")
        self.assertTrue(result["refreshing"])

    def test_expired_cache_is_returned_while_refresh_starts(self):
        cached = self.cached_feed(age_seconds=CACHE_TTL_SECONDS + 1)
        with (
            mock.patch.object(self.service, "read_cache", return_value=cached),
            mock.patch.object(self.service, "refresh_feed_in_background", return_value=True) as refresh,
        ):
            result = self.service.feed_for_request()
        self.assertEqual(result["articles"], cached["articles"])
        self.assertTrue(result["refreshing"])
        refresh.assert_called_once_with()

    def test_recent_failed_attempt_uses_backoff(self):
        cached = self.cached_feed(age_seconds=CACHE_TTL_SECONDS + 1, retry_age_seconds=5)
        cached["stale"] = True
        with (
            mock.patch.object(self.service, "read_cache", return_value=cached),
            mock.patch.object(self.service, "refresh_feed_in_background") as refresh,
        ):
            result = self.service.feed_for_request()
        self.assertTrue(result["stale"])
        self.assertFalse(result["refreshing"])
        refresh.assert_not_called()

    def test_wait_request_returns_completed_cache_without_an_extra_refresh(self):
        cached = self.cached_feed(age_seconds=1)
        with (
            mock.patch.object(self.service, "read_cache", return_value=cached),
            mock.patch.object(self.service, "refresh_feed_in_background") as refresh,
        ):
            result = self.service.feed_for_request(wait_for_refresh=True)
        self.assertEqual(result["articles"], cached["articles"])
        self.assertFalse(result["refreshing"])
        refresh.assert_not_called()

    def test_thumbnail_wait_returns_the_enriched_cache(self):
        cached = self.cached_feed(age_seconds=1)
        with (
            mock.patch.object(self.service, "wait_for_thumbnail_refresh") as wait,
            mock.patch.object(self.service, "read_cache", return_value=cached),
        ):
            result = self.service.feed_for_request(wait_for_thumbnails=True)
        wait.assert_called_once_with()
        self.assertEqual(result["articles"], cached["articles"])

    def test_orphaned_thumbnail_refresh_state_is_cleared_after_restart(self):
        cached = {**self.cached_feed(age_seconds=1), "thumbnails_refreshing": True}
        result = self.service.cached_feed_payload(cached)
        self.assertFalse(result["thumbnails_refreshing"])

    def test_active_thumbnail_refresh_state_is_preserved(self):
        cached = {**self.cached_feed(age_seconds=1), "thumbnails_refreshing": True}
        self.service._thumbnail_worker_active = True
        try:
            result = self.service.cached_feed_payload(cached)
        finally:
            self.service._thumbnail_worker_active = False
        # A restarted service clears an orphaned flag; an active worker remains
        # observable to the caller while the refresh is in flight.
        self.assertTrue(result["thumbnails_refreshing"])


if __name__ == "__main__":
    unittest.main()
