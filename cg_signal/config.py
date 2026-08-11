from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path


# The JSON shape is a client-facing contract.  Classifier changes are tracked
# independently so a rule update can reclassify stored articles without making
# otherwise-compatible clients reject the feed.
FEED_SCHEMA_VERSION = 1
CLASSIFICATION_REVISION = 4
SOURCE_CACHE_SCHEMA_VERSION = 2

CACHE_TTL_SECONDS = 15 * 60
FEED_REFRESH_RETRY_SECONDS = 60
IMAGE_INDEX_TTL_SECONDS = 30 * 86400
MAX_ITEMS_PER_SOURCE = 40
MAX_FEED_ENTRIES = 1000
MAX_STATE_IDS = 5000
MAX_STATE_NOTES = 1200
MAX_NOTE_LENGTH = 4000
MAX_FEEDBACK_ITEMS = 500
MAX_STATE_SOURCES = 200
MAX_ARCHIVE_PAGE_SIZE = 200
MAX_SOURCE_NAME_LENGTH = 100
MAX_SOURCE_URL_LENGTH = 2048


FEEDS = (
    {
        "id": "80-level",
        "name": "80 Level",
        "site": "https://80.lv/",
        "feed": "https://80.lv/feed",
        "accent": "#f4b400",
    },
    {
        "id": "cgworld",
        "name": "CGWORLD",
        "site": "https://cgworld.jp/",
        "feed": "https://cgworld.jp/atom.xml",
        "accent": "#ef5350",
    },
    {
        "id": "gamemakers",
        "name": "Game Makers",
        "site": "https://gamemakers.jp/",
        "feed": "https://gamemakers.jp/feed/",
        "accent": "#3d8bfd",
    },
    {
        "id": "3dnchu",
        "name": "3D人",
        "site": "https://3dnchu.com/",
        "feed": "https://3dnchu.com/feed/",
        "accent": "#ef7c3b",
    },
    {
        "id": "cginterest",
        "name": "CGinterest",
        "site": "https://cginterest.com/",
        "feed": "https://cginterest.com/feed/",
        "accent": "#2bb673",
    },
    {
        "id": "befores-afters",
        "name": "befores & afters",
        "site": "https://beforesandafters.com/",
        "feed": "https://beforesandafters.com/feed/",
        "accent": "#df4661",
        "limit": 20,
    },
    {
        "id": "game-developer",
        "name": "Game Developer",
        "site": "https://www.gamedeveloper.com/",
        "feed": "https://www.gamedeveloper.com/rss.xml",
        "accent": "#7357ff",
        "limit": 20,
    },
    {
        "id": "cartoon-brew",
        "name": "Cartoon Brew",
        "site": "https://www.cartoonbrew.com/",
        "feed": "https://www.cartoonbrew.com/feed/",
        "accent": "#f15a2a",
        "limit": 20,
    },
    {
        "id": "siggraph",
        "name": "ACM SIGGRAPH",
        "site": "https://blog.siggraph.org/",
        "feed": "https://blog.siggraph.org/feed/",
        "accent": "#008f95",
        "limit": 20,
    },
    {
        "id": "gamebusiness",
        "name": "GameBusiness.jp",
        "site": "https://www.gamebusiness.jp/category/development/",
        "feed": "https://www.gamebusiness.jp/rss/index.rdf",
        "accent": "#d14b3f",
        "limit": 20,
    },
    {
        "id": "automaton-interviews",
        "name": "AUTOMATON Interviews",
        "site": "https://automaton-media.com/devlog/interview/",
        "feed": "https://automaton-media.com/devlog/interview/feed/",
        "accent": "#5b6472",
        "limit": 20,
    },
    {
        "id": "automaton",
        "name": "AUTOMATON",
        "site": "https://automaton-media.com/",
        "feed": "https://automaton-media.com/feed/",
        "accent": "#e6504f",
        "limit": 20,
    },
    {
        "id": "denfaminicogamer",
        "name": "Denfaminicogamer",
        "site": "https://news.denfaminicogamer.jp/",
        "feed": "https://news.denfaminicogamer.jp/feed",
        "accent": "#8e44ad",
        "limit": 20,
    },
    {
        "id": "unreal-engine",
        "name": "Unreal Engine",
        "site": "https://www.unrealengine.com/",
        "feed": "https://www.unrealengine.com/rss?lang=en-US",
        "accent": "#4b75ff",
        "limit": 20,
    },
    {
        "id": "blender-developers",
        "name": "Blender Developers",
        "site": "https://code.blender.org/",
        "feed": "https://code.blender.org/feed/",
        "accent": "#f18a21",
        "limit": 20,
    },
)

SOURCE_ACCENTS = ("#4b75ff", "#f18a21", "#61d0c8", "#ff7857", "#a77bff", "#d7ff57")


@dataclass(frozen=True)
class RuntimePaths:
    """All mutable filesystem locations for one CG Signal runtime.

    Keeping these paths in an immutable value makes desktop and mobile
    services independently constructible.  No request or build needs to
    mutate process-wide module state.
    """

    root: Path
    static_dir: Path
    cache_dir: Path
    cache_file: Path
    feed_source_cache_file: Path
    image_index_file: Path
    thumbnail_dir: Path
    thumbnail_anchor: Path
    user_state_file: Path
    archive_db_file: Path
    pid_file: Path
    backup_dir: Path
    database_lock_file: Path

    @classmethod
    def for_root(cls, root: Path | str) -> "RuntimePaths":
        project_root = Path(root).resolve()
        cache_dir = project_root / ".cache"
        return cls(
            root=project_root,
            static_dir=project_root / "static",
            cache_dir=cache_dir,
            cache_file=cache_dir / "feed-cache.json",
            feed_source_cache_file=cache_dir / "feed-source-cache.json",
            image_index_file=cache_dir / "image-index.json",
            thumbnail_dir=cache_dir / "thumbnails",
            thumbnail_anchor=cache_dir,
            user_state_file=cache_dir / "user-state.json",
            archive_db_file=cache_dir / "cg-signal.db",
            pid_file=cache_dir / "server.pid",
            backup_dir=project_root / ".backups",
            database_lock_file=cache_dir / "database.lock",
        )

    def with_cache_dir(self, cache_dir: Path | str) -> "RuntimePaths":
        """Return an isolated runtime rooted at *cache_dir*.

        The project/static root remains unchanged, while all generated state
        moves together.  A caller may subsequently replace only the public
        request-cache paths when it wants persistent HTTP validators.
        """

        generated = Path(cache_dir).resolve()
        return RuntimePaths(
            root=self.root,
            static_dir=self.static_dir,
            cache_dir=generated,
            cache_file=generated / "feed-cache.json",
            feed_source_cache_file=generated / "feed-source-cache.json",
            image_index_file=generated / "image-index.json",
            thumbnail_dir=generated / "thumbnails",
            thumbnail_anchor=generated,
            user_state_file=generated / "user-state.json",
            archive_db_file=generated / "cg-signal.db",
            pid_file=generated / "server.pid",
            backup_dir=generated / "backups",
            database_lock_file=generated / "database.lock",
        )


def source_revision(root: Path | str) -> str:
    """Hash the executable package and entrypoint for launcher health checks."""

    project_root = Path(root).resolve()
    files = [project_root / "server.py"]
    package = project_root / "cg_signal"
    if package.is_dir():
        files.extend(sorted(package.glob("*.py")))
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(project_root)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()
