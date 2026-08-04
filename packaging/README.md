# Standalone Windows build

Produces `anime.exe` — anime-sh with Python and every dependency baked in, for
people who have no Python and shouldn't need to care.

```bash
uv run --with pyinstaller python -m PyInstaller --onefile --name anime --console \
  --collect-all textual --collect-all textual_image --collect-all rich --collect-all typer \
  --collect-all curl_cffi --collect-all pydantic --collect-all aiosqlite --collect-all platformdirs \
  --collect-submodules anime_sh --collect-data anime_sh --copy-metadata anime-sh \
  --distpath dist/bundle --workpath build/pyi --specpath build/pyi \
  packaging/launcher.py
```

Two flags are not optional, and both fail *silently* if you drop them:

- **`--copy-metadata anime-sh`** — providers and resolvers are entry points, and
  entry points live in the package metadata. Without it the build runs, reports
  its version as `0.0.0+unknown`, and `doctor` says `providers: none installed`
  while still claiming the core looks healthy. Nothing can play.
- **`packaging/launcher.py` as the entry script** — `anime_sh/__main__.py` uses a
  relative import, which PyInstaller cannot execute as top-level
  (`attempted relative import with no known parent package`).

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

Put `anime.exe` beside `mpv.exe` in one folder with a `.bat` that prepends that
folder to `PATH`; mpv is then found without being installed. Roughly 145 MB
unzipped, 74 MB zipped. Leave `ffmpeg` out — it is 231 MB on its own and only
`anime download` needs it.

**Do not bundle mpv in a public release.** mpv is GPL, so redistributing the
binary carries source-offer obligations. Declaring it as a dependency is fine;
shipping it inside your artifact is not the same thing.
