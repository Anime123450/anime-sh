#!/usr/bin/env python
"""Print one version's section from CHANGELOG.md.

Release notes already exist — they are written for users, in the changelog, at
the time the change is made. Retyping them into the GitHub release form is how
they drift. This extracts the section verbatim so the tag, the changelog and the
release page cannot disagree.

    python scripts/changelog_section.py 0.2.40        # -> that section's body
    python scripts/changelog_section.py v0.2.40       # a leading v is fine
    python scripts/changelog_section.py --list        # every version present

Exit code 1 (with an explanation on stderr) if the version has no section, so a
release workflow fails loudly rather than publishing empty notes.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

# "## [0.2.40] — 2026-08-01", tolerating an em dash, an en dash or a hyphen, and
# a bare "## 0.2.40" without brackets.
_HEADING = re.compile(r"^##\s+\[?(?P<version>[0-9][^\]\s]*)\]?(?:\s*[—–-]\s*(?P<date>.+))?\s*$")


def sections(text: str) -> dict[str, tuple[str, str]]:
    """version -> (date, body). Body keeps its original markdown."""
    out: dict[str, tuple[str, str]] = {}
    current: str | None = None
    date = ""
    buf: list[str] = []
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            if current is not None:
                out[current] = (date, "\n".join(buf).strip())
            current = m.group("version")
            date = (m.group("date") or "").strip()
            buf = []
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        out[current] = (date, "\n".join(buf).strip())
    return out


def main() -> int:
    # This changelog describes provider request flows with arrows ("/search ->
    # /ajax/episode/list"), and those are real U+2192. Piped on Windows, Python
    # picks cp1252 for stdout and dies encoding them -- which made this script
    # report "no section" for the nine versions that happen to contain one,
    # exactly the versions whose notes are most worth reading. Write UTF-8
    # regardless of what the console claims to be.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover - exotic stdout
        pass

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", nargs="?", help="e.g. 0.2.40 or v0.2.40")
    ap.add_argument("--list", action="store_true", help="list every version in the changelog")
    ap.add_argument("--changelog", type=Path, default=CHANGELOG)
    args = ap.parse_args()

    if not args.changelog.is_file():
        print(f"no changelog at {args.changelog}", file=sys.stderr)
        return 1

    found = sections(args.changelog.read_text(encoding="utf-8"))

    if args.list:
        for version, (date, _) in found.items():
            print(f"{version}\t{date}")
        return 0

    if not args.version:
        ap.error("give a version, or --list")

    wanted = args.version.lstrip("vV")
    if wanted not in found:
        print(
            f"CHANGELOG.md has no section for {wanted}. "
            f"Known: {', '.join(list(found)[:5])}…",
            file=sys.stderr,
        )
        return 1

    date, body = found[wanted]
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
