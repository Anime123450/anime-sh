# AUR package

`PKGBUILD` and `.SRCINFO` for the Arch User Repository, built from the PyPI
sdist against Arch's own Python packages (rather than vendoring a virtualenv).

Both files are **generated** — after a release, run:

```bash
uv run python scripts/aur_pkgbuild.py --version <new-version>
```

That downloads the sdist, hashes what it actually received, and rewrites both
files. CI then builds the package in an Arch container and installs it; that is
the only place `makepkg` ever runs, since this project is developed on Windows.

## Publishing to the AUR

This is a person's job, not CI's: pushing needs an SSH key registered to an AUR
maintainer account.

First time only:

```bash
git clone ssh://aur@aur.archlinux.org/anime-sh.git aur-anime-sh
```

Then, for each release:

```bash
cp packaging/aur/PKGBUILD packaging/aur/.SRCINFO aur-anime-sh/
cd aur-anime-sh
git commit -am "anime-sh <new-version>"
git push
```

`.SRCINFO` must match the `PKGBUILD` — the AUR serves it directly, so a stale
one is a package that lies about its own version. CI checks this with
`makepkg --printsrcinfo`.
