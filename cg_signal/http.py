"""HTTP handler and desktop entrypoint for CG Signal."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import mimetypes
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import threading
import urllib.parse
import webbrowser
from typing import Any

from .config import (
    CLASSIFICATION_REVISION,
    FEED_SCHEMA_VERSION,
    RuntimePaths,
    source_revision,
)
from .feeds import FeedService


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "CGSignal/1.0"

    @property
    def dashboard(self) -> "DashboardServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, format_string: str, *args: Any) -> None:
        message = format_string % args
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self, maximum_size: int = 50_000) -> Any:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid content length.") from exc
        if content_length <= 0 or content_length > maximum_size:
            raise ValueError("Invalid request payload size.")
        try:
            return json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid JSON request payload.") from exc

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        service = self.dashboard.service
        repository = service.repository
        if parsed.path == "/api/health":
            self.send_json(
                {
                    "ok": True,
                    "service": "CG Signal",
                    "source_revision": self.dashboard.source_revision,
                    "pid": os.getpid(),
                    "feed_schema_version": FEED_SCHEMA_VERSION,
                    "classification_revision": CLASSIFICATION_REVISION,
                    # Kept in the public health response for older launchers.
                    "classification_version": CLASSIFICATION_REVISION,
                }
            )
            return
        if parsed.path == "/api/feed":
            parameters = urllib.parse.parse_qs(parsed.query)
            try:
                self.send_json(
                    service.feed_for_request(
                        force=parameters.get("refresh", ["0"])[0] == "1",
                        wait_for_refresh=parameters.get("wait", ["0"])[0] == "1",
                        wait_for_thumbnails=parameters.get("wait_thumbnails", ["0"])[0] == "1",
                    )
                )
            except Exception as exc:
                self.send_json({"error": "Unable to gather feeds", "detail": str(exc)}, status=500)
            return
        if parsed.path == "/api/state":
            self.send_json(repository.read_state())
            return
        if parsed.path == "/api/archive":
            parameters = urllib.parse.parse_qs(parsed.query)
            try:
                source_ids = [
                    source_id
                    for value in parameters.get("sources", [])
                    for source_id in value.split(",")
                    if source_id
                ]
                self.send_json(
                    repository.query_archive(
                        query=parameters.get("q", [""])[0],
                        lane=parameters.get("lane", ["All"])[0],
                        source_ids=source_ids,
                        limit=int(parameters.get("limit", ["60"])[0]),
                        offset=int(parameters.get("offset", ["0"])[0]),
                        new_after=parameters.get("new_after", [""])[0],
                    )
                )
            except (ValueError, sqlite3.Error) as exc:
                self.send_json({"error": "Unable to search archive", "detail": str(exc)}, status=400)
            return
        if parsed.path == "/api/sources":
            self.send_json({"sources": repository.list_source_configs()})
            return

        relative_path = "index.html" if parsed.path in {"", "/"} else parsed.path.lstrip("/")
        candidate = (self.dashboard.paths.static_dir / relative_path).resolve()
        try:
            candidate.relative_to(self.dashboard.paths.static_dir.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not candidate.is_file():
            self.send_error(404)
            return
        body = candidate.read_bytes()
        content_type, _ = mimetypes.guess_type(candidate.name)
        if content_type and content_type.startswith("text/"):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        supported_paths = {"/api/state", "/api/sources", "/api/sources/test", "/api/sources/toggle"}
        if parsed.path not in supported_paths:
            self.send_error(404)
            return
        service = self.dashboard.service
        repository = service.repository
        try:
            maximum_size = 750_000 if parsed.path == "/api/state" else 50_000
            payload = self.read_json_body(maximum_size)
            if parsed.path == "/api/state":
                self.send_json(repository.write_state(payload))
            elif parsed.path == "/api/sources":
                source = repository.add_source_config(payload)
                service.invalidate_feed_cache()
                self.send_json({"source": source}, status=201)
            elif parsed.path == "/api/sources/test":
                self.send_json(service.test_source(payload))
            else:
                if not isinstance(payload, dict):
                    raise ValueError("Source toggle details must be an object.")
                source = repository.set_source_enabled(payload.get("id"), payload.get("enabled"))
                service.invalidate_feed_cache()
                self.send_json({"source": source})
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self.send_json({"error": "Unable to update local data", "detail": str(exc)}, status=500)


class DashboardServer(ThreadingHTTPServer):
    """Use an exclusive listener so repeated launches cannot start duplicates."""

    allow_reuse_address = False
    allow_reuse_port = False
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], *, paths: RuntimePaths):
        self.paths = paths
        self.service = FeedService(self.paths)
        self.source_revision = source_revision(self.paths.root)
        super().__init__(address, handler)

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the private CG Signal RSS dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4310)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--print-source-revision", action="store_true")
    arguments = parser.parse_args()
    paths = RuntimePaths.for_root(Path(__file__).resolve().parents[1])
    if arguments.print_source_revision:
        print(source_revision(paths.root))
        return
    try:
        server = DashboardServer((arguments.host, arguments.port), DashboardHandler, paths=paths)
    except OSError as error:
        raise SystemExit(
            f"CG Signal could not use {arguments.host}:{arguments.port}. It may already be running."
        ) from error
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.pid_file.write_text(str(os.getpid()), encoding="utf-8")
    url = f"http://{arguments.host}:{arguments.port}"
    print("\nCG Signal is ready")
    print(f"Open: {url}")
    print("Press Ctrl+C to stop.\n")
    if not arguments.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CG Signal.")
    finally:
        server.server_close()
        try:
            if paths.pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                paths.pid_file.unlink()
        except (FileNotFoundError, OSError):
            pass
