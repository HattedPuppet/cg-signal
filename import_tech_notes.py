r"""Import CG Signal morning brief notes into a local, searchable Markdown folder.

Run from the CG Signal project, for example on Windows:
    python import_tech_notes.py --inbox "$env:USERPROFILE\Downloads" --out "D:\CG Notes"

The inbox contains cg-signal-notes-YYYY-MM-DD.md files. The destination holds
one Markdown file per primary source URL. Run the same command again after
new files arrive, or use it as the action for a local scheduled task.
"""

from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit


BATCH_NAME = re.compile(r"cg-signal-notes-(\d{4}-\d{2}-\d{2})\.md")
SOURCE_LINE = re.compile(r"^- 一次資料[:：][ \t]*(https?://\S+)[ \t]*$", re.MULTILINE)
NOTE_DATE = re.compile(r"^<!-- cg-signal-date: (\d{4}-\d{2}-\d{2}) -->\n")


def read_batch(path: Path, day: str) -> dict[str, str]:
    date.fromisoformat(day)
    heading, *sections = re.split(r"^## (?=\S)", path.read_text(encoding="utf-8-sig"), flags=re.MULTILINE)
    if heading.strip() != f"# CG Signal 技術メモ {day}" or not sections:
        raise ValueError(f"Unexpected heading or missing notes in {path.name}")

    notes = {}
    for section in sections:
        sources = SOURCE_LINE.findall(section)
        if len(sources) != 1 or not urlsplit(sources[0]).hostname:
            raise ValueError(f"Each note in {path.name} must contain one primary source URL")
        source = urlsplit(sources[0])._replace(fragment="").geturl()
        if source in notes:
            raise ValueError(f"Duplicate primary source in {path.name}: {source}")
        notes[source] = f"<!-- cg-signal-date: {day} -->\n# {section.strip()}\n"
    return notes


def write_note(target: Path, content: str) -> None:
    handle = NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=target.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def import_notes(inbox: Path, destination: Path) -> int:
    if not inbox.is_dir():
        raise ValueError(f"Inbox directory does not exist: {inbox}")

    imported = {}
    for batch in sorted(inbox.glob("cg-signal-notes-*.md")):
        match = BATCH_NAME.fullmatch(batch.name)
        if match and batch.is_file():
            imported.update(read_batch(batch, match[1]))

    changes = 0
    destination.mkdir(parents=True, exist_ok=True)
    for source, content in imported.items():
        digest = sha256(source.encode("utf-8")).hexdigest()
        target = destination / f"{digest}.md"
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            existing_day = NOTE_DATE.match(existing)
            new_day = NOTE_DATE.match(content)
            if not existing_day:
                raise ValueError(f"Existing file was not created by this importer: {target}")
            if existing == content or (new_day and existing_day[1] > new_day[1]):
                continue
        write_note(target, content)
        changes += 1
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inbox", type=Path, required=True, help="Folder with daily Markdown files")
    parser.add_argument("--out", type=Path, required=True, help="Local Markdown folder for Pi or Codex")
    args = parser.parse_args()
    try:
        changes = import_notes(args.inbox, args.out)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Import failed: {error}\n")
    print(f"{changes} note(s) written to {args.out}")


if __name__ == "__main__":
    main()
