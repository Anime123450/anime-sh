"""Fill the winget and scoop manifests in for one release.

The templates in `packaging/` carry a placeholder version and a zeroed hash on
purpose: a manifest is a promise that an exact file lives at an exact URL, so
the two fields that must never be guessed are the two that a human editing YAML
by hand gets wrong. This reads the hash from the artifact itself.

    python scripts/make_manifests.py 0.2.66 --from-file dist/bundle/anime.exe
    python scripts/make_manifests.py 0.2.66 --sha256 <hex>

Writes to `dist/manifests/`. Nothing is submitted automatically — winget goes
through a pull request to microsoft/winget-pkgs, and a scoop bucket is a repo
you own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLACEHOLDER_VERSION = "0.0.0"
PLACEHOLDER_HASH = "0" * 64


def sha256_of(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def render(text: str, version: str, sha256: str) -> str:
    """Substitute the two fields that must match the published artifact."""
    text = text.replace(PLACEHOLDER_VERSION, version)
    return text.replace(PLACEHOLDER_HASH, sha256)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("version", help="release version, without the leading v")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-file", type=pathlib.Path,
                        help="the built .exe, hashed in place")
    source.add_argument("--sha256", help="the hash, if you already have it")
    ap.add_argument("--out", type=pathlib.Path,
                    default=ROOT / "dist" / "manifests")
    args = ap.parse_args(argv)

    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        print(f"error: {args.version!r} is not a release version", file=sys.stderr)
        return 2

    if args.from_file:
        if not args.from_file.is_file():
            print(f"error: no such file: {args.from_file}", file=sys.stderr)
            return 2
        sha = sha256_of(args.from_file)
    else:
        sha = args.sha256.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            print("error: --sha256 must be 64 hex characters", file=sys.stderr)
            return 2

    written = []
    for template in sorted((ROOT / "packaging" / "winget").glob("*.yaml")):
        target = args.out / "winget" / template.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            render(template.read_text(encoding="utf-8"), args.version, sha),
            encoding="utf-8",
        )
        written.append(target)

    scoop_src = ROOT / "packaging" / "scoop" / "anime-sh.json"
    scoop_out = args.out / "scoop" / "anime-sh.json"
    scoop_out.parent.mkdir(parents=True, exist_ok=True)
    rendered = render(scoop_src.read_text(encoding="utf-8"), args.version, sha)
    json.loads(rendered)  # a manifest that will not parse helps nobody
    scoop_out.write_text(rendered, encoding="utf-8")
    written.append(scoop_out)

    for path in written:
        # Relative when it helps, absolute when it must: `--out` is free to
        # point anywhere, and `relative_to` raises rather than giving up.
        try:
            print(path.relative_to(ROOT))
        except ValueError:
            print(path)
    print(f"\nversion {args.version}  sha256 {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
