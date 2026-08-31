import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from cg_signal.thumbnails import store_thumbnail, validate_thumbnail_bytes


MODULE_PATH = Path(__file__).resolve().parents[1] / "mobile" / "build_mobile.py"
SPEC = importlib.util.spec_from_file_location("build_mobile", MODULE_PATH)
build_mobile = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(build_mobile)


class MobileExportTests(unittest.TestCase):
    def fixture(self):
        return {
            "classification_version": 4,
            "generated_at": "2026-07-16T10:00:00+00:00",
            "duplicates_collapsed": 2,
            "archive_count": 999,
            "saved": ["private-id"],
            "notes": {"private-id": "never publish this"},
            "articles": [
                {
                    "id": "article-1",
                    "title": "Blender workflow",
                    "url": "https://example.com/article",
                    "summary": "A public RSS excerpt",
                    "published_at": "2026-07-16T09:00:00+00:00",
                    "source": "Example",
                    "source_id": "example",
                    "lane": "Tech & Development",
                    "software_group": "Blender",
                    "software_tags": ["Blender"],
                    "topic_tags": ["Modeling & sculpting"],
                    "priority_score": 88,
                    "private_note": "do not copy",
                    "related": [{"source": "Other", "title": "Coverage", "url": "https://other.example", "secret": "no"}],
                    "sources": [{"id": "example", "name": "Example", "accent": "#fff", "feed": "https://example.com/private-feed"}],
                }
            ],
            "sources": [
                {
                    "id": "example",
                    "name": "Example",
                    "site": "https://example.com",
                    "feed": "https://example.com/feed.xml",
                    "accent": "#fff",
                    "ok": True,
                    "count": 1,
                    "etag": '"private-request-validator"',
                }
            ],
            "warnings": ["Example: connection detail that should stay private"],
        }

    def test_public_payload_uses_an_explicit_allowlist(self):
        result = build_mobile.sanitize_feed(self.fixture())
        serialized = str(result)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["unique_count"], 1)
        self.assertNotIn("saved", result)
        self.assertNotIn("archive_count", result)
        self.assertNotIn("never publish this", serialized)
        self.assertNotIn("private_note", serialized)
        self.assertNotIn("feed.xml", serialized)
        self.assertNotIn("private-request-validator", serialized)
        self.assertNotIn("connection detail", serialized)
        self.assertEqual(result["unavailable_sources"], ["Example"])

    def test_sanitizer_normalizes_null_nested_values_and_drops_bad_urls(self):
        payload = self.fixture()
        payload["articles"][0]["software_tags"] = None
        payload["articles"][0]["topic_tags"] = ["Blender", 7]
        payload["articles"][0]["related"] = [None, {"title": "missing URL"}]
        payload["articles"][0]["sources"] = ["wrong", {"id": "example", "name": "Example", "site": "javascript:bad"}]
        payload["sources"] = [None, {"id": "example", "name": "Example", "site": "file:///private"}]
        result = build_mobile.sanitize_feed(payload)
        article = result["articles"][0]
        self.assertEqual(article["software_tags"], [])
        self.assertEqual(article["topic_tags"], ["Blender"])
        self.assertEqual(article["related"], [])
        self.assertEqual(article["sources"], [])
        self.assertEqual(result["sources"], [])
        self.assertNotIn("javascript:", str(result))
        self.assertNotIn("file:", str(result))

    def test_sanitizer_and_renderers_do_not_emit_remote_thumbnail_urls(self):
        payload = self.fixture()
        payload["articles"][0]["image"] = "https://cdn.example.test/card.jpg"
        result = build_mobile.sanitize_feed(payload)
        self.assertEqual(result["articles"][0]["image"], "")
        desktop = (MODULE_PATH.parents[1] / "static" / "app.js").read_text(encoding="utf-8")
        mobile = (MODULE_PATH.parent / "site" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function safeImageUrl", desktop)
        self.assertIn("function safeImageUrl", mobile)

    def test_sanitizer_caps_collections_at_shared_validator_limits(self):
        payload = self.fixture()
        payload["warnings"] = []
        payload["articles"] = [
            {
                **payload["articles"][0],
                "id": f"article-{index}",
                "url": f"https://example.com/article-{index}",
            }
            for index in range(build_mobile.MAX_MOBILE_ARTICLES + 1)
        ]
        payload["sources"] = [
            {
                "id": f"source-{index}",
                "name": f"Source {index}",
                "site": f"https://source-{index}.example",
                "accent": "#fff",
                "ok": True,
                "count": index,
            }
            for index in range(build_mobile.MAX_MOBILE_SOURCES + 1)
        ]
        payload["unavailable_sources"] = [
            f"Unavailable {index}"
            for index in range(build_mobile.MAX_MOBILE_UNAVAILABLE_SOURCES + 1)
        ]

        result = build_mobile.sanitize_feed(payload)

        self.assertEqual(len(result["articles"]), build_mobile.MAX_MOBILE_ARTICLES)
        self.assertEqual(result["articles"][0]["id"], "article-0")
        self.assertEqual(result["articles"][-1]["id"], "article-1499")
        self.assertEqual(len(result["sources"]), build_mobile.MAX_MOBILE_SOURCES)
        self.assertEqual(result["sources"][0]["id"], "source-0")
        self.assertEqual(result["sources"][-1]["id"], "source-299")
        self.assertEqual(
            len(result["unavailable_sources"]),
            build_mobile.MAX_MOBILE_UNAVAILABLE_SOURCES,
        )
        self.assertEqual(result["unavailable_sources"][-1], "Unavailable 299")

        domain = (MODULE_PATH.parents[1] / "static" / "domain.mjs").read_text(encoding="utf-8")
        self.assertIn(f"MAX_FEED_ARTICLES = {build_mobile.MAX_MOBILE_ARTICLES}", domain)
        self.assertIn(f"MAX_FEED_SOURCES = {build_mobile.MAX_MOBILE_SOURCES}", domain)

    def test_build_copies_only_mobile_assets_and_sanitized_feed(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "site"
            build_mobile.build_site(output, self.fixture())
            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "feed.json").is_file())
            self.assertTrue((output / "domain.mjs").is_file())
            self.assertFalse((output / "user-state.json").exists())
            self.assertFalse((output / "cg-signal.db").exists())

    def test_mobile_bundle_copies_only_verified_referenced_thumbnails(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "http"
            validated = validate_thumbnail_bytes(b"\x89PNG\r\n\x1a\nmobile", "image/png")
            reference = store_thumbnail(cache / "thumbnails", validated)
            payload = self.fixture()
            payload["articles"][0]["image"] = reference
            output = Path(temporary) / "site"
            build_mobile.build_site(output, payload, thumbnail_root=cache / "thumbnails")
            emitted = json.loads((output / "feed.json").read_text(encoding="utf-8"))
            self.assertEqual(emitted["articles"][0]["image"], reference)
            self.assertEqual((output / reference).read_bytes(), validated.body)

            (cache / "thumbnails" / Path(reference).name).unlink()
            build_mobile.build_site(output, payload, thumbnail_root=cache / "thumbnails")
            emitted = json.loads((output / "feed.json").read_text(encoding="utf-8"))
            self.assertEqual(emitted["articles"][0]["image"], "")
            self.assertFalse((output / reference).exists())

    def test_mobile_bundle_keeps_referenced_thumbnails_beyond_legacy_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "http"
            output = Path(temporary) / "site"
            thumbnail_root = cache / "thumbnails"
            thumbnail_root.mkdir(parents=True)
            payload = self.fixture()
            articles = []
            for index in range(501):
                body = b"\x89PNG\r\n\x1a\n" + index.to_bytes(4, "big")
                validated = validate_thumbnail_bytes(body, "image/png")
                reference = validated.reference
                (thumbnail_root / Path(reference).name).write_bytes(validated.body)
                articles.append({
                    **payload["articles"][0],
                    "id": f"article-{index}",
                    "url": f"https://example.com/article-{index}",
                    "image": reference,
                })
            payload["articles"] = articles

            build_mobile.build_site(output, payload, thumbnail_root=thumbnail_root)

            emitted = json.loads((output / "feed.json").read_text(encoding="utf-8"))
            self.assertEqual(len(emitted["articles"]), 501)
            self.assertEqual(sum(bool(article["image"]) for article in emitted["articles"]), 501)
            self.assertEqual(len(list((output / "thumbnails").iterdir())), 501)

    def test_mobile_bundle_spends_byte_budget_on_newer_articles_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "http"
            output = Path(temporary) / "site"
            thumbnail_root = cache / "thumbnails"
            thumbnail_root.mkdir(parents=True)
            payload = self.fixture()
            old = validate_thumbnail_bytes(b"\x89PNG\r\n\x1a\nold", "image/png")
            new = validate_thumbnail_bytes(b"\x89PNG\r\n\x1a\nnew", "image/png")
            assert old and new
            (thumbnail_root / Path(old.reference).name).write_bytes(old.body)
            (thumbnail_root / Path(new.reference).name).write_bytes(new.body)
            payload["articles"] = [
                {
                    **payload["articles"][0],
                    "id": "older",
                    "url": "https://example.com/older",
                    "published_at": "2026-07-16T08:00:00+00:00",
                    "image": old.reference,
                },
                {
                    **payload["articles"][0],
                    "id": "newer",
                    "url": "https://example.com/newer",
                    "published_at": "2026-07-16T10:00:00+00:00",
                    "image": new.reference,
                },
            ]

            with mock.patch.object(build_mobile, "MAX_MOBILE_THUMBNAIL_BYTES", len(new.body)):
                build_mobile.build_site(output, payload, thumbnail_root=thumbnail_root)

            emitted = json.loads((output / "feed.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [article["image"] for article in emitted["articles"]],
                ["", new.reference],
            )
            self.assertFalse((output / old.reference).exists())
            self.assertTrue((output / new.reference).is_file())

    def test_mobile_bundle_rejects_symlinked_thumbnail_root_without_touching_sentinel(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            anchor = base / "http"
            anchor.mkdir()
            external = base / "external"
            external.mkdir()
            sentinel = external / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            root = anchor / "thumbnails"
            try:
                root.symlink_to(external, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable")
            with self.assertRaises(ValueError):
                build_mobile.bundle_thumbnails(
                    self.fixture(),
                    base / "output",
                    root,
                    thumbnail_anchor=anchor,
                )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertFalse((base / "output").exists())

    def test_gather_and_build_keep_persistent_thumbnail_anchor(self):
        from cg_signal.feeds import FeedService

        with tempfile.TemporaryDirectory() as temporary:
            request_cache = Path(temporary) / "persistent-http"
            output = Path(temporary) / "site"
            payload = self.fixture()
            body = b"\x89PNG\r\n\x1a\nfrom-gather"

            def fake_build_feed(service, *, force=False):
                validated = validate_thumbnail_bytes(body, "image/png")
                reference = store_thumbnail(
                    service.paths.thumbnail_dir,
                    validated,
                    expected_anchor=service.paths.thumbnail_anchor,
                )
                result = dict(payload)
                result["articles"] = [dict(payload["articles"][0], image=reference)]
                return result

            with mock.patch.object(FeedService, "build_feed", fake_build_feed):
                gathered = build_mobile.gather_feed(request_cache)

            reference = gathered["articles"][0]["image"]
            self.assertTrue((request_cache / "thumbnails" / Path(reference).name).is_file())
            build_mobile.build_site(
                output,
                gathered,
                thumbnail_root=request_cache / "thumbnails",
                thumbnail_anchor=request_cache,
            )
            emitted = json.loads((output / "feed.json").read_text(encoding="utf-8"))
            self.assertEqual(emitted["articles"][0]["image"], reference)
            self.assertEqual((output / reference).read_bytes(), body)

    def test_gather_feed_refuses_to_publish_timed_out_thumbnail_refresh(self):
        payload = self.fixture()
        payload["thumbnails_refreshing"] = True
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("cg_signal.feeds.FeedService.build_feed", return_value=payload),
            mock.patch("cg_signal.feeds.FeedService.wait_for_thumbnail_refresh", return_value=False) as wait,
            mock.patch("cg_signal.feeds.FeedService.read_cache", return_value=payload),
        ):
            with self.assertRaisesRegex(RuntimeError, "refusing to publish an incomplete feed"):
                build_mobile.gather_feed(Path(temporary) / "http")
        wait.assert_called_once_with(timeout_seconds=build_mobile.MOBILE_THUMBNAIL_WAIT_SECONDS)

    def test_gather_feed_refuses_cache_still_marked_as_refreshing(self):
        payload = self.fixture()
        payload["thumbnails_refreshing"] = True
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("cg_signal.feeds.FeedService.build_feed", return_value=payload),
            mock.patch("cg_signal.feeds.FeedService.wait_for_thumbnail_refresh", return_value=True),
            mock.patch("cg_signal.feeds.FeedService.read_cache", return_value=payload),
        ):
            with self.assertRaisesRegex(RuntimeError, "refusing to publish an incomplete feed"):
                build_mobile.gather_feed(Path(temporary) / "http")

    def test_gather_feed_returns_completed_thumbnail_cache(self):
        payload = self.fixture()
        payload["thumbnails_refreshing"] = True
        refreshed = {**payload, "thumbnails_refreshing": False, "generated_at": "completed"}
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("cg_signal.feeds.FeedService.build_feed", return_value=payload),
            mock.patch("cg_signal.feeds.FeedService.wait_for_thumbnail_refresh", return_value=True),
            mock.patch("cg_signal.feeds.FeedService.read_cache", return_value=refreshed),
        ):
            self.assertEqual(
                build_mobile.gather_feed(Path(temporary) / "http"),
                refreshed,
            )

    def test_history_merge_keeps_current_sources_before_historical_sources(self):
        current = self.fixture()
        current["sources"] = [
            {"id": f"current-{index}", "name": f"Current {index}", "site": "https://example.com"}
            for index in range(build_mobile.MAX_MOBILE_SOURCES)
        ]
        previous = self.fixture()
        previous["sources"] = [
            {"id": f"history-{index}", "name": f"History {index}", "site": "https://example.com"}
            for index in range(build_mobile.MAX_MOBILE_SOURCES)
        ]
        merged = build_mobile.merge_feed_history(current, previous)
        self.assertEqual(len(merged["sources"]), build_mobile.MAX_MOBILE_SOURCES)
        self.assertTrue(all(source["id"].startswith("current-") for source in merged["sources"]))

    def test_scheduled_build_restores_only_the_public_request_cache(self):
        project_root = MODULE_PATH.parents[1]
        workflow = (
            project_root / ".github" / "workflows" / "mobile-pages.yml"
        ).read_text(encoding="utf-8")
        ignore = (project_root / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("pull_request:", workflow)
        self.assertIn("needs: test", workflow)
        self.assertIn(
            "if: ${{ !cancelled() && github.event_name != 'pull_request' && needs.build.result == 'success' }}",
            workflow,
        )
        self.assertIn("format('cg-signal-pr-{0}', github.event.pull_request.number)", workflow)
        self.assertIn("|| 'cg-signal-mobile-pages'", workflow)
        self.assertIn("uses: actions/cache@caa296126883cff596d87d8935842f9db880ef25", workflow)
        self.assertIn("path: .mobile-cache", workflow)
        self.assertIn("--request-cache-dir .mobile-cache/http", workflow)
        self.assertIn("--previous-json .mobile-cache/history/feed.json", workflow)
        self.assertIn("discard_history", workflow)
        self.assertIn(".mobile-cache/http/image-index.json", workflow)
        self.assertIn(".mobile-cache/http/thumbnails", workflow)
        self.assertIn("permissions: {}", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn("configure-pages", workflow)
        self.assertNotIn("curl ", workflow)
        self.assertIn(".mobile-cache/", ignore)

    def test_previous_public_feed_fills_transient_gaps_with_bounded_history(self):
        current = self.fixture()
        previous = {
            "classification_version": 4,
            "generated_at": "2026-07-15T10:00:00+00:00",
            "articles": [
                {
                    "id": "article-2",
                    "title": "A retained Houdini story",
                    "url": "https://example.com/retained",
                    "published_at": "2026-07-10T09:00:00+00:00",
                    "source": "Example",
                    "source_id": "example",
                    "private_note": "must still be removed",
                },
                {
                    "id": "duplicate-url",
                    "title": "Duplicate of the current story",
                    "url": "https://example.com/article",
                    "published_at": "2026-07-15T09:00:00+00:00",
                    "source": "Example",
                    "source_id": "example",
                },
                {
                    "id": "expired",
                    "title": "Outside the rolling history",
                    "url": "https://example.com/expired",
                    "published_at": "2025-01-01T09:00:00+00:00",
                    "source": "Example",
                    "source_id": "example",
                },
            ],
            "sources": current["sources"],
        }

        result = build_mobile.merge_feed_history(
            current,
            previous,
            now=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )

        self.assertEqual(
            [article["id"] for article in result["articles"]],
            ["article-1", "article-2"],
        )
        self.assertEqual(result["carried_forward_count"], 1)
        self.assertEqual(result["sources"][0]["count"], 2)
        self.assertNotIn("private_note", str(result))

    def test_previous_classification_version_is_not_carried_forward(self):
        current = self.fixture()
        previous = {
            "classification_version": 1,
            "generated_at": "2026-07-15T10:00:00+00:00",
            "articles": [
                {
                    "id": "stale-label",
                    "title": "Limited-time gacha event",
                    "url": "https://example.com/stale",
                    "published_at": "2026-07-15T09:00:00+00:00",
                    "source": "Example",
                    "source_id": "example",
                    "lane": "Tech & Development",
                }
            ],
            "sources": current["sources"],
        }

        result = build_mobile.merge_feed_history(
            current,
            previous,
            now=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )

        self.assertEqual([article["id"] for article in result["articles"]], ["article-1"])
        self.assertEqual(result["classification_version"], 4)
        self.assertEqual(result["carried_forward_count"], 0)

    def test_mobile_offline_cache_requires_current_feed_schema(self):
        javascript = (MODULE_PATH.parent / "site" / "app.js").read_text(encoding="utf-8")
        self.assertIn("FEED_SCHEMA_VERSION", javascript)
        self.assertIn("payload?.feed_schema_version === FEED_SCHEMA_VERSION", javascript)
        self.assertIn("function feedPayloadIsCompatible(payload)", javascript)
        self.assertIn("feedPayloadIsStructurallyCompatible(payload)", javascript)
        self.assertNotIn("CLASSIFICATION_VERSION", javascript)

    def test_mobile_shell_revision_is_bumped_with_lane_schema(self):
        site = MODULE_PATH.parent / "site"
        html = (site / "index.html").read_text(encoding="utf-8")
        javascript = (site / "app.js").read_text(encoding="utf-8")
        worker = (site / "sw.js").read_text(encoding="utf-8")
        self.assertIn('from "./domain.mjs"', javascript)
        self.assertIn('type="module"', html)
        self.assertIn("app.js?v=20260812-v24", html)
        self.assertIn("styles.css?v=20260812-v24", html)
        self.assertIn('register("./sw.js?v=20260812-v24")', javascript)
        self.assertIn('const CACHE_NAME = "cg-signal-mobile-v24"', worker)
        self.assertIn("app.js?v=20260812-v24", worker)
        self.assertIn("./domain.mjs", worker)
        self.assertIn('fetch("./feed.json", { cache: "no-store" })', javascript)
        self.assertNotIn("reload(", javascript)
        self.assertIn(".then(async (response) =>", worker)
        self.assertIn("const cache = await caches.open(CACHE_NAME);", worker)
        self.assertIn("await cache.put(event.request, response.clone());", worker)
        self.assertIn(".catch(() => caches.match(event.request))", worker)

    def test_mobile_shell_keeps_inline_controls_reachable(self):
        site = MODULE_PATH.parent / "site"
        html = (site / "index.html").read_text(encoding="utf-8")
        javascript = (site / "app.js").read_text(encoding="utf-8")
        styles = (site / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn('id="explore-button"', html)
        self.assertNotIn('id="explore-panel"', html)
        self.assertEqual(html.count("data-search-input"), 1)
        self.assertEqual(html.count("data-source-select"), 0)
        self.assertEqual(html.count("data-category-list"), 1)
        self.assertIn('id="source-list"', html)
        self.assertIn('class="search-box header-search"', html)
        self.assertNotIn('<select id="source-select"', html)
        self.assertIn('data-source-option', javascript)
        self.assertIn("function syncControlValues()", javascript)
        self.assertNotIn("function openExplore", javascript)
        self.assertIn("function renderSourceButtons()", javascript)
        self.assertIn(".source-button", styles)
        self.assertIn(".header-search", styles)
        self.assertIn('id="scroll-top-button"', html)
        self.assertIn('id="density-toggle"', html)
        self.assertIn('id="filter-drawer-handle"', html)
        self.assertIn('id="filter-drawer-content"', html)
        self.assertIn("scrollIntoView({ behavior: \"auto\", block: \"center\" })", javascript)
        self.assertIn("story-card:not(.skeleton)", javascript)
        self.assertNotIn('window.scrollTo({ top: 0, behavior: "smooth"', javascript)
        self.assertIn("setFilterDrawerExpanded", javascript)
        self.assertIn('cg-signal-mobile:density', javascript)
        self.assertIn('storyList.classList.toggle("is-compact"', javascript)
        self.assertIn('document.addEventListener("pointerdown"', javascript)
        self.assertIn('elements.filterDrawer.contains(event.target)', javascript)
        self.assertIn("pointerdown", javascript)
        self.assertIn('class="app-header-row"', html)
        self.assertIn("position: sticky", styles)
        self.assertIn(".filter-drawer { padding: 5px 0; }", styles)
        self.assertIn(".filter-drawer.is-collapsed", styles)
        self.assertIn(".app-header .filter-drawer-handle", styles)
        self.assertIn("rgba(255,255,255,.1)", styles)
        self.assertIn(".scroll-top-button", styles)
        self.assertIn(".story-list.is-compact", styles)
        self.assertIn("grid-template-columns: 102px minmax(0, 1fr)", styles)
        self.assertIn("grid-template-columns: repeat(4, 1fr)", styles)
        self.assertIn("margin: 4px 0 0", styles)
        self.assertNotIn('class="hero"', html)
        self.assertNotIn('id="story-total"', html)
        self.assertNotIn('id="repeat-total"', html)
        self.assertNotIn('id="recent-total"', html)
        self.assertNotIn("Today’s board", html)
        self.assertNotIn("Today's board", html)
        self.assertNotIn("Stay current", html)
        self.assertNotIn("Keep moving", html)
        self.assertIn('class="update-row feed-update-row"', html)
        self.assertNotIn("storyTotal", javascript)
        self.assertNotIn("cg-signal-mobile:visited", javascript)

    def test_mobile_source_management_is_device_local(self):
        site = MODULE_PATH.parent / "site"
        html = (site / "index.html").read_text(encoding="utf-8")
        javascript = (site / "app.js").read_text(encoding="utf-8")
        styles = (site / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="source-manager-panel"', html)
        self.assertIn('class="source-manage-button"', html)
        self.assertNotIn('id="result-count"', html)
        self.assertIn('id="enable-all-sources"', html)
        self.assertNotIn("Add RSS", html)
        self.assertIn('disabledSources: "cg-signal-mobile:disabled-sources"', javascript)
        self.assertIn("function persistDisabledSources()", javascript)
        self.assertIn("function articleHasEnabledSource(article)", javascript)
        self.assertNotIn("if (article.source_id) return !state.disabledSources", javascript)
        self.assertIn("function renderSourceManager()", javascript)
        self.assertNotIn("resultCount", javascript)
        self.assertIn("localStorage.removeItem(storageKeys.disabledSources)", javascript)
        self.assertIn(".source-manager-panel", styles)
        self.assertIn(".source-manage-button", styles)
        self.assertIn(".source-manager-item.is-enabled", styles)

    def test_mobile_feed_has_pins_without_brief_or_read_state(self):
        site = MODULE_PATH.parent / "site"
        html = (site / "index.html").read_text(encoding="utf-8")
        javascript = (site / "app.js").read_text(encoding="utf-8")
        styles = (site / "styles.css").read_text(encoding="utf-8")
        self.assertIn('data-view="pinned"', html)
        self.assertIn('id="pinned-total"', html)
        self.assertNotIn('id="brief-panel"', html)
        self.assertNotIn('id="briefing-listen"', html)
        self.assertNotIn('id="unread-count"', html)
        self.assertIn('pinned: "cg-signal-mobile:pinned"', javascript)
        self.assertNotIn("currentIds.has(id)", javascript)
        self.assertNotIn("state.read", javascript)
        self.assertNotIn("data-read-id", javascript)
        self.assertNotIn("brief", javascript.lower())
        self.assertNotIn("brief", styles.lower())
        self.assertIn("data-pin-id", javascript)

    def test_mobile_shell_has_a_recent_publication_window(self):
        site = MODULE_PATH.parent / "site"
        html = (site / "index.html").read_text(encoding="utf-8")
        javascript = (site / "app.js").read_text(encoding="utf-8")
        styles = (site / "styles.css").read_text(encoding="utf-8")
        service_worker = (site / "sw.js").read_text(encoding="utf-8")
        self.assertIn('data-time-window="month"', html)
        self.assertIn('data-time-window="quarter"', html)
        self.assertIn('data-time-window="all"', html)
        self.assertIn('timeWindow: "cg-signal-mobile:time-window"', javascript)
        self.assertIn("function articleWithinTimeWindow(article)", javascript)
        self.assertIn("function storyListMarkup(articles)", javascript)
        self.assertIn("function readCachedFeed()", javascript)
        self.assertIn("applyFeed(cached)", javascript)
        self.assertIn("cg-signal-mobile-v24", service_worker)
        self.assertIn("styles.css?v=20260812-v24", service_worker)
        self.assertIn("app.js?v=20260812-v24", service_worker)
        self.assertIn("./domain.mjs", service_worker)
        self.assertIn("sw.js?v=20260812-v24", javascript)
        self.assertIn("sw.js?v=20260812-v24", service_worker)
        self.assertIn("fetch(event.request)", service_worker)
        self.assertIn("return articles.map(storyMarkup).join", javascript)
        self.assertNotIn("function articleRecencyBucket(article)", javascript)
        self.assertNotIn("Last 24 hours", javascript)
        self.assertNotIn("Last 3 days", javascript)
        self.assertNotIn(".recency-section", styles)

    def test_mobile_bottom_navigation_uses_latest_and_pinned(self):
        html = (MODULE_PATH.parent / "site" / "index.html").read_text(encoding="utf-8")
        javascript = (MODULE_PATH.parent / "site" / "app.js").read_text(encoding="utf-8")
        styles = (MODULE_PATH.parent / "site" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".bottom-nav", styles)
        self.assertIn('data-view="latest"', html)
        self.assertIn('data-view="pinned"', html)
        self.assertIn('view: "latest"', javascript)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", styles)


if __name__ == "__main__":
    unittest.main()
