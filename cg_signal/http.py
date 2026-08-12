"""HTTP handler and desktop entrypoint for CG Signal."""

from __future__ import annotations

import argparse
from datetime import datetime
import html
import hmac
import json
import mimetypes
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from typing import Any

from .backup import (
    BackupError,
    DatabaseLease,
    DatabaseLeaseHeldError,
    create_backup,
    format_preview,
    restore_snapshot,
    verify_snapshot,
)
from .config import (
    CLASSIFICATION_REVISION,
    FEED_SCHEMA_VERSION,
    RuntimePaths,
    source_revision,
)
from .feeds import FeedService
from .thumbnails import (
    EXTENSION_TO_MIME,
    canonical_thumbnail_reference,
    read_verified_thumbnail,
)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "CGSignal/1.0"

    @property
    def dashboard(self) -> "DashboardServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, format_string: str, *args: Any) -> None:
        message = format_string % args
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def end_headers(self) -> None:
        # These are applied centrally so error responses and static files carry
        # the same browser-boundary policy as JSON API responses.
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    @property
    def expected_host(self) -> str:
        return f"127.0.0.1:{self.dashboard.server_address[1]}"

    def authorize_request(self, path: str, *, mutation: bool = False) -> bool:
        """Enforce the exact local authority and browser request metadata."""

        if self.headers.get("Host", "").strip() != self.expected_host:
            self.send_error(421, "Misdirected Request")
            return False
        expected_origin = f"http://{self.expected_host}"
        origin = self.headers.get("Origin")
        if origin:
            try:
                origin_parts = urllib.parse.urlsplit(origin)
                origin_ok = (
                    origin_parts.scheme.lower() == "http"
                    and origin_parts.netloc == self.expected_host
                    and not origin_parts.path
                    and not origin_parts.query
                    and not origin_parts.fragment
                )
            except ValueError:
                origin_ok = False
            if not origin_ok:
                self.send_error(403, "Forbidden")
                return False
        referer = self.headers.get("Referer")
        if referer:
            try:
                referer_parts = urllib.parse.urlsplit(referer)
                referer_ok = (
                    referer_parts.scheme.lower() == "http"
                    and referer_parts.netloc == self.expected_host
                )
            except ValueError:
                referer_ok = False
            if not referer_ok:
                self.send_error(403, "Forbidden")
                return False
        fetch_site = self.headers.get("Sec-Fetch-Site")
        fetch_site_value = fetch_site.strip().lower() if fetch_site else ""
        is_api = path == "/api" or path.startswith("/api/")
        allowed_fetch_sites = {"same-origin"} if is_api or mutation else {"same-origin", "none"}
        if fetch_site_value and fetch_site_value not in allowed_fetch_sites:
            self.send_error(403, "Forbidden")
            return False
        if is_api and path != "/api/health":
            supplied = self.headers.get("X-CG-Signal-Token", "")
            if not hmac.compare_digest(supplied, self.dashboard.api_token):
                self.send_error(403, "Forbidden")
                return False
        if mutation:
            content_type = self.headers.get("Content-Type", "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                self.send_error(415, "Unsupported Media Type")
                return False
            if origin:
                try:
                    origin_parts = urllib.parse.urlsplit(origin)
                    if f"{origin_parts.scheme.lower()}://{origin_parts.netloc}" != expected_origin:
                        raise ValueError
                except (ValueError, AttributeError):
                    self.send_error(403, "Forbidden")
                    return False
        return True

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
        if not self.authorize_request(parsed.path):
            return
        service = self.dashboard.service
        repository = service.repository
        if parsed.path.startswith("/thumbnails/"):
            # Asset URLs are deliberately token-free so ``<img>`` requests do
            # not need custom headers.  The exact canonical path remains
            # localhost Host-gated and cannot contain encoded aliases/query
            # strings or traversal segments.
            reference = parsed.path.lstrip("/")
            if parsed.query or canonical_thumbnail_reference(reference) != reference:
                self.send_error(404)
                return
            verified = read_verified_thumbnail(
                self.dashboard.paths.thumbnail_dir,
                reference,
                expected_anchor=self.dashboard.paths.thumbnail_anchor,
            )
            parsed_reference = reference.rsplit(".", 1)[-1]
            if verified is None or parsed_reference not in EXTENSION_TO_MIME:
                self.send_error(404)
                return
            body = verified.body
            self.send_response(200)
            self.send_header("Content-Type", EXTENSION_TO_MIME[parsed_reference])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=2592000, immutable")
            self.end_headers()
            self.wfile.write(body)
            return
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
                        # GET is intentionally passive.  Forced refresh has a
                        # separate POST endpoint so query strings cannot force
                        # an expensive network operation.
                        force=False,
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
        if parsed.path == "/api/history":
            parameters = urllib.parse.parse_qs(parsed.query)
            try:
                source_ids = [
                    source_id
                    for value in parameters.get("sources", [])
                    for source_id in value.split(",")
                    if source_id
                ]
                self.send_json(
                    repository.query_history(
                        query=parameters.get("q", [""])[0],
                        lane=parameters.get("lane", ["All"])[0],
                        source_ids=source_ids,
                        limit=int(parameters.get("limit", ["60"])[0]),
                        offset=int(parameters.get("offset", ["0"])[0]),
                        new_after=parameters.get("new_after", [""])[0],
                    )
                )
            except (ValueError, sqlite3.Error) as exc:
                self.send_json({"error": "Unable to search history", "detail": str(exc)}, status=400)
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
        if candidate.name == "index.html":
            token = html.escape(self.dashboard.api_token, quote=True).encode("ascii")
            body = body.replace(b"__CG_SIGNAL_API_TOKEN__", token)
        content_type, _ = mimetypes.guess_type(candidate.name)
        if candidate.suffix.lower() == ".mjs":
            content_type = "application/javascript"
        elif content_type and content_type.startswith("text/"):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if candidate.name == "index.html" else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if not self.authorize_request(parsed.path, mutation=True):
            return
        supported_paths = {
            "/api/feed/refresh", "/api/state", "/api/sources", "/api/sources/test", "/api/sources/toggle",
        }
        if parsed.path not in supported_paths:
            self.send_error(404)
            return
        service = self.dashboard.service
        repository = service.repository
        try:
            maximum_size = 750_000 if parsed.path == "/api/state" else 50_000
            payload = self.read_json_body(maximum_size)
            if parsed.path == "/api/feed/refresh":
                if payload != {}:
                    raise ValueError("Feed refresh payload must be an empty object.")
                self.send_json(service.feed_for_request(force=True))
            elif parsed.path == "/api/state":
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
        if address[0] != "127.0.0.1":
            raise ValueError("CG Signal only binds to 127.0.0.1.")
        self.paths = paths
        self.service = FeedService(self.paths)
        self.source_revision = source_revision(self.paths.root)
        self.api_token = secrets.token_urlsafe(32)
        super().__init__(address, handler)

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the private CG Signal RSS dashboard.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("backup", "restore"),
        help="backup local SQLite history or restore a verified snapshot",
    )
    parser.add_argument(
        "snapshot",
        nargs="?",
        help="snapshot directory for restore",
    )
    parser.add_argument("--destination", help="backup root for a generated snapshot child")
    parser.add_argument("--confirm", action="store_true", help="apply a restore after previewing it")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--print-source-revision", action="store_true")
    arguments = parser.parse_args()
    paths = RuntimePaths.for_root(Path(__file__).resolve().parents[1])

    if arguments.print_source_revision:
        if (
            arguments.command
            or arguments.snapshot
            or arguments.destination
            or arguments.confirm
            or arguments.port is not None
            or arguments.no_browser
        ):
            parser.error("--print-source-revision cannot be combined with backup or restore options")
        print(source_revision(paths.root))
        return

    if arguments.command == "backup":
        if (
            arguments.snapshot
            or arguments.confirm
            or arguments.port is not None
            or arguments.no_browser
            or arguments.print_source_revision
        ):
            parser.error("backup does not accept restore or serve-only options")
        try:
            snapshot_dir = create_backup(paths, arguments.destination, reason="manual")
        except (BackupError, OSError, sqlite3.Error) as error:
            print(f"CG Signal backup failed: {error}", file=sys.stderr)
            raise SystemExit(1) from error
        print(f"Backup verified: {snapshot_dir}")
        return

    if arguments.command == "restore":
        if not arguments.snapshot:
            parser.error("restore requires a snapshot directory")
        if (
            arguments.destination
            or arguments.port is not None
            or arguments.no_browser
            or arguments.print_source_revision
        ):
            parser.error("restore does not accept backup or serve-only options")
        try:
            # Preview is intentionally read-only and takes no lease.  The
            # confirmed path verifies again immediately before staging.
            verified = verify_snapshot(arguments.snapshot)
            if not arguments.confirm:
                print(format_preview(verified, paths.history_db_file))
                return
            result = restore_snapshot(paths, verified.path)
        except (BackupError, OSError, sqlite3.Error) as error:
            print(f"CG Signal restore failed: {error}", file=sys.stderr)
            raise SystemExit(1) from error
        print(f"Restore complete: {result.target}")
        if result.recovery_snapshot is not None:
            print(f"Recovery snapshot: {result.recovery_snapshot}")
        print("The dashboard remains stopped.")
        return

    if arguments.snapshot or arguments.destination or arguments.confirm:
        parser.error("backup or restore must be specified before these options")

    # The lease is acquired before constructing DashboardServer and held until
    # after server_close(), so confirmed restore cannot race startup/shutdown.
    lease = DatabaseLease(paths.database_lock_file)
    server: DashboardServer | None = None
    try:
        try:
            lease.acquire()
        except DatabaseLeaseHeldError as error:
            print(
                "CG Signal could not acquire the database lease. "
                "Stop the dashboard with stop-dashboard.ps1, then retry.",
                file=sys.stderr,
            )
            raise SystemExit(1) from error
        try:
            server = DashboardServer(("127.0.0.1", arguments.port or 4310), DashboardHandler, paths=paths)
        except OSError as error:
            raise SystemExit(
                f"CG Signal could not use 127.0.0.1:{arguments.port or 4310}. It may already be running."
            ) from error
        paths.cache_dir.mkdir(parents=True, exist_ok=True)
        paths.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        url = f"http://127.0.0.1:{arguments.port or 4310}"
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
        primary_error = sys.exc_info()[1]
        cleanup_error: BaseException | None = None
        if server is not None:
            try:
                server.server_close()
            except BaseException as error:
                cleanup_error = error
        try:
            if paths.pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                paths.pid_file.unlink()
        except FileNotFoundError:
            pass
        except BaseException as error:
            if cleanup_error is None:
                cleanup_error = error
        finally:
            try:
                lease.release()
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
        if cleanup_error is not None:
            if primary_error is not None:
                try:
                    primary_error.add_note(f"Dashboard cleanup failed: {cleanup_error}")
                except Exception:
                    pass
            else:
                raise RuntimeError(f"Dashboard cleanup failed: {cleanup_error}") from cleanup_error
