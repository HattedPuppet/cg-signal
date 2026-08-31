"""Serve deterministic local fixtures for the Chromium smoke tests."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import functools
import importlib.util
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
from typing import Any
import urllib.parse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cg_signal.classification import apply_article_classification  # noqa: E402
from cg_signal.config import (  # noqa: E402
    CLASSIFICATION_REVISION,
    FEED_SCHEMA_VERSION,
    RuntimePaths,
)
from cg_signal.http import DashboardHandler, DashboardServer  # noqa: E402
from cg_signal.thumbnails import store_thumbnail, validate_thumbnail_bytes  # noqa: E402


def _load_mobile_builder() -> Any:
    module_path = PROJECT_ROOT / "mobile" / "build_mobile.py"
    spec = importlib.util.spec_from_file_location("cg_signal_smoke_build_mobile", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load mobile builder from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QuietDashboardHandler(DashboardHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        parameters = urllib.parse.parse_qs(parsed.query)
        if parsed.path in {"", "/", "/index.html"} and parameters.get("state_fault") == ["1"]:
            if not getattr(self.dashboard, "_fault_state_seeded", False):
                self.dashboard.service.repository.write_state(
                    {
                        "saved": ["smoke-blender-article"],
                        "muted_sources": ["smoke-unreal-source"],
                    }
                )
                self.dashboard._fault_state_seeded = True
        if parsed.path in {"", "/", "/index.html"} and parameters.get("state_control") == ["1"]:
            if not getattr(self.dashboard, "_control_state_seeded", False):
                self.dashboard.service.repository.write_state(
                    {"saved": ["smoke-blender-article"], "muted_sources": []}
                )
                self.dashboard._control_state_seeded = True
        if parsed.path in {"", "/", "/index.html"} and parameters.get("state_failure") == ["1"]:
            if not getattr(self.dashboard, "_failure_state_seeded", False):
                self.dashboard.service.repository.write_state(
                    {"saved": ["smoke-blender-article"], "muted_sources": []}
                )
                self.dashboard._failure_state_seeded = True
        super().do_GET()

    def log_message(self, format_string: str, *args: Any) -> None:  # noqa: ARG002
        return


class QuietStaticHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".mjs": "application/javascript",
    }

    def log_message(self, format_string: str, *args: Any) -> None:  # noqa: ARG002
        return


def fixture_payload() -> dict[str, Any]:
    """Return two current-month articles with all render-required fields."""

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    newest = max(month_start, now - timedelta(minutes=5))
    older = max(month_start, newest - timedelta(minutes=1))
    sources = [
        {
            "id": "smoke-unreal-source",
            "name": "Smoke Unreal Source",
            "site": "https://example.test/unreal",
            "feed": "https://example.test/unreal/feed.xml",
            "accent": "#4b75ff",
            "ok": True,
            "count": 1,
        },
        {
            "id": "smoke-blender-source",
            "name": "Smoke Blender Source",
            "site": "https://example.test/blender",
            "feed": "https://example.test/blender/feed.xml",
            "accent": "#f18a21",
            "ok": True,
            "count": 1,
        },
    ]
    articles = [
        {
            "id": "smoke-unreal-article",
            "title": "Unreal Engine Rendering Techniques",
            "url": "https://example.test/articles/unreal-rendering",
            "summary": "A focused Unreal Engine rendering workflow for real-time scenes.",
            "image": "",
            "published_at": newest.isoformat(),
            "source": "Smoke Unreal Source",
            "source_id": "smoke-unreal-source",
            "source_site": "https://example.test/unreal",
            "accent": "#4b75ff",
            "source_count": 1,
            "cluster_size": 1,
            "sources": [
                {
                    "id": "smoke-unreal-source",
                    "name": "Smoke Unreal Source",
                    "site": "https://example.test/unreal",
                    "accent": "#4b75ff",
                },
            ],
            "related": [],
            "software_tags": [],
            "topic_tags": [],
            "priority_reasons": [],
        },
        {
            "id": "smoke-blender-article",
            "title": "Blender Lighting Workflow",
            "url": "https://example.test/articles/blender-lighting",
            "summary": "A practical Blender lighting and material workflow for production scenes.",
            "image": "",
            "published_at": older.isoformat(),
            "source": "Smoke Blender Source",
            "source_id": "smoke-blender-source",
            "source_site": "https://example.test/blender",
            "accent": "#f18a21",
            "source_count": 1,
            "cluster_size": 1,
            "sources": [
                {
                    "id": "smoke-blender-source",
                    "name": "Smoke Blender Source",
                    "site": "https://example.test/blender",
                    "accent": "#f18a21",
                },
            ],
            "related": [],
            "software_tags": [],
            "topic_tags": [],
            "priority_reasons": [],
        },
    ]
    for article in articles:
        apply_article_classification(article)
    return {
        "feed_schema_version": FEED_SCHEMA_VERSION,
        "classification_revision": CLASSIFICATION_REVISION,
        "classification_version": CLASSIFICATION_REVISION,
        "schema_version": FEED_SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "cached": True,
        "stale": False,
        "refreshing": False,
        "raw_count": len(articles),
        "unique_count": len(articles),
        "duplicates_collapsed": 0,
        "articles": articles,
        "sources": sources,
        "warnings": [],
        "history_count": len(articles),
        "thumbnails_refreshing": False,
    }


def _serve_mobile(output: Path) -> ThreadingHTTPServer:
    handler = functools.partial(QuietStaticHandler, directory=str(output))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    temporary = tempfile.TemporaryDirectory(prefix="cg-signal-browser-")
    dashboard: DashboardServer | None = None
    mobile: ThreadingHTTPServer | None = None
    threads: list[threading.Thread] = []
    try:
        root = Path(temporary.name)
        dashboard_cache = root / "dashboard-cache"
        paths = RuntimePaths.for_root(PROJECT_ROOT).with_cache_dir(dashboard_cache)
        payload = fixture_payload()

        dashboard = DashboardServer(("127.0.0.1", 0), QuietDashboardHandler, paths=paths)
        dashboard.service.write_cache(payload)
        dashboard.service.repository.record_articles(payload["articles"])
        dashboard_thread = threading.Thread(
            target=dashboard.serve_forever,
            name="cg-signal-smoke-dashboard",
            daemon=True,
        )
        dashboard_thread.start()
        threads.append(dashboard_thread)

        mobile_output = root / "mobile-site"
        mobile_cache = root / "mobile-cache"
        thumbnail = validate_thumbnail_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
            "image/png",
        )
        reference = store_thumbnail(
            mobile_cache / "thumbnails",
            thumbnail,
            expected_anchor=mobile_cache,
        )
        mobile_payload = json.loads(json.dumps(payload))
        mobile_payload["articles"][0]["image"] = reference
        mobile_builder = _load_mobile_builder()
        mobile_builder.build_site(
            mobile_output,
            mobile_payload,
            thumbnail_root=mobile_cache / "thumbnails",
            thumbnail_anchor=mobile_cache,
        )
        mobile = _serve_mobile(mobile_output)
        mobile_thread = threading.Thread(
            target=mobile.serve_forever,
            name="cg-signal-smoke-mobile",
            daemon=True,
        )
        mobile_thread.start()
        threads.append(mobile_thread)

        dashboard_port = dashboard.server_address[1]
        mobile_port = mobile.server_address[1]
        print(
            json.dumps(
                {
                    "desktop_url": f"http://127.0.0.1:{dashboard_port}/",
                    "mobile_url": f"http://127.0.0.1:{mobile_port}/",
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        sys.stdin.readline()
    finally:
        for server in (mobile, dashboard):
            if server is None:
                continue
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
        temporary.cleanup()


if __name__ == "__main__":
    main()
