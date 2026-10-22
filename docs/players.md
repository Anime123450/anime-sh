# Players: mpv and VLC

anime-sh plays episodes in **mpv** by default. **VLC** is an optional
alternative, for when you already have it and would rather not install mpv, or
prefer its window. Everything that matters works with either: resume position,
progress and history, AniList sync, intro/outro skipping and auto-next.

## Choosing one

In the app: **`,`** opens Settings → **Playback** → *Player*. The panel below the
list shows where each player was found on this machine, or why it was not.

From the command line:

```bash
anime config set player.name vlc     # or mpv, the default
anime config get player.name
```

The choice applies to the **next episode you start**. An episode that is
already playing keeps its player, and auto-next keeps it for the episodes it
rolls on to. The same goes for quality, skipping and auto-next itself.

## Where VLC is looked for

1. `player.vlc_path`, if set. A file or the folder it sits in both work:

   ```bash
   anime config set player.vlc_path "D:\Apps\VLC\vlc.exe"
   anime config set player.vlc_path ""          # back to auto-detect
   ```

2. `vlc` on your `PATH`.
3. The standard install location: `%ProgramFiles%\VideoLAN\VLC\vlc.exe` (and the
   32-bit and per-user ones) on Windows, `/Applications/VLC.app` on macOS,
   `/usr/bin/vlc`, `/snap/bin/vlc` or the Flatpak export on Linux.

If you chose VLC and it is not found, or `vlc_path` points at nothing, playback
stops with that reason and how to fix it. **It never quietly falls back to
mpv**: a different player opening than the one you chose is a worse surprise
than an error.

## Settings you can't change from the app

A setting forced by a command-line flag or an `ANIME_SH_*` environment variable
(say `ANIME_SH_PLAYER__NAME=vlc`) is shown in Settings with its source and
locked. Editing the file would not change this run, so the screen does not
pretend it can. Unset the variable to edit it again.

`player.args` are **mpv options** and are passed to mpv only. VLC ignores them.

## How VLC is controlled, and what stays private

Every play starts **its own VLC process**. It is launched with
`--no-one-instance`, so the episode never opens in a VLC window you already had
open, and that child is the only process anime-sh ever pauses, stops or closes.

anime-sh talks to it through VLC's built-in HTTP control interface:

- bound to **127.0.0.1** only, so nothing else on your network can reach it;
- on a **port picked at run time** from the ones free at that moment (if
  another program takes it first, anime-sh retries up to three times);
- protected by a **new random password for each play**. That password lives in
  memory for that one episode. It is never written to your config, Settings or
  any log (the debug log shows `--http-password=***`). Control requests ignore
  any HTTP proxy you have configured, so they never leave the machine.

It is passed to VLC on its command line, so another program running as you
could read it from the process list while the episode plays. That is the same
trust boundary as everything else in your user account.

## What a source can ask of VLC

| Stream needs | mpv | VLC |
|---|---|---|
| User-Agent | ✓ | ✓ |
| Referer | ✓ | ✓ |
| Other HTTP headers (Origin, cookies, tokens) | ✓ | ✗ |
| Subtitle tracks | ✓ | ✓ (the default track loads first; extra tracks are added as inputs) |
| Resume position | ✓ | ✓ |

When a source needs a header VLC cannot send, anime-sh says which header and
does not start VLC. Playing without it would give you a broken stream or
nothing, with no hint why. Pick another source, or switch to mpv for that show.

## Auto-next with VLC is a best guess

mpv reports exactly when a file reaches its end. VLC's HTTP interface does not:
it only says playing, paused or stopped. anime-sh counts an episode as **finished
naturally** when VLC goes from playing to stopped with:

- a known duration, and
- the last position within **max(5 seconds, 2 % of the episode)** of the end
  (about 29 seconds of a 24-minute episode), and
- no stop requested by anime-sh itself.

So:

- Stopping VLC **inside** that last stretch looks like the episode ending, and the
  next one starts. That is the known limitation.
- Stopping earlier, closing the window, or a stream whose length VLC never
  learned never counts as finished and never rolls on.

Progress and history are saved the same way for both players: periodically
while you watch, and at the end.

## When VLC won't start

| What you see | What it means |
|---|---|
| *VLC not found on PATH or in the standard install location* | Install VLC, or set `player.vlc_path`. |
| *player.vlc_path is set to …, but there is no VLC there* | Fix or clear the path. |
| *VLC exited with code N before its control interface came up* | VLC refused the launch, often a broken install or a missing Lua plugin (the HTTP interface is Lua). Reinstalling VLC usually fixes it. |
| *its control interface did not answer within 15s* | VLC started but its HTTP interface never came up, often a very slow first start. anime-sh closes the VLC it started. Try again. |
| *couldn't get a free loopback port … after 3 tries* | Something else keeps taking the ports; rare. |
| *VLC can't send the HTTP header(s) this source needs* | See the table above. |

`anime doctor` checks mpv, ffmpeg and the rest. Settings → Playback shows what
VLC resolves to.

## Testing it for real

The unit tests fake VLC. `tests/integration/test_vlc_playback.py` drives a real
one: it generates a 20-second clip with ffmpeg, serves it locally and checks
start position, headers, pause/resume, seek, natural-end detection, that the
control port answers only on loopback and only with the password, and that only
its own VLC closes. It uses a temporary config, never yours.

```powershell
$env:ANIME_SH_LIVE_VLC = '1'; uv run pytest tests/integration/test_vlc_playback.py -q; Remove-Item Env:ANIME_SH_LIVE_VLC
```

Run over SSH on Windows, VLC opens in a session nobody can see. The test proves
the control and playback, not that a window appeared.
