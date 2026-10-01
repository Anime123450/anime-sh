# Standalone Windows build

Produces `anime.exe` — anime-sh with Python and every dependency baked in, for
people who have no Python and shouldn't need to care.

```bash
uv run --with pyinstaller python -m PyInstaller --onefile --name anime --console \
  --collect-all textual --collect-all textual_image --collect-all rich --collect-all typer \
  --collect-all pydantic --collect-all aiosqlite --collect-all platformdirs \
  --collect-submodules anime_sh --collect-data anime_sh --copy-metadata anime-sh \
  --distpath dist/bundle --workpath build/pyi --specpath build/pyi \
  packaging/launcher.py
```

You do not normally need to run this by hand: the release workflow builds the
same bundle for every tag, verifies it, and attaches it to the GitHub Release.
This is here for reproducing a build locally.

Two things about the command:

- **`packaging/launcher.py` as the entry script** is required.
  `anime_sh/__main__.py` uses a relative import, which PyInstaller cannot execute
  as a top-level script (`attempted relative import with no known parent
  package`).
- **`--copy-metadata anime-sh`** is kept but is no longer load-bearing.
  Providers and resolvers are entry points, entry points live in the package
  metadata, and a bundle without it once reported version `0.0.0+unknown` with
  no providers — an app that cannot play anything, built green. Re-checked on
  PyInstaller 6 (2026-08-30): a build *without* the flag still carries the
  `dist-info` and still lists both providers, so the collection now happens
  anyway. Left in place because it is free and says what we depend on; the real
  guard is the verification step in the release workflow, which asserts the
  providers are there rather than trusting any particular flag.

## Verifying a build

Test it the way a recipient runs it — with no Python and nothing else on PATH:

```bash
env -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV PATH="/c/Windows/System32" ./dist/bundle/anime.exe doctor
```

`providers: anizone, anikoto` and a real version number mean the metadata made
it in. Then confirm it can reach a stream:

```bash
./dist/bundle/anime.exe play "Frieren" -e 1 --json
```

## Shipping it to someone

Every tagged release carries `anime-sh-<version>-windows-x64.exe` on its GitHub
Release page, which is what the winget and scoop manifests point at. **About
30 MB**, one file, no Python needed. (An earlier note here said 145 MB; that was
a `--onedir` build. The one-file bundle compresses its own payload.)

`mpv` still has to be on `PATH` to play anything, and `ffmpeg` to download —
neither is bundled. The package managers declare them as dependencies, which is
the right way round: **do not bundle mpv in a public release.** mpv is GPL, so
redistributing the binary carries source-offer obligations. Declaring a
dependency is not the same thing as shipping one.


## Where to publish it

Ranked for this project specifically — a Python TUI that shells out to `mpv`,
distributed as one Windows executable.

### 1. WinGet — ruled out, and not on a technical point

**Do not resubmit this without reading why it was declined.** The manifests in
`packaging/winget/` are correct and were never the problem: all ten validation
stages passed, the CLA was signed, and `winget validate` was clean against
schema 1.12.0.

It was declined on [content policy
2.2](https://learn.microsoft.com/windows/package-manager/package/windows-package-manager-policies#22-content-including-names-logos-original-and-third-party),
in [PR #426448](https://github.com/microsoft/winget-pkgs/pull/426448) on
26/09/2026. The repository asked for publicly verifiable documentation that the
bundled AniKoto / AniZone / HiAnime integrations are authorised by those
providers and the content rights holders. There is none, so the submission was
withdrawn on 01/10/2026 rather than claim otherwise.

The offered alternative was to remove the provider and resolver layer from the
published package. That was declined from this side: it takes `play`, `sources`,
`download` and `prefetch` with it, leaving a binary that cannot play an episode
while carrying a name and documentation that say it can. Removing the bundled
providers while keeping the plugin interface they load through would invite
users to put them straight back, which is not the spirit of the policy either.

So Windows reach comes from Scoop and Chocolatey. The one thing that could
honestly go to WinGet is a separate, narrower package — AniList search, list
sync, history and statistics, no providers — under its own identifier.

### 2. Scoop — same day, no gatekeeper

A Scoop bucket is a git repo **you** own, so publishing is a commit. Create
`Anime123450/scoop-anime-sh`, drop `packaging/scoop/anime-sh.json` in a
`bucket/` folder, and it is installable immediately. `checkver`/`autoupdate` in
that manifest mean Scoop can follow new GitHub releases without you touching it
again.

Scoop also handles the external tools properly — `scoop install mpv ffmpeg` —
and it is what this machine already uses for both.

With WinGet out, Scoop is the primary Windows route and Chocolatey the reach.

### 3. PyPI — keep it

Already published, and still the best route for anyone who has Python, `uv` or
`pipx`, and the only one that covers macOS and Linux. The executable is a
Windows convenience, not a replacement.

### Not npm

npm would mean shipping a JavaScript package whose only job is to download this
binary, which adds Node.js as a prerequisite for a program that has nothing to
do with Node. It puts the tool in front of the wrong audience and adds a moving
part between the user and the file. The same argument rules out Docker here: a
TUI that has to drive a local video player is a bad fit for a container.

### Later, if it is wanted elsewhere

- **Homebrew** (macOS/Linux) — a tap installing from PyPI, or a formula.
- **AUR** (Arch) — a `PKGBUILD` depending on `python-*` and `mpv`.

Both are worth doing only once someone asks; PyPI already serves those platforms.

## What must never be bundled

`mpv` is GPL. Redistributing the binary inside a release carries source-offer
obligations, and the package managers already solve the problem properly by
declaring it as a dependency. `ffmpeg` is left out for size as well as licence —
it is only needed by `anime download`.
