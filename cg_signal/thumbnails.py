"""Validation and storage helpers for app-owned article thumbnail assets.

The feed contains only opaque, content-addressed references.  Publisher URLs
are fetched by :class:`cg_signal.safe_http.SafeHttpClient`, validated here,
and written beneath the runtime's thumbnail directory before any browser can
request them.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from contextlib import contextmanager
import threading
import time
from typing import Any, Mapping


MAX_THUMBNAIL_BYTES = 2_000_000
MAX_THUMBNAIL_FILES = 2_000
MAX_THUMBNAIL_TOTAL_BYTES = 256_000_000
THUMBNAIL_INDEX_SCHEMA_VERSION = 1
THUMBNAIL_POSITIVE_TTL_SECONDS = 30 * 86400
THUMBNAIL_NEGATIVE_TTL_SECONDS = 86400

MIME_TO_EXTENSION = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
EXTENSION_TO_MIME = {extension: mime for mime, extension in MIME_TO_EXTENSION.items()}
_REFERENCE_RE = re.compile(r"^thumbnails/([0-9a-f]{64})\.(jpg|png|webp)$")
_STORE_LOCKS: dict[Path, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class ValidatedThumbnail:
    body: bytes
    mime_type: str
    extension: str
    digest: str

    @property
    def reference(self) -> str:
        return f"thumbnails/{self.digest}.{self.extension}"


def _content_type(headers: Any) -> str:
    if headers is None:
        return ""
    raw_value = None
    getter = getattr(headers, "get", None)
    if callable(getter):
        try:
            raw_value = getter("Content-Type")
        except (TypeError, ValueError):
            raw_value = None
    if raw_value is not None:
        return str(raw_value).strip().lower()
    getter = getattr(headers, "get_content_type", None)
    if callable(getter):
        try:
            return str(getter()).lower()
        except (TypeError, ValueError):
            return ""
    if isinstance(headers, Mapping):
        value = headers.get("Content-Type", headers.get("content-type", ""))
    else:
        value = ""
    return str(value).strip().lower()


def _magic_matches(mime_type: str, body: bytes) -> bool:
    if mime_type == "image/jpeg":
        return body.startswith(b"\xff\xd8\xff")
    if mime_type == "image/png":
        return body.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/webp":
        return len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP"
    return False


def validate_thumbnail_bytes(body: Any, mime_type: Any) -> ValidatedThumbnail | None:
    """Validate exact supported MIME/signature and return digest metadata."""

    if not isinstance(body, (bytes, bytearray)):
        return None
    body = bytes(body)
    mime = str(mime_type or "").strip().lower()
    extension = MIME_TO_EXTENSION.get(mime)
    if extension is None or not body or len(body) > MAX_THUMBNAIL_BYTES:
        return None
    if not _magic_matches(mime, body):
        return None
    return ValidatedThumbnail(body, mime, extension, hashlib.sha256(body).hexdigest())


def validate_thumbnail_response(response: Any) -> ValidatedThumbnail | None:
    if response is None or getattr(response, "status", None) != 200:
        return None
    body = getattr(response, "body", b"")
    return validate_thumbnail_bytes(body, _content_type(getattr(response, "headers", None)))


def canonical_thumbnail_reference(value: Any) -> str:
    """Return *value* only when it is an exact canonical asset reference."""

    if not isinstance(value, str) or _REFERENCE_RE.fullmatch(value) is None:
        return ""
    return value


def parse_thumbnail_reference(value: Any) -> tuple[str, str] | None:
    match = _REFERENCE_RE.fullmatch(value) if isinstance(value, str) else None
    return (match.group(1), match.group(2)) if match else None


def _is_junction_or_symlink(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        return True


def _absolute_path(path: Path | str) -> Path:
    # ``absolute``/``abspath`` normalizes ``..`` without following reparse
    # points.  Resolution is performed only after every existing component has
    # been checked below.
    return Path(os.path.abspath(os.fspath(path)))


def validate_thumbnail_root(
    root: Path | str,
    expected_anchor: Path | str | None = None,
) -> Path:
    """Validate a thumbnail store beneath a trusted, non-reparse anchor.

    The returned path is the normalized lexical path, never a redirected
    ``resolve()`` result.  All existing components are checked for symlinks or
    Windows junctions before the resolved containment check.
    """

    root_path = _absolute_path(root)
    anchor_path = _absolute_path(expected_anchor if expected_anchor is not None else root_path.parent)
    try:
        root_path.relative_to(anchor_path)
    except ValueError as exc:
        raise ValueError("Thumbnail root escapes its trusted cache anchor.") from exc
    if root_path == anchor_path:
        raise ValueError("Thumbnail root must be beneath its trusted cache anchor.")

    # Check every component, including the anchor and all parents, so a
    # junction/symlink cannot redirect either traversal or mutation.
    candidate = root_path
    while True:
        if _is_junction_or_symlink(candidate):
            raise ValueError("Thumbnail cache paths may not contain symlinks or junctions.")
        if candidate == candidate.parent:
            break
        candidate = candidate.parent

    try:
        resolved_anchor = anchor_path.resolve(strict=False)
        resolved_root = root_path.resolve(strict=False)
        if resolved_anchor != anchor_path:
            raise ValueError("Trusted thumbnail anchor resolves through a link.")
        resolved_root.relative_to(resolved_anchor)
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Trusted thumbnail"):
            raise
        raise ValueError("Thumbnail root escapes its trusted cache anchor.") from exc
    return root_path


def _store_lock_for(root_path: Path) -> threading.RLock:
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(root_path)
        if lock is None:
            lock = threading.RLock()
            _STORE_LOCKS[root_path] = lock
        return lock


@contextmanager
def _store_lock(root_path: Path):
    lock = _store_lock_for(root_path)
    with lock:
        lock_path = root_path / ".thumbnail.lock"
        if _is_junction_or_symlink(lock_path):
            raise ValueError("Thumbnail lock may not be a symlink or junction.")
        try:
            handle = lock_path.open("a+b")
        except OSError as exc:
            raise ValueError("Thumbnail store lock is unavailable.") from exc
        try:
            # Keep publication quota enforcement process-safe when the
            # launcher happens to have more than one worker process.
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.01)
                        handle.seek(0)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            if _is_junction_or_symlink(lock_path):
                raise ValueError("Thumbnail lock may not be a symlink or junction.")
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()


def thumbnail_path(
    root: Path | str,
    reference: Any,
    *,
    expected_anchor: Path | str | None = None,
) -> Path | None:
    parsed = parse_thumbnail_reference(reference)
    if parsed is None:
        return None
    try:
        root_path = validate_thumbnail_root(root, expected_anchor)
    except ValueError:
        return None
    path = root_path / Path(reference).name
    if path.parent != root_path or path.name != Path(reference).name:
        return None
    return path


def _validated_file(path: Path, digest: str, extension: str) -> ValidatedThumbnail | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_THUMBNAIL_BYTES:
            return None
        body = path.read_bytes()
    except (OSError, ValueError):
        return None
    result = validate_thumbnail_bytes(body, EXTENSION_TO_MIME.get(extension, ""))
    if result is None or result.digest != digest:
        return None
    return result


def read_verified_thumbnail(
    root: Path | str,
    reference: Any,
    *,
    expected_anchor: Path | str | None = None,
) -> ValidatedThumbnail | None:
    parsed = parse_thumbnail_reference(reference)
    path = thumbnail_path(root, reference, expected_anchor=expected_anchor)
    if parsed is None or path is None:
        return None
    return _validated_file(path, parsed[0], parsed[1])


def _asset_inventory(root_path: Path) -> list[tuple[Path, ValidatedThumbnail]]:
    inventory: list[tuple[Path, ValidatedThumbnail]] = []
    if not root_path.is_dir():
        return inventory
    try:
        paths = list(root_path.iterdir())
    except OSError:
        return inventory
    for path in paths:
        if path.name.startswith(".thumbnail-") or path.name == ".thumbnail.lock":
            continue
        if path.is_symlink() or not path.is_file():
            continue
        parsed = parse_thumbnail_reference(f"thumbnails/{path.name}")
        if parsed is None:
            continue
        verified = _validated_file(path, parsed[0], parsed[1])
        if verified is not None:
            inventory.append((path, verified))
    return inventory


def _unlink_store_path(
    root_path: Path,
    path: Path,
    expected_anchor: Path | str | None,
) -> None:
    """Unlink one store child only after revalidating the trusted root."""

    root_path = validate_thumbnail_root(root_path, expected_anchor)
    if path.parent != root_path:
        raise ValueError("Thumbnail path escapes its trusted store.")
    path.unlink()


def store_thumbnail(
    root: Path | str,
    thumbnail: ValidatedThumbnail | bytes,
    mime_type: str | None = None,
    *,
    expected_anchor: Path | str | None = None,
    max_files: int = MAX_THUMBNAIL_FILES,
    max_bytes: int = MAX_THUMBNAIL_TOTAL_BYTES,
) -> str:
    """Atomically store validated bytes and return the canonical reference."""

    if isinstance(thumbnail, ValidatedThumbnail):
        validated = validate_thumbnail_bytes(thumbnail.body, thumbnail.mime_type)
        if validated is None or validated.digest != thumbnail.digest or validated.extension != thumbnail.extension:
            raise ValueError("Invalid thumbnail bytes or media type.")
    else:
        validated = validate_thumbnail_bytes(thumbnail, mime_type)
        if validated is None:
            raise ValueError("Invalid thumbnail bytes or media type.")
    root_path = validate_thumbnail_root(root, expected_anchor)
    root_path.mkdir(parents=True, exist_ok=True)
    # Revalidate after creating missing components and immediately before any
    # file operation.  A concurrent replacement with a reparse point is
    # rejected rather than resolved and accepted.
    root_path = validate_thumbnail_root(root_path, expected_anchor)
    with _store_lock(root_path):
        root_path = validate_thumbnail_root(root_path, expected_anchor)
        target = root_path / f"{validated.digest}.{validated.extension}"
        if _is_junction_or_symlink(target):
            raise ValueError("Thumbnail assets may not be symlinks or junctions.")
        if target.is_file():
            existing = _validated_file(target, validated.digest, validated.extension)
            if existing is not None:
                return validated.reference
            _unlink_store_path(root_path, target, expected_anchor)
        try:
            max_files = int(max_files)
            max_bytes = int(max_bytes)
        except (TypeError, ValueError) as exc:
            raise ValueError("Thumbnail store quotas must be integers.") from exc
        if max_files < 1 or max_bytes < len(validated.body):
            raise ValueError("Thumbnail store quota exceeded.")
        inventory = _asset_inventory(root_path)
        if len(inventory) >= max_files or sum(len(item[1].body) for item in inventory) + len(validated.body) > max_bytes:
            raise ValueError("Thumbnail store quota exceeded.")
        temporary_name = ""
        try:
            root_path = validate_thumbnail_root(root_path, expected_anchor)
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=root_path, prefix=".thumbnail-", suffix=".tmp", delete=False
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(validated.body)
                temporary.flush()
                os.fsync(temporary.fileno())
            root_path = validate_thumbnail_root(root_path, expected_anchor)
            try:
                # A hard-link creation is atomic and refuses to replace an
                # existing digest target, even when another worker wins the race.
                os.link(temporary_name, target)
            except FileExistsError:
                # Another worker won the immutable digest race.  Never
                # overwrite its bytes; use them only after revalidation.
                existing = _validated_file(target, validated.digest, validated.extension)
                if existing is None:
                    raise
        finally:
            if temporary_name:
                try:
                    _unlink_store_path(root_path, Path(temporary_name), expected_anchor)
                except (FileNotFoundError, OSError, ValueError):
                    pass
        return validated.reference


def load_thumbnail_index(path: Path | str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"schema_version": THUMBNAIL_INDEX_SCHEMA_VERSION, "entries": {}}
    if not isinstance(payload, dict) or payload.get("schema_version") != THUMBNAIL_INDEX_SCHEMA_VERSION:
        return {"schema_version": THUMBNAIL_INDEX_SCHEMA_VERSION, "entries": {}}
    entries = payload.get("entries")
    return {
        "schema_version": THUMBNAIL_INDEX_SCHEMA_VERSION,
        "entries": entries if isinstance(entries, dict) else {},
    }


def save_thumbnail_index(path: Path | str, index: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def prune_thumbnail_store(
    root: Path | str,
    index: dict[str, Any],
    *,
    now: float | None = None,
    max_files: int = MAX_THUMBNAIL_FILES,
    max_bytes: int = MAX_THUMBNAIL_TOTAL_BYTES,
    expected_anchor: Path | str | None = None,
) -> dict[str, Any]:
    """Remove malformed, stale, unreferenced, and over-quota assets."""

    root_path = validate_thumbnail_root(root, expected_anchor)
    if not root_path.is_dir():
        return {"schema_version": THUMBNAIL_INDEX_SCHEMA_VERSION, "entries": {}}
    with _store_lock(root_path):
        return _prune_thumbnail_store_locked(
            root_path,
            index,
            now=now,
            max_files=max_files,
            max_bytes=max_bytes,
            expected_anchor=expected_anchor,
        )


def _prune_thumbnail_store_locked(
    root_path: Path,
    index: dict[str, Any],
    *,
    now: float | None,
    max_files: int,
    max_bytes: int,
    expected_anchor: Path | str | None,
) -> dict[str, Any]:
    root_path = validate_thumbnail_root(root_path, expected_anchor)
    current = time.time() if now is None else float(now)
    entries = index.get("entries", {}) if isinstance(index, dict) else {}
    clean: dict[str, Any] = {}
    referenced: dict[str, tuple[str, float]] = {}
    for article_url, entry in entries.items() if isinstance(entries, dict) else []:
        if not isinstance(article_url, str) or not isinstance(entry, dict):
            continue
        status = entry.get("status")
        checked_at = entry.get("checked_at")
        try:
            age = current - float(checked_at)
        except (TypeError, ValueError):
            continue
        ttl = THUMBNAIL_POSITIVE_TTL_SECONDS if status == "ok" else THUMBNAIL_NEGATIVE_TTL_SECONDS
        if age < 0 or age >= ttl:
            continue
        if status == "ok":
            reference = canonical_thumbnail_reference(entry.get("image"))
            parsed = parse_thumbnail_reference(reference)
            if not reference or parsed is None or read_verified_thumbnail(
                root_path, reference, expected_anchor=expected_anchor
            ) is None:
                continue
            clean[article_url] = {"status": "ok", "image": reference, "checked_at": float(checked_at)}
            referenced[reference] = (article_url, float(checked_at))
        elif status == "miss":
            clean[article_url] = {"status": "miss", "checked_at": float(checked_at)}
    for temporary in root_path.glob(".thumbnail-*.tmp") if root_path.is_dir() else []:
        try:
            _unlink_store_path(root_path, temporary, expected_anchor)
        except (OSError, ValueError):
            pass
    files: list[tuple[Path, str, int, float]] = []
    if root_path.is_dir():
        for path in root_path.iterdir():
            if (
                path.is_symlink()
                or not path.is_file()
                or path.name.startswith(".thumbnail-")
                or path.name == ".thumbnail.lock"
            ):
                if path.is_symlink() or path.name.startswith(".thumbnail-"):
                    try:
                        _unlink_store_path(root_path, path, expected_anchor)
                    except (OSError, ValueError):
                        pass
                continue
            reference = f"thumbnails/{path.name}"
            parsed = parse_thumbnail_reference(reference)
            if parsed is None or reference not in referenced:
                try:
                    _unlink_store_path(root_path, path, expected_anchor)
                except (OSError, ValueError):
                    pass
                continue
            verified = read_verified_thumbnail(root_path, reference, expected_anchor=expected_anchor)
            if verified is None:
                try:
                    _unlink_store_path(root_path, path, expected_anchor)
                except (OSError, ValueError):
                    pass
                clean.pop(referenced[reference][0], None)
                referenced.pop(reference, None)
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0
            files.append((path, reference, len(verified.body), referenced[reference][1] or mtime))
    total = sum(item[2] for item in files)
    while len(files) > max_files or total > max_bytes:
        victim = min(files, key=lambda item: item[3])
        files.remove(victim)
        total -= victim[2]
        try:
            _unlink_store_path(root_path, victim[0], expected_anchor)
        except (OSError, ValueError):
            pass
        article_url = referenced.get(victim[1], (None, 0))[0]
        if article_url is not None:
            clean.pop(article_url, None)
    return {"schema_version": THUMBNAIL_INDEX_SCHEMA_VERSION, "entries": clean}


# Friendly aliases for callers/tests that prefer the shorter names.
thumbnail_reference = canonical_thumbnail_reference
thumbnail_from_response = validate_thumbnail_response
resolve_thumbnail_path = thumbnail_path


__all__ = [
    "EXTENSION_TO_MIME",
    "MAX_THUMBNAIL_BYTES",
    "MAX_THUMBNAIL_FILES",
    "MAX_THUMBNAIL_TOTAL_BYTES",
    "MIME_TO_EXTENSION",
    "THUMBNAIL_INDEX_SCHEMA_VERSION",
    "THUMBNAIL_NEGATIVE_TTL_SECONDS",
    "THUMBNAIL_POSITIVE_TTL_SECONDS",
    "ValidatedThumbnail",
    "canonical_thumbnail_reference",
    "load_thumbnail_index",
    "parse_thumbnail_reference",
    "prune_thumbnail_store",
    "read_verified_thumbnail",
    "save_thumbnail_index",
    "store_thumbnail",
    "thumbnail_from_response",
    "thumbnail_path",
    "thumbnail_reference",
    "validate_thumbnail_bytes",
    "validate_thumbnail_response",
    "validate_thumbnail_root",
    "resolve_thumbnail_path",
]
