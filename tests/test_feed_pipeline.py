import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from unittest import mock

from cg_signal.config import FEED_SCHEMA_VERSION, RuntimePaths, SOURCE_CACHE_SCHEMA_VERSION
from cg_signal.feeds import FeedService
from cg_signal.safe_http import SafeHttpError, SafeHttpResponse
from cg_signal.thumbnails import (
    THUMBNAIL_NEGATIVE_TTL_SECONDS,
    canonical_thumbnail_reference,
    store_thumbnail,
    validate_thumbnail_bytes,
)


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

    def test_rendered_thumbnail_policy_keeps_only_local_paths(self):
        for remote in (
            "http://127.0.0.1/pixel.png",
            "https://192.168.1.2/pixel.png",
            "https://169.254.169.254/latest/meta-data",
            "https://cdn.example.test/pixel.png",
        ):
            self.assertEqual(canonical_thumbnail_reference(remote), "")
        self.assertEqual(canonical_thumbnail_reference("/assets/pixel.png"), "")
        self.assertEqual(
            canonical_thumbnail_reference("thumbnails/" + "a" * 64 + ".jpg"),
            "thumbnails/" + "a" * 64 + ".jpg",
        )


class FeedIngestionTests(ServiceTestCase):
    def feed_response(self, xml: bytes) -> SafeHttpResponse:
        headers = Message()
        headers["Content-Type"] = "application/rss+xml"
        return SafeHttpResponse(200, headers, xml, source_fixture()["feed"])

    def cache_entry(self):
        return {
            "feed": source_fixture()["feed"], "etag": '"feed-v2"',
            "last_modified": "Thu, 30 Jul 2026 00:00:00 GMT", "articles": [cached_article()],
        }

    def test_valid_entries_are_sorted_before_source_limit(self):
        xml = b"""<?xml version='1.0'?><rss><channel>
            <item><title>Oldest</title><link>https://example.com/oldest</link><pubDate>2026-08-01T00:00:00Z</pubDate></item>
            <item><title>Newest</title><link>https://example.com/newest</link><pubDate>2026-08-03T00:00:00Z</pubDate></item>
            <item><title>Middle</title><link>https://example.com/middle</link><pubDate>2026-08-02T00:00:00Z</pubDate></item>
        </channel></rss>"""
        source = {**source_fixture(), "limit": 2}
        with mock.patch.object(self.service.http, "get", return_value=self.feed_response(xml)):
            result = self.service.fetch_source(source)
        self.assertTrue(result["ok"])
        self.assertEqual([article["title"] for article in result["articles"]], ["Newest", "Middle"])
        self.assertEqual(result["diagnostics"]["accepted"], 3)

    def test_invalid_preferred_date_falls_through_to_valid_candidate(self):
        xml = b"""<rss><channel><item>
            <title>Fallback date</title><link>https://example.com/fallback</link>
            <pubDate>not a date</pubDate><published>2026-08-03T00:00:00Z</published>
        </item></channel></rss>"""
        with mock.patch.object(self.service.http, "get", return_value=self.feed_response(xml)):
            result = self.service.fetch_source(source_fixture())
        self.assertTrue(result["ok"])
        self.assertEqual(result["articles"][0]["title"], "Fallback date")
        self.assertEqual(result["diagnostics"]["invalid_date"], 0)

    def test_invalid_sibling_does_not_discard_valid_sibling(self):
        xml = b"""<rss><channel>
            <item><title>Invalid</title><link>https://example.com/invalid</link><pubDate>bad</pubDate></item>
            <item><title>Valid</title><link>https://example.com/valid</link><pubDate>2026-08-03T00:00:00Z</pubDate></item>
        </channel></rss>"""
        with mock.patch.object(self.service.http, "get", return_value=self.feed_response(xml)):
            result = self.service.fetch_source(source_fixture())
        self.assertTrue(result["ok"])
        self.assertEqual([article["title"] for article in result["articles"]], ["Valid"])
        self.assertEqual(result["diagnostics"]["invalid_date"], 1)

    def test_overflowing_date_does_not_discard_valid_sibling(self):
        xml = b"""<rss><channel>
            <item><title>Overflow</title><link>https://example.com/overflow</link><pubDate>0001-01-01T00:00:00+23:59</pubDate></item>
            <item><title>Valid</title><link>https://example.com/valid</link><pubDate>2026-08-03T00:00:00Z</pubDate></item>
        </channel></rss>"""
        with mock.patch.object(self.service.http, "get", return_value=self.feed_response(xml)):
            result = self.service.fetch_source(source_fixture())
        self.assertTrue(result["ok"])
        self.assertEqual([article["title"] for article in result["articles"]], ["Valid"])
        self.assertEqual(result["diagnostics"]["invalid_date"], 1)

    def test_all_unusable_entries_fail_and_reuse_cached_snapshot(self):
        xml = b"""<rss><channel><item>
            <title>Undated</title><link>https://example.com/undated</link>
        </item></channel></rss>"""
        with mock.patch.object(self.service.http, "get", return_value=self.feed_response(xml)):
            result = self.service.fetch_source(source_fixture(), self.cache_entry())
        self.assertFalse(result["ok"])
        self.assertTrue(result["used_stale_cache"])
        self.assertEqual(result["articles"][0]["id"], "article-1")
        self.assertEqual(result["diagnostics"]["missing_date"], 1)

    def test_more_than_one_thousand_entries_fails_explicitly(self):
        items = b"".join(
            f"<item><title>Item {index}</title><link>https://example.com/{index}</link>"
            f"<pubDate>2026-08-03T00:00:00Z</pubDate></item>".encode("ascii")
            for index in range(1001)
        )
        xml = b"<rss><channel>" + items + b"</channel></rss>"
        with mock.patch.object(self.service.http, "get", return_value=self.feed_response(xml)):
            result = self.service.fetch_source(source_fixture())
        self.assertFalse(result["ok"])
        self.assertIn("more than 1000", result["message"])
        self.assertEqual(result["diagnostics"]["total"], 1001)

    def test_ingestion_diagnostics_distinguish_rejection_categories(self):
        xml = b"""<rss><channel>
            <item><title>Accepted</title><link>https://example.com/accepted</link><pubDate>2026-08-03T00:00:00Z</pubDate></item>
            <item><title>Missing date</title><link>https://example.com/missing</link></item>
            <item><title>Invalid date</title><link>https://example.com/invalid</link><pubDate>bad</pubDate></item>
            <item><link>https://example.com/no-title</link><pubDate>2026-08-03T00:00:00Z</pubDate></item>
        </channel></rss>"""
        with mock.patch.object(self.service.http, "get", return_value=self.feed_response(xml)):
            result = self.service.fetch_source(source_fixture())
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["diagnostics"],
            {
                "total": 4,
                "accepted": 1,
                "missing_date": 1,
                "invalid_date": 1,
                "missing_title_or_link": 1,
            },
        )


class ConditionalFeedRequestTests(ServiceTestCase):
    def cache_entry(self):
        return {
            "feed": "https://example.com/feed.xml", "etag": '"feed-v2"',
            "last_modified": "Thu, 30 Jul 2026 00:00:00 GMT", "articles": [cached_article()],
        }

    def test_304_reuses_cached_articles_and_sends_validators(self):
        headers = Message()
        headers["ETag"] = '"feed-v2"'
        not_modified = SafeHttpResponse(304, headers, b"", "https://example.com/feed.xml")
        with mock.patch.object(self.service.http, "get", return_value=not_modified) as http_get:
            result = self.service.fetch_source(source_fixture(), self.cache_entry())
        request_headers = http_get.call_args.kwargs["headers"]
        self.assertEqual(request_headers["If-None-Match"], '"feed-v2"')
        self.assertEqual(request_headers["If-Modified-Since"], "Thu, 30 Jul 2026 00:00:00 GMT")
        self.assertTrue(result["ok"])
        self.assertTrue(result["not_modified"])
        self.assertEqual(result["articles"][0]["id"], "article-1")

    def test_temporary_failure_reuses_the_last_source_snapshot(self):
        with mock.patch.object(self.service.http, "get", side_effect=SafeHttpError("temporary outage")):
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
            mock.patch.object(self.service.repository, "history_article_count", return_value=0),
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
    def test_stale_negative_thumbnail_cache_is_retried(self):
        article = {**cached_article(), "_thumbnail_candidate": "https://cdn.example/image.jpg"}
        self.service.write_image_index({
            "schema_version": 1,
            "entries": {
                article["url"]: {
                    "status": "miss",
                    "checked_at": time.time() - THUMBNAIL_NEGATIVE_TTL_SECONDS - 1,
                },
            },
        })
        with (
            mock.patch.object(self.service, "_fetch_thumbnail_asset", return_value="") as fetch_asset,
            mock.patch.object(self.service, "fetch_page_image", return_value=""),
        ):
            self.service.enrich_missing_images([article])
        self.assertEqual(fetch_asset.call_args_list[0].args[0], "https://cdn.example/image.jpg")

    def test_enrichment_prioritizes_newest_articles_across_source_order(self):
        articles = [
            {**cached_article(), "url": "https://example.com/old", "published_at": "2026-07-01T00:00:00+00:00", "_thumbnail_candidate": "old"},
            {**cached_article(), "id": "article-2", "url": "https://example.com/new", "published_at": "2026-07-30T00:00:00+00:00", "_thumbnail_candidate": "new"},
        ]
        with (
            mock.patch.object(self.service, "apply_cached_images", return_value=articles),
            mock.patch.object(self.service, "_fetch_thumbnail_asset", return_value="") as fetch_asset,
            mock.patch.object(self.service, "fetch_page_image", return_value=""),
        ):
            self.service.enrich_missing_images(articles)
        self.assertEqual(
            [call.args[0] for call in fetch_asset.call_args_list if call.args[0]],
            ["new", "old"],
        )

    def test_enrichment_keeps_running_thumbnail_that_finishes_after_batch_wait(self):
        article = cached_article()

        def fetch_and_store(*_args):
            return store_thumbnail(
                self.service.paths.thumbnail_dir,
                validate_thumbnail_bytes(b"\xff\xd8\xfflate", "image/jpeg"),
            )

        def report_all_as_pending(futures, **_kwargs):
            return set(), set(futures)

        with (
            mock.patch.object(self.service, "apply_cached_images", return_value=[article]),
            mock.patch.object(self.service, "_fetch_thumbnail_asset", side_effect=fetch_and_store),
            mock.patch("cg_signal.feeds.concurrent.futures.wait", side_effect=report_all_as_pending),
        ):
            self.service.enrich_missing_images([article])

        reference = article["image"]
        self.assertRegex(reference, r"^thumbnails/[0-9a-f]{64}\.jpg$")
        self.assertEqual(
            self.service.read_image_index()["entries"][article["url"]]["image"],
            reference,
        )

    def test_cached_cluster_uses_image_fetched_from_related_member(self):
        primary = cached_article()
        primary["related"] = [{"url": "https://example.com/related"}]
        generated_at = "2026-08-28T00:00:00+00:00"
        self.service.write_cache({"generated_at": generated_at, "articles": [primary], "thumbnails_refreshing": True})
        image = store_thumbnail(self.service.paths.thumbnail_dir, b"\x89PNG\r\n\x1a\nmember", "image/png")
        member = {"url": "https://example.com/related", "image": image}
        self.service.update_cached_thumbnail_images([member], generated_at)
        self.assertEqual(self.service.read_cache()["articles"][0]["image"], image)

    def test_rss_candidate_is_fetched_and_stored_as_app_owned_reference(self):
        article = {**cached_article(), "_thumbnail_candidate": "https://cdn.example/image.jpg"}
        headers = Message()
        headers["Content-Type"] = "image/jpeg"
        response = SafeHttpResponse(200, headers, b"\xff\xd8\xffasset", "https://cdn.example/image.jpg")
        with mock.patch.object(self.service.http, "get", return_value=response) as get:
            self.service.enrich_missing_images([article])
        self.assertRegex(article["image"], r"^thumbnails/[0-9a-f]{64}\.jpg$")
        self.assertEqual(get.call_count, 1)
        self.assertIsNotNone(
            __import__("cg_signal.thumbnails", fromlist=["read_verified_thumbnail"]).read_verified_thumbnail(
                self.service.paths.thumbnail_dir, article["image"]
            )
        )

    def test_enrichment_replaces_oldest_asset_when_store_is_full(self):
        entries = {}
        old_paths = []
        for index in range(2):
            article_url = f"https://example.com/old-{index}"
            reference = store_thumbnail(
                self.service.paths.thumbnail_dir,
                b"\x89PNG\r\n\x1a\n" + bytes([index]) * 8,
                "image/png",
                expected_anchor=self.service.paths.thumbnail_anchor,
                max_files=2,
                max_bytes=128,
            )
            old_path = self.service.paths.thumbnail_dir / Path(reference).name
            old_paths.append(old_path)
            entries[article_url] = {
                "status": "ok",
                "image": reference,
                "checked_at": time.time(),
            }
        old_paths[0].touch()
        old_paths[1].touch()
        self.service.write_image_index({"schema_version": 1, "entries": entries})

        article = {
            **cached_article(),
            "id": "new",
            "url": "https://example.com/new",
            "published_at": "2026-08-31T00:00:00+00:00",
            "_thumbnail_candidate": "https://cdn.example/new.png",
        }
        headers = Message()
        headers["Content-Type"] = "image/png"
        response = SafeHttpResponse(
            200, headers, b"\x89PNG\r\n\x1a\nnew-image", article["_thumbnail_candidate"]
        )
        real_store = store_thumbnail

        def constrained_store(root, thumbnail, mime_type=None, **kwargs):
            return real_store(
                root,
                thumbnail,
                mime_type,
                expected_anchor=kwargs.get("expected_anchor"),
                max_files=2,
                max_bytes=128,
            )

        with (
            mock.patch.object(self.service.http, "get", return_value=response),
            mock.patch("cg_signal.feeds.store_thumbnail", side_effect=constrained_store),
        ):
            self.service.enrich_missing_images([article])

        current_index = self.service.read_image_index()["entries"]
        self.assertRegex(article["image"], r"^thumbnails/[0-9a-f]{64}\.png$")
        self.assertEqual(current_index[article["url"]]["image"], article["image"])
        self.assertEqual(len(list(self.service.paths.thumbnail_dir.glob("*.png"))), 2)
        self.assertEqual(sum(url in current_index for url in entries), 1)

    def test_page_og_candidate_is_fetched_only_after_rss_candidate_fails(self):
        article = {**cached_article(), "_thumbnail_candidate": "https://cdn.example/bad.gif"}
        page_headers = Message()
        page_headers["Content-Type"] = "text/html; charset=utf-8"
        image_headers = Message()
        image_headers["Content-Type"] = "image/png"
        responses = [
            SafeHttpResponse(200, Message(), b"GIF89a", "https://cdn.example/bad.gif"),
            SafeHttpResponse(200, page_headers, b'<meta property="og:image" content="/hero.png">', "https://example.com/final"),
            SafeHttpResponse(200, image_headers, b"\x89PNG\r\n\x1a\nasset", "https://example.com/hero.png"),
        ]
        responses[0].headers["Content-Type"] = "image/gif"
        with mock.patch.object(self.service.http, "get", side_effect=responses) as get:
            self.service.enrich_missing_images([article])
        self.assertRegex(article["image"], r"^thumbnails/[0-9a-f]{64}\.png$")
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args_list[1].args[0], "https://example.com/article")
        self.assertEqual(get.call_args_list[2].args[0], "https://example.com/hero.png")

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
            mock.patch.object(self.service.repository, "record_articles", return_value=1),
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
        reference = store_thumbnail(
            self.service.paths.thumbnail_dir,
            validate_thumbnail_bytes(b"\xff\xd8\xffasset", "image/jpeg"),
        )
        article, enriched = cached_article(), {**cached_article(), "image": reference}
        cached = {"generated_at": "2026-07-30T00:00:00+00:00", "articles": [article]}
        with mock.patch.object(self.service, "read_cache", return_value=cached), mock.patch.object(self.service, "write_cache") as write_cache:
            self.service.update_cached_thumbnail_images([enriched], cached["generated_at"])
        written = write_cache.call_args.args[0]
        self.assertEqual(written["articles"][0]["image"], reference)
        self.assertFalse(written["thumbnails_refreshing"])

    def test_completed_thumbnail_worker_clears_state_when_no_image_is_found(self):
        article = cached_article()
        cached = {"generated_at": "2026-07-30T00:00:00+00:00", "articles": [article], "thumbnails_refreshing": True}
        with mock.patch.object(self.service, "read_cache", return_value=cached), mock.patch.object(self.service, "write_cache") as write_cache:
            self.service.update_cached_thumbnail_images([article], cached["generated_at"])
        written = write_cache.call_args.args[0]
        self.assertEqual(written["articles"], [article])
        self.assertFalse(written["thumbnails_refreshing"])

    def test_thumbnail_worker_reports_failure_after_clearing_desktop_state(self):
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
        self.assertFalse(self.service.wait_for_thumbnail_refresh())


if __name__ == "__main__":
    unittest.main()
