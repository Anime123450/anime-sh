# anime-sh UI, unified settings, and VLC playback design

**Date:** 2026-10-11

**Status:** Draft for written-spec approval. Ani approved the overall direction on 2026-10-11; the written specification remains subject to review before implementation planning.

## 1. Purpose

Audit anime-sh's existing terminal UI, make only evidence-backed usability and visual corrections, consolidate user-facing settings into one category-tabbed screen, and add VLC as an optional playback backend while keeping mpv as the default.

The work proceeds in this order:

1. Audit current UI and interactions; fix only reproducible, audit-proven issues.
2. Build the unified Settings screen.
3. Add and verify the VLC backend.

## 2. Existing state and integration points

The inspected repository is `master` at `25213b6`, with a clean worktree. The app uses Python and Textual. `AnimeShApp` currently has separate Theme and Providers modal screens. Theme preview applies immediately and Escape restores the starting theme. Provider enable/disable persists through the existing config setter, and the screen explicitly says the change takes effect on next launch. The provider screen prevents disabling the last enabled provider.

The current configuration already has a player name (mpv by default), player arguments, playback preferences, UI preferences, provider/resolver settings, and a download directory. `Container` constructs the player selected by configuration and injects `PlaybackService` into the TUI. Preserve this composition-root boundary: add an injected `SettingsService` for typed reads/updates and a `PlayerProvider` resolved from validated configuration at each new playback session. Settings widgets must not instantiate `MpvPlayer` or `VlcPlayer` directly. Persist a playback choice first, then have the provider resolve that backend on the next play; provider/resolver registry changes remain next-launch because the registry is currently built once.

`PlaybackService` already owns the shared behavior that VLC should reuse: resume from saved position; periodic progress saves; completion tracking; intro/outro skipping through `PlaybackHandle.seek`; and auto-next only after a completed natural end. The player protocol reports position, duration, paused state, end-of-file state, and an optional end reason. The mpv implementation uses JSON IPC.

The previous UI-test baseline was reported as 144 passing tests, but it has not been freshly rerun for this specification. The baseline must be rerun before implementation changes.

## 3. Decisions approved by Ani

- Preserve the poster-first visual direction; do not redesign the home screen wholesale.
- Polish only issues proven by the audit.
- Use one Settings screen with category tabs.
- Add VLC as a selectable desktop player; retain mpv as the default.
- Use a VLC desktop process controlled through a loopback interface.
- Aim for resume/progress, skip seeking, and auto-next where supported.
- For VLC auto-next, use a best-effort near-end heuristic. It may advance after a manual stop near the end; this limitation must be visible in documentation and tested.

## 4. UI audit and evidence-backed polish

### Coverage

Inspect Home/Search, Detail, Sources, My List (when linked), Help, and the existing Theme and Providers flows. Exercise normal navigation and back/escape behavior, selection, keyboard focus, scroll overflow, mouse-accessible controls already presented by the UI, modal dismissal, and resize behavior.

Cover the meaningful states: initial/loading, empty results, no library entries, missing posters, provider unavailable/disabled, network or service errors, long titles/synopses, and populated lists. Check at 80×24 and 120×40 terminal sizes. Review the existing design guidance in `docs/DESIGN.md` and `docs/ENGINEERING_STANDARDS.md` before changing presentation.

### Acceptance rule

Record each proposed correction with a reproducible route/state and the observed defect. Fix only findings that can be reproduced or demonstrated by a focused UI test. Do not claim visual defects from the earlier SVG capture; that capture was not successfully reviewed. Do not change the poster-first composition, remove existing information, or silently rebind existing shortcuts. Add or update tests for corrected behavior and re-check affected screens at both terminal sizes.

## 5. Unified Settings screen

Create one non-modal Settings screen with a compact row of category tabs, keyboard-accessible left/right movement, clear focus, and the app's existing theme tokens. Avoid a permanent side rail so the screen remains usable at 80×24. Add a Settings entry in the global bindings after the audit checks for conflicts; retain `t` and `p` as direct entry points to Appearance and Sources so existing muscle memory continues to work. Update Help to show the new route.

### Categories

- **Appearance:** theme with the current live-preview / Escape-to-revert behavior; UI episode display and density options already represented in configuration.
- **Playback:** mpv/VLC selection (mpv remains the default), audio, quality, auto-next, intro/outro skip, and prefer-local-downloads options. Show whether VLC was detected and the executable path being used. Allow an explicit VLC path override, but do not hard-code Ani's machine path into the portable default.
- **Sources:** provider and resolver enabled/disabled state, with the existing at-least-one-provider constraint and a visible next-launch effect label. Keep registry priority behavior; provider reordering is not part of this change unless the audit proves it is necessary.
- **Downloads:** download destination with path validation and a clear explanation of when the change applies.

Do not put raw player arguments, provider timeouts, concurrency tuning, or custom host configuration in the primary screen. They remain available through the existing CLI/config path unless a later approved design adds a safe editor.

### Persistence and effective timing

The injected `SettingsService` owns typed validation and the shared config-writing path; do not hand-edit TOML from widgets. Apply runtime changes only after persistence succeeds. Surface save/validation errors, preserve the last valid value on failure, and avoid partial updates. Never silently switch to mpv when VLC was selected but is missing or failed to start.

- Theme preview is live; pressing Enter persists the chosen theme; Escape restores the original theme.
- UI display choices update the current TUI after save.
- Playback choices apply to the next playback session; they do not hot-swap an active player.
- Provider/resolver enablement applies next launch, matching current behavior.
- Download destination applies to the next download job.

The config schema gives CLI flags and environment variables precedence over the config file. When an override controls a displayed setting, identify the source and treat that field as read-only for the current run; do not imply that changing the file value changes the effective runtime value.

## 6. VLC playback adapter

Add an infrastructure adapter implementing the existing player and playback-handle ports. Keep playback orchestration in `PlaybackService`; do not duplicate progress, skip, or episode-selection logic in the VLC adapter.

### Discovery and process lifecycle

Resolve VLC by optional configured path, PATH, then documented platform-standard install locations. If none is available, show VLC as unavailable and give an actionable error if selected. Ani's current Windows machine has VLC 3.0.23 at `C:\Program Files\VideoLAN\VLC\vlc.exe`; use it for the integration smoke test, not as a cross-platform default.

Launch a dedicated VLC desktop process with its HTTP interface enabled on `127.0.0.1`, using a free ephemeral port and a newly generated high-entropy password for that run. Do not use the default fixed port/password, expose the interface on a LAN address, persist its password, or log it. Treat the temporary password as protection against other local clients, not as a boundary against the same OS user, who may be able to inspect process arguments. Ensure the invocation creates an isolated VLC instance rather than forwarding media to an already-running VLC instance; verify the exact supported invocation against VLC 3.0.23. Do not request that VLC exit at media end: keep its control endpoint alive long enough to observe the stopped state. Wait for the loopback endpoint to become ready with a bounded timeout. On startup failure, timeout, cancellation, or normal completion, close only the process created by this adapter and release its client resources. Never kill unrelated VLC processes.

The official VLC 3.0 documentation describes enabling the HTTP interface with `--extraintf=http`, host, port, and password options. The implementation must bind to loopback rather than the documentation example's LAN-wide `0.0.0.0` address. See the VLC 3.0 [HTTP interface setup](https://docs.videolan.me/vlc-user/desktop/3.0/en/gettingstarted/setup/linux/archlinux.html).

### Control and event mapping

Use the HTTP status endpoint to poll state, time, position, and duration, and the HTTP request commands for pause, seek, and stop. Map those values into the existing playback-event contract. Preserve unknown duration as zero; do not invent a duration or mark an unknown-duration stream complete. Seek/resume and skip actions must use the same handle so `PlaybackService` remains backend-independent.

The VLC 3.0 HTTP request implementation exposes status fields including `state`, `time`, `position`, and `length`; it also handles seek and stop commands. The status payload does not include a dedicated natural-EOF reason, and an explicit stop also changes playlist state. See the official [VLC 3.0 HTTP request module](https://github.com/videolan/vlc-3.0/blob/master/share/lua/intf/modules/httprequests.lua).

Preserve current stream requirements: pass supported User-Agent and Referer values and attach subtitle tracks. VLC's HTTP access module reads `http-user-agent` and `http-referrer`; see the official [VLC 3.0 HTTP access module](https://github.com/videolan/vlc-3.0/blob/master/modules/access/http.c). If a stream supplies a custom header that the adapter cannot preserve with a verified VLC mechanism, fail that attempt with an actionable `PlayerError`; never silently discard required headers. Do not add a general-purpose local media proxy in this version.

### Resume, progress, skips, and auto-next

- Open the selected stream, wait until the control endpoint has a valid input, then seek to the saved position. Do not count pre-start polling as confirmed playback.
- Emit regular position/duration/paused events so the existing progress-save cadence and resume store continue to work.
- Let the existing playback service perform intro/outro skipping using `PlaybackHandle.seek`.
- Keep the existing completion threshold for watch-history completion.
- For VLC auto-next, infer natural completion only on a transition from `playing` to `stopped`, with known positive duration and `last_position >= duration - max(5 seconds, 2% of duration)`, and only when the adapter did not request the stop. Emit an end event with reason `eof` in that case; otherwise report a stop/quit. This is explicitly best-effort: a manual stop inside that near-end window can trigger auto-next. Unknown duration or process exit without a qualifying status transition must not trigger auto-next.

## 7. Verification plan

### Automated tests

- Re-run the full existing test suite and capture the actual count before changes.
- Settings: category navigation, field validation, config persistence, save failure rollback, next-launch label, at-least-one-provider guard, retained `t`/`p` entry points, and environment/CLI override source plus read-only behavior.
- Player factory: mpv default, VLC selection, missing executable, invalid path, and no silent fallback.
- VLC adapter: authentication and endpoint readiness, JSON parsing, paused/playing/stopped mapping, resume seek, manual seek, skip seek, custom-header refusal, timeout, port conflict retry, process exit, and cleanup on every failure path.
- Auto-next: transition inside/outside the near-end threshold, unknown duration, user-requested stop, process exit, and the documented manual-stop-near-end false-positive case.
- Regression: mpv behavior and existing playback-service tests remain unchanged except for shared protocol coverage.

### Runtime checks

On Ani's Windows PC with VLC 3.0.23, use a short local test file and a local test HTTP fixture—not an anime provider—to verify desktop launch, loopback-only control, status polling, seek/pause/stop, subtitle attachment if a fixture is available, resume, cleanup, and the auto-next heuristic. Verify no unrelated VLC process is affected. Exercise the TUI at 80×24 and 120×40 and revisit every audit-proven change.

## 8. Explicit non-goals and risks

- No wholesale home-screen redesign or speculative visual cleanup.
- No replacement of mpv or change to its default status.
- No embedded VLC video surface; VLC remains its own desktop window.
- No LAN-accessible VLC control server or persistent HTTP password.
- No silent loss of stream headers and no silent mpv fallback.
- Best-effort VLC EOF detection has a known false-positive window near the end; the selected threshold and limitation must remain documented.
- Custom VLC behavior, stream/header compatibility, and the dedicated-instance invocation must be tested against the installed VLC 3.0.23 before claiming parity.
