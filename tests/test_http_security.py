import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from cg_signal.config import RuntimePaths
from cg_signal.http import DashboardHandler, DashboardServer
from cg_signal.thumbnails import store_thumbnail, validate_thumbnail_bytes


class DashboardSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        paths = RuntimePaths.for_root(Path.cwd()).with_cache_dir(Path(self.temporary.name) / "cache")
        self.server = DashboardServer(("127.0.0.1", 0), DashboardHandler, paths=paths)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.temporary.cleanup()

    def request(self, path, *, headers=None, data=None, method=None):
        request = urllib.request.Request(
            f"{self.base}{path}", headers=headers or {}, data=data, method=method,
        )
        try:
            return urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            return error

    def token(self):
        response = self.request("/")
        body = response.read().decode("utf-8")
        match = re.search(r'<meta name="cg-signal-api-token" content="([^"]+)"', body)
        self.assertIsNotNone(match)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(response.headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertNotIn(self.server.api_token, response.geturl())
        return match.group(1)

    def test_bootstrap_health_and_api_token_boundary(self):
        token = self.token()
        self.assertEqual(token, self.server.api_token)
        health = self.request("/api/health")
        self.assertEqual(health.status, 200)
        self.assertEqual(self.request("/api/sources").status, 403)
        self.assertEqual(self.request("/api/sources", headers={"X-CG-Signal-Token": "wrong"}).status, 403)
        self.assertEqual(
            self.request("/api/sources", headers={"X-CG-Signal-Token": token}).status,
            200,
        )

    def test_host_origin_and_fetch_metadata_are_gated(self):
        token = self.token()
        port = self.server.server_address[1]
        self.assertEqual(
            self.request("/", headers={"Sec-Fetch-Site": "none"}).status,
            200,
        )
        self.assertEqual(
            self.request(
                "/api/sources",
                headers={"X-CG-Signal-Token": token, "Sec-Fetch-Site": "none"},
            ).status,
            403,
        )
        self.assertEqual(
            self.request("/api/health", headers={"Host": f"127.0.0.1:{port + 1}"}).status,
            421,
        )
        self.assertEqual(
            self.request(
                "/api/sources",
                headers={"X-CG-Signal-Token": token, "Origin": "http://evil.test"},
            ).status,
            403,
        )
        self.assertEqual(
            self.request(
                "/api/sources",
                headers={"X-CG-Signal-Token": token, "Referer": "http://evil.test/return"},
            ).status,
            403,
        )
        self.assertEqual(
            self.request(
                "/api/sources",
                headers={"X-CG-Signal-Token": token, "Sec-Fetch-Site": "cross-site"},
            ).status,
            403,
        )

    def test_mutation_requires_json_and_same_origin_when_present(self):
        token = self.token()
        base_headers = {"X-CG-Signal-Token": token}
        self.assertEqual(
            self.request(
                "/api/state", headers={**base_headers, "Content-Type": "text/plain"},
                data=b"{}", method="POST",
            ).status,
            415,
        )
        self.assertEqual(
            self.request(
                "/api/state",
                headers={**base_headers, "Content-Type": "application/json", "Origin": "http://evil.test"},
                data=b"{}", method="POST",
            ).status,
            403,
        )
        port = self.server.server_address[1]
        response = self.request(
            "/api/state",
            headers={
                **base_headers,
                "Content-Type": "application/json; charset=utf-8",
                "Origin": f"http://127.0.0.1:{port}",
            },
            data=json.dumps({}).encode(), method="POST",
        )
        self.assertEqual(response.status, 200)

    def test_get_refresh_is_passive_and_post_refresh_is_forced(self):
        token = self.token()
        with mock.patch.object(self.server.service, "feed_for_request", return_value={"articles": []}) as feed:
            self.assertEqual(
                self.request("/api/feed?refresh=1", headers={"X-CG-Signal-Token": token}).status,
                200,
            )
            self.assertEqual(feed.call_args.kwargs["force"], False)
            self.assertEqual(
                self.request(
                    "/api/feed/refresh",
                    headers={"X-CG-Signal-Token": token, "Content-Type": "application/json"},
                    data=b"{}", method="POST",
                ).status,
                200,
            )
            self.assertEqual(feed.call_args.kwargs["force"], True)

    def test_history_route_replaces_archive_route(self):
        token = self.token()
        with mock.patch.object(
            self.server.service.repository,
            "query_history",
            return_value={"articles": [], "total": 0, "history_count": 0},
        ) as query:
            response = self.request("/api/history", headers={"X-CG-Signal-Token": token})
        self.assertEqual(response.status, 200)
        query.assert_called_once()
        self.assertEqual(
            self.request("/api/archive", headers={"X-CG-Signal-Token": token}).status,
            404,
        )

    def test_token_rotates_between_server_instances(self):
        old_token = self.token()
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        paths = RuntimePaths.for_root(Path.cwd()).with_cache_dir(Path(self.temporary.name) / "cache-2")
        self.server = DashboardServer(("127.0.0.1", 0), DashboardHandler, paths=paths)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        new_token = self.token()
        self.assertNotEqual(old_token, new_token)

    def test_canonical_thumbnail_route_is_host_gated_and_token_free(self):
        body = b"\xff\xd8\xff" + b"thumbnail"
        validated = validate_thumbnail_bytes(body, "image/jpeg")
        reference = store_thumbnail(self.server.paths.thumbnail_dir, validated)
        response = self.request(f"/{reference}")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), body)
        self.assertEqual(response.headers.get("Content-Type"), "image/jpeg")
        self.assertEqual(response.headers.get("Cache-Control"), "public, max-age=2592000, immutable")
        self.assertEqual(self.request(f"/{reference}?x=1").status, 404)
        self.assertEqual(self.request(f"/thumbnails/%2e%2e/{reference.split('/', 1)[1]}").status, 404)
        port = self.server.server_address[1]
        self.assertEqual(
            self.request(f"/{reference}", headers={"Host": f"127.0.0.1:{port + 1}"}).status,
            421,
        )

    def test_thumbnail_route_rejects_symlinked_cache_root(self):
        cache_dir = self.server.paths.cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        external = Path(self.temporary.name) / "external-thumbnails"
        external.mkdir()
        sentinel = external / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        try:
            self.server.paths.thumbnail_dir.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are unavailable")
        response = self.request(
            "/thumbnails/" + "0" * 64 + ".jpg",
        )
        self.assertEqual(response.status, 404)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
