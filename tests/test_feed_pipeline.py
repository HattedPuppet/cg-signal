import unittest
import urllib.error
from email.message import Message
from unittest import mock

import server


def source_fixture():
    return {
        "id": "example",
        "name": "Example",
        "site": "https://example.com",
        "feed": "https://example.com/feed.xml",
        "accent": "#ffffff",
    }


def cached_article():
    return {
        "id": "article-1",
        "title": "Cached article",
        "url": "https://example.com/article",
        "summary": "Summary",
        "image": "",
        "published_at": "2026-07-30T00:00:00+00:00",
        "timestamp": 1785369600.0,
        "source": "Example",
        "source_id": "example",
        "source_site": "https://example.com",
        "accent": "#ffffff",
        "topic": "Production techniques",
        "lane": "Tech & Development",
        "_refs": [],
    }


class ConditionalFeedRequestTests(unittest.TestCase):
    def cache_entry(self):
        return {
            "feed": "https://example.com/feed.xml",
            "etag": '"feed-v2"',
            "last_modified": "Thu, 30 Jul 2026 00:00:00 GMT",
            "articles": [cached_article()],
        }

    def test_304_reuses_cached_articles_and_sends_validators(self):
        headers = Message()
        headers["ETag"] = '"feed-v2"'
        not_modified = urllib.error.HTTPError(
            "https://example.com/feed.xml",
            304,
            "Not Modified",
            headers,
            None,
        )
        with mock.patch.object(
            server.urllib.request, "urlopen", side_effect=not_modified
        ) as urlopen:
            result = server.fetch_source(source_fixture(), self.cache_entry())

        request = urlopen.call_args.args[0]
        request_headers = dict(request.header_items())
        self.assertEqual(request_headers["If-none-match"], '"feed-v2"')
        self.assertEqual(
            request_headers["If-modified-since"],
            "Thu, 30 Jul 2026 00:00:00 GMT",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["not_modified"])
        self.assertEqual(result["articles"][0]["id"], "article-1")

    def test_temporary_failure_reuses_the_last_source_snapshot(self):
        with mock.patch.object(
            server.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("temporary outage"),
        ):
            result = server.fetch_source(source_fixture(), self.cache_entry())

        self.assertFalse(result["ok"])
        self.assertTrue(result["used_stale_cache"])
        self.assertEqual(result["articles"][0]["id"], "article-1")


class ThumbnailPipelineTests(unittest.TestCase):
    def test_refresh_publishes_articles_before_thumbnail_scraping(self):
        source = source_fixture()
        article = cached_article()
        source_result = {
            "source": source,
            "articles": [article],
            "ok": True,
            "message": "",
            "duration_ms": 12,
            "etag": '"feed-v2"',
            "last_modified": "",
            "not_modified": False,
            "used_stale_cache": False,
        }
        with (
            mock.patch.object(server, "list_source_configs", return_value=[source]),
            mock.patch.object(
                server,
                "read_feed_source_cache",
                return_value={"example": {"feed": source["feed"]}},
            ),
            mock.patch.object(server, "fetch_source", return_value=source_result) as fetch,
            mock.patch.object(server, "update_feed_source_cache"),
            mock.patch.object(
                server, "apply_cached_images", return_value=[article]
            ),
            mock.patch.object(server, "cluster_articles", return_value=[article]),
            mock.patch.object(server, "archive_articles", return_value=1),
            mock.patch.object(server, "write_cache") as write_cache,
            mock.patch.object(
                server, "schedule_thumbnail_enrichment", return_value=True
            ) as schedule,
            mock.patch.object(server, "enrich_missing_images") as enrich,
        ):
            payload = server.refresh_feed()

        fetch.assert_called_once_with(source, {"feed": source["feed"]})
        write_cache.assert_called_once()
        schedule.assert_called_once()
        enrich.assert_not_called()
        self.assertEqual(payload["articles"], [article])
        self.assertTrue(payload["thumbnails_refreshing"])

    def test_completed_thumbnail_worker_updates_only_the_matching_feed(self):
        article = cached_article()
        enriched = {**article, "image": "https://example.com/preview.jpg"}
        cached = {
            "generated_at": "2026-07-30T00:00:00+00:00",
            "articles": [article],
        }
        with (
            mock.patch.object(server, "read_cache", return_value=cached),
            mock.patch.object(server, "write_cache") as write_cache,
        ):
            server.update_cached_thumbnail_images(
                [enriched], "2026-07-30T00:00:00+00:00"
            )

        written = write_cache.call_args.args[0]
        self.assertEqual(
            written["articles"][0]["image"],
            "https://example.com/preview.jpg",
        )
        self.assertFalse(written["thumbnails_refreshing"])


if __name__ == "__main__":
    unittest.main()
