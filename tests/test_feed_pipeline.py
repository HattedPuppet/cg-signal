import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from unittest import mock

from cg_signal.config import FEED_SCHEMA_VERSION, RuntimePaths, SOURCE_CACHE_SCHEMA_VERSION
from cg_signal.feeds import FeedService


def source_fixture():
    return {
        "id": "example", "name": "Example", "site": "https://example.com",
        "feed": "https://example.com/feed.xml", "accent": "#ffffff",
    }


def cached_article():
    return {
        "id": "article-1", "title": "Cached article", "url": "https://example.com/article",
        "summary": "Summary", "image": "", "published_at": "2026-07-30T00:00:00+00:00",
        "timestamp": 1785369600.0, "source": "Example", "source_id": "example",
        "source_site": "https://example.com", "accent": "#ffffff",
        "topic": "Production techniques", "lane": "Tech & Development", "_refs": [],
    }


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = FeedService(RuntimePaths.for_root(Path(self.temporary.name)))

    def tearDown(self):
        self.temporary.cleanup()


class ConditionalFeedRequestTests(ServiceTestCase):
    def cache_entry(self):
        return {
            "feed": "https://example.com/feed.xml", "etag": '"feed-v2"',
            "last_modified": "Thu, 30 Jul 2026 00:00:00 GMT", "articles": [cached_article()],
        }

    def test_304_reuses_cached_articles_and_sends_validators(self):
        headers = Message()
        headers["ETag"] = '"feed-v2"'
        not_modified = urllib.error.HTTPError(
            "https://example.com/feed.xml", 304, "Not Modified", headers, None
        )
        with mock.patch("cg_signal.feeds.urllib.request.urlopen", side_effect=not_modified) as urlopen:
            result = self.service.fetch_source(source_fixture(), self.cache_entry())
        request = urlopen.call_args.args[0]
        request_headers = dict(request.header_items())
        self.assertEqual(request_headers["If-none-match"], '"feed-v2"')
        self.assertEqual(request_headers["If-modified-since"], "Thu, 30 Jul 2026 00:00:00 GMT")
        self.assertTrue(result["ok"])
        self.assertTrue(result["not_modified"])
        self.assertEqual(result["articles"][0]["id"], "article-1")

    def test_temporary_failure_reuses_the_last_source_snapshot(self):
        with mock.patch(
            "cg_signal.feeds.urllib.request.urlopen",
            side_effect=urllib.error.URLError("temporary outage"),
        ):
            result = self.service.fetch_source(source_fixture(), self.cache_entry())
        self.assertFalse(result["ok"])
        self.assertTrue(result["used_stale_cache"])
        self.assertEqual(result["articles"][0]["id"], "article-1")


class FeedSourceCacheVersionTests(ServiceTestCase):
    def test_source_cache_schema_is_independent_of_classifier_revision(self):
        cache_file = self.service.paths.feed_source_cache_file
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            '{"schema_version": 0, "sources": {"example": {"articles": []}}}',
            encoding="utf-8",
        )
        self.assertEqual(self.service.read_feed_source_cache(), {})
        cache_file.write_text(
            f'{{"schema_version": {SOURCE_CACHE_SCHEMA_VERSION}, "sources": {{"example": {{"articles": []}}}}}}',
            encoding="utf-8",
        )
        self.assertEqual(self.service.read_feed_source_cache()["example"]["articles"], [])


class FeedFallbackSchemaTests(ServiceTestCase):
    def test_failed_refresh_does_not_promote_incompatible_cached_articles(self):
        source = source_fixture()
        failure = {
            "source": source,
            "articles": [],
            "ok": False,
            "message": "offline",
            "duration_ms": 12,
            "etag": "",
            "last_modified": "",
            "not_modified": False,
            "used_stale_cache": False,
        }
        incompatible = {
            "feed_schema_version": FEED_SCHEMA_VERSION + 1,
            "classification_revision": 1,
            "articles": [{"foreign_shape": True}],
        }
        with (
            mock.patch.object(self.service.repository, "list_source_configs", return_value=[source]),
            mock.patch.object(self.service, "read_feed_source_cache", return_value={}),
            mock.patch.object(self.service, "fetch_source", return_value=failure),
            mock.patch.object(self.service, "update_feed_source_cache"),
            mock.patch.object(self.service.repository, "archive_article_count", return_value=0),
            mock.patch.object(self.service, "write_cache") as write_cache,
        ):
            result = self.service.refresh_feed(incompatible)
        self.assertEqual(result["feed_schema_version"], FEED_SCHEMA_VERSION)
        self.assertEqual(result["articles"], [])
        self.assertEqual(write_cache.call_args.args[0]["articles"], [])
        self.assertFalse(self.service.cached_feed_is_fresh(result))
        self.assertFalse(self.service.feed_refresh_is_due(result))
        result["last_refresh_attempt_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=61)
        ).isoformat()
        self.assertTrue(self.service.feed_refresh_is_due(result))


class ThumbnailPipelineTests(ServiceTestCase):
    def test_refresh_publishes_articles_before_thumbnail_scraping(self):
        source, article = source_fixture(), cached_article()
        source_result = {
            "source": source, "articles": [article], "ok": True, "message": "",
            "duration_ms": 12, "etag": '"feed-v2"', "last_modified": "",
            "not_modified": False, "used_stale_cache": False,
        }
        with (
            mock.patch.object(self.service.repository, "list_source_configs", return_value=[source]),
            mock.patch.object(self.service, "read_feed_source_cache", return_value={"example": {"feed": source["feed"]}}),
            mock.patch.object(self.service, "fetch_source", return_value=source_result) as fetch,
            mock.patch.object(self.service, "update_feed_source_cache"),
            mock.patch.object(self.service, "apply_cached_images", return_value=[article]),
            mock.patch("cg_signal.feeds.cluster_articles", return_value=[article]),
            mock.patch.object(self.service.repository, "archive_articles", return_value=1),
            mock.patch.object(self.service, "write_cache") as write_cache,
            mock.patch.object(self.service, "schedule_thumbnail_enrichment", return_value=True) as schedule,
            mock.patch.object(self.service, "enrich_missing_images") as enrich,
        ):
            payload = self.service.refresh_feed()
        fetch.assert_called_once_with(source, {"feed": source["feed"]})
        write_cache.assert_called_once()
        self.assertTrue(write_cache.call_args.args[0]["thumbnails_refreshing"])
        schedule.assert_called_once()
        enrich.assert_not_called()
        self.assertEqual(payload["articles"], [article])

    def test_completed_thumbnail_worker_updates_only_the_matching_feed(self):
        article, enriched = cached_article(), {**cached_article(), "image": "https://example.com/preview.jpg"}
        cached = {"generated_at": "2026-07-30T00:00:00+00:00", "articles": [article]}
        with mock.patch.object(self.service, "read_cache", return_value=cached), mock.patch.object(self.service, "write_cache") as write_cache:
            self.service.update_cached_thumbnail_images([enriched], cached["generated_at"])
        written = write_cache.call_args.args[0]
        self.assertEqual(written["articles"][0]["image"], "https://example.com/preview.jpg")
        self.assertFalse(written["thumbnails_refreshing"])

    def test_completed_thumbnail_worker_clears_state_when_no_image_is_found(self):
        article = cached_article()
        cached = {"generated_at": "2026-07-30T00:00:00+00:00", "articles": [article], "thumbnails_refreshing": True}
        with mock.patch.object(self.service, "read_cache", return_value=cached), mock.patch.object(self.service, "write_cache") as write_cache:
            self.service.update_cached_thumbnail_images([article], cached["generated_at"])
        written = write_cache.call_args.args[0]
        self.assertEqual(written["articles"], [article])
        self.assertFalse(written["thumbnails_refreshing"])

    def test_thumbnail_worker_clears_state_after_enrichment_failure(self):
        article, generated_at = cached_article(), "2026-07-30T00:00:00+00:00"
        cached = {"generated_at": generated_at, "articles": [article], "thumbnails_refreshing": True}
        self.service._thumbnail_pending = ([article], generated_at)
        self.service._thumbnail_worker_active = True
        with (
            mock.patch.object(self.service, "enrich_missing_images", side_effect=RuntimeError("boom")),
            mock.patch.object(self.service, "read_cache", return_value=cached),
            mock.patch.object(self.service, "write_cache") as write_cache,
        ):
            self.service._thumbnail_worker()
        written = write_cache.call_args.args[0]
        self.assertFalse(written["thumbnails_refreshing"])


if __name__ == "__main__":
    unittest.main()
