"""Refresh packaging/aur/ for a new release.

Only two things change between releases — the version and the sdist checksum —
but both are exactly the kind of thing that is wrong silently. A checksum
naming a file the release does not contain has bitten this project's Scoop
bucket before, so this fetches the real sdist and hashes what it actually
downloaded rather than trusting a published digest.

    uv run python scripts/aur_pkgbuild.py --version 0.2.83

Writes PKGBUILD and .SRCINFO in place. It does not push to the AUR: that needs
an SSH key registered to a maintainer account, which is a person's job.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUR = ROOT / "packaging" / "aur"
# The stable, predictable form. The hashed /packages/<a>/<b>/<hash>/ URL that
# the PyPI JSON API returns changes shape and is far less readable in a
# PKGBUILD that a human has to review.
SDIST = "https://files.pythonhosted.org/packages/source/a/anime-sh/anime_sh-{v}.tar.gz"


def sdist_sha256(version: str) -> str:
    url = SDIST.format(v=version)
    with urllib.request.urlopen(url, timeout=60) as resp:
        if resp.status != 200:
            raise SystemExit(f"{url} -> HTTP {resp.status}")
        blob = resp.read()
    if len(blob) < 10_000:
        # A 404 page hashes perfectly well and would sail through.
        raise SystemExit(f"{url} returned {len(blob)} bytes - that is not an sdist")
    return hashlib.sha256(blob).hexdigest()


def srcinfo(pkgbuild: str) -> str:
    """Render .SRCINFO from the PKGBUILD.

    The AUR requires it to match, and `makepkg --printsrcinfo` only runs on
    Arch. This covers the fields this PKGBUILD actually uses; anything richer
    belongs in a real makepkg run.
    """

    def one(key: str) -> str | None:
        m = re.search(rf"^{key}=(.+)$", pkgbuild, re.M)
        if not m:
            return None
        return m.group(1).strip().strip("'\"")

    def many(key: str) -> list[str]:
        m = re.search(rf"^{key}=\((.*?)\)$", pkgbuild, re.M | re.S)
        if not m:
            return []
        return re.findall(r"'([^']+)'", m.group(1))

    version = one("pkgver")
    out = [
        f"pkgbase = {one('pkgname')}",
        f"\tpkgdesc = {one('pkgdesc')}",
        f"\tpkgver = {version}",
        f"\tpkgrel = {one('pkgrel')}",
        f"\turl = {one('url')}",
    ]
    for arch in many("arch"):
        out.append(f"\tarch = {arch}")
    for lic in many("license"):
        out.append(f"\tlicense = {lic}")
    for dep in many("makedepends"):
        out.append(f"\tmakedepends = {dep}")
    for dep in many("depends"):
        out.append(f"\tdepends = {dep}")
    for dep in many("optdepends"):
        out.append(f"\toptdepends = {dep}")
    out.append(f"\tsource = {SDIST.format(v=version)}")
    for sha in many("sha256sums"):
        out.append(f"\tsha256sums = {sha}")
    out += ["", f"pkgname = {one('pkgname')}", ""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    args = ap.parse_args()

    path = AUR / "PKGBUILD"
    text = path.read_text(encoding="utf-8")
    sha = sdist_sha256(args.version)

    text = re.sub(r"^pkgver=.*$", f"pkgver={args.version}", text, count=1, flags=re.M)
    # A new upstream version restarts the package revision.
    text = re.sub(r"^pkgrel=.*$", "pkgrel=1", text, count=1, flags=re.M)
    text = re.sub(r"^sha256sums=\('[^']*'\)$", f"sha256sums=('{sha}')",
                  text, count=1, flags=re.M)
    path.write_bytes(text.encode("utf-8"))
    (AUR / ".SRCINFO").write_bytes(srcinfo(text).encode("utf-8"))

    print(f"anime-sh {args.version}", file=sys.stderr)
    print(f"  sha256 {sha}  (hashed from the downloaded sdist)", file=sys.stderr)
    print(f"  wrote {path} and {AUR / '.SRCINFO'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
