# Implementation Plan: anime-sh UI Settings + VLC

> For agentic workers: execute this plan task-by-task. Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` if available; keep the reviewed task boundaries and run the exact checks below.

**Status:** Approved for implementation in the current chat; Ani directed inline completion on 2026-10-15.
**Approved design:** `docs/superpowers/specs/2026-10-11-anime-sh-ui-settings-vlc-design.md`
**Repository:** `C:\Users\anime\dev\anime-sh` on `master`; do not push.

## Goal

Complete an evidence-led TUI audit and polish, replace separate Theme/Providers views with one categorized Settings screen, and add selectable VLC playback without changing mpv as the default.

## Design summary

Keep the Textual UI behind the existing app-service boundary. `SettingsService` owns validated effective configuration, source attribution, and persistence; a `PlayerProvider` port reports availability and creates the selected player from validated settings. At each new playback session, `PlaybackService` resolves one player and one immutable settings snapshot. VLC is an infrastructure adapter: it launches an isolated VLC process, controls that process through authenticated loopback HTTP, and emits the existing neutral playback events so resume, progress, skip, completion, and auto-next stay in the shared playback flow.

## Tech stack

Python 3.11+, Textual 8+, Pydantic Settings, `httpx`, asyncio/subprocess, pytest, and the existing import-linter. No new Python dependencies.

## Global constraints

- Keep `mpv` as the schema default and preserve current mpv behavior. A selected but unavailable VLC must produce an actionable error; never silently fall back to mpv.
- Support Ani's VLC 3.0.23 on Windows (`C:\Program Files\VideoLAN\VLC\vlc.exe`) while resolving VLC portably: configured path, `PATH`, then documented platform-standard locations. Confirm command-line flags against the installed VLC before relying on them.
- One full Settings screen with compact category tabs: Appearance, Playback, Sources, Downloads. Preserve `t` and `p` as shortcuts into Appearance and Sources. Use `comma` for the new global Settings shortcut; the installed Textual accepts the `comma` binding name and the current global bindings (`q`, `/`, `l`, `t`, `p`, `?`, Escape) do not use it. No side rail or unapproved redesign.
- Preserve precedence `CLI > environment > config file > defaults`. Show the effective value and its source; CLI/environment-overridden values are read-only in the TUI.
- Apply appearance changes on save/preview as specified; playback settings apply to the next session, source enablement on next launch, and download destination on the next job. Do not hot-swap an active playback session or move an active download.
- VLC control binds only to `127.0.0.1` on a runtime-selected port, uses a fresh high-entropy per-run password, and never writes or logs that password. Never bind to a LAN interface or terminate an unrelated VLC process. Clean up only the subprocess this adapter launched.
- Preserve supported User-Agent, Referer, and subtitle behavior. Reject unsupported custom stream headers before launching VLC; do not silently discard them.
- VLC auto-next is best-effort: treat a transition from playing to stopped as end-of-episode only when duration is known, stop was not requested by the adapter, and `position >= duration - max(5 seconds, 2% of duration)`. Unknown duration never auto-advances. A user stop inside the threshold can still be a false positive; document and test this limitation.
- Keep legacy theme/provider modules as small compatibility shims if needed; do not delete files in this change. Do not add secrets, new dependencies, or unrelated settings.
- Do not commit or push implementation changes until this plan is approved. Commits may be made locally per task; never push.

## Review focus

1. CLI/environment override values remain visible and read-only, with no TOML write: `test_overrides_are_visible_read_only_and_leave_toml_unchanged`.
2. Missing VLC never selects mpv: `test_missing_vlc_never_falls_back_to_mpv`.
3. VLC startup/readiness failure cleans up only its owned process and exposes no password: `test_vlc_startup_timeout_cleans_up_owned_process`.
4. Stream headers/subtitles are preserved or explicitly rejected before launch: `test_vlc_preserves_supported_headers_and_subtitles` and `test_unsupported_custom_headers_fail_before_launch`.
5. Completion is not inferred from unknown duration or an early stop; test the exact near-end threshold and its manual-stop false-positive limitation: `test_vlc_eof_uses_near_end_threshold` and `test_manual_stop_near_end_is_best_effort`.

---

## Task 1 — Establish the baseline and audit the current UI

**Files**
- Create `docs/superpowers/audits/2026-10-15-anime-sh-ui-audit.md`.
- Do not modify application code in this task.

**Steps**
1. From the repository root, run `uv run pytest -q` before any application edits and record the actual result. The earlier baseline was 144 TUI tests passing on 2026-10-11; the fresh `uv run pytest tests/tui -q` attempt on 2026-10-15 timed out at 300 seconds, so it is not a passing baseline. If the full run also times out, identify the slow/blocked test and record the limit rather than claiming a pass.
2. Capture the current screens at 80x24 and 120x40. In PowerShell from the repository root:
   ```powershell
   $shots = Join-Path $env:TEMP 'anime-sh-audit-2026-10-15'
   uv run python scripts/tui_shots.py $shots --screen home --sizes 80x24,120x40
   uv run python scripts/tui_shots.py $shots --screen search --query frie --sizes 80x24,120x40
   uv run python scripts/tui_shots.py $shots --screen search --query zzz --sizes 80x24,120x40
   uv run python scripts/tui_shots.py $shots --screen detail --sizes 80x24,120x40
   uv run python scripts/tui_shots.py $shots --screen help --sizes 80x24,120x40
   ```
   Inspect both the SVG and text export for each capture. Exercise Sources and My List through existing Textual pilot tests because `tui_shots.py` does not currently render those routes.
3. Compare against `docs/DESIGN.md`, current behavior, and the existing tests under `tests/tui/`. Record only reproducible layout, keyboard, focus, or state defects—not personal styling preferences.
4. For each finding, record screen/state, terminal size, reproduction steps, expected/observed behavior, artifact path, and the exact regression-test name that would prove a fix. If there are no findings, say so explicitly.

**Acceptance**
- The audit report is complete and evidence-linked; baseline status is truthful.
- No UI changes are made merely because they look different from a preferred mockup.

**Commit:** `docs: record anime-sh UI audit`

## Task 2 — Fix only reproduced audit findings

**Files**
- Create `tests/tui/test_ui_audit_regressions.py` only if the audit found issues needing regression coverage.
- Modify only the `src/anime_sh/tui/` files named by the audit report, plus the regression test file.

**Steps**
1. For each finding in the audit report, add one focused failing Textual pilot test named exactly as recorded there. Assert the observable behavior or rendered layout contract, not implementation details.
2. Run the named test first and confirm it fails for the recorded reason.
3. Apply the smallest fix within the approved TUI scope; retain the poster-first direction and existing global shortcuts.
4. Re-run the focused test, then:
   ```powershell
   uv run pytest tests/tui/test_ui_audit_regressions.py tests/tui/test_home_design.py tests/tui/test_focus_and_keys.py tests/tui/test_episode_layouts.py tests/tui/test_themes.py tests/tui/test_providers_screen.py -q
   ```
   If the regression file was not created, omit it from this command.
5. Re-capture only affected screens with `scripts/tui_shots.py` at 80x24 and 120x40; inspect text and SVG outputs and record the result in the audit report.
6. If Task 1 found no reproducible issue, skip code changes and close this task by recording “no code changes required” in the audit report.

**Acceptance**
- Every code change maps to a reproduced audit finding and a regression test or before/after visual artifact.
- No audit finding is silently dropped; no unrelated screen is redesigned.

**Commit:** `fix: apply evidence-backed TUI polish` (or no code commit if there are no findings).

## Task 3 — Add typed settings persistence and player-provider contracts

**Files**
- Create `src/anime_sh/app/settings.py`.
- Modify `src/anime_sh/config/schema.py` and `src/anime_sh/config/loader.py`.
- Modify `src/anime_sh/domain/models.py` and `src/anime_sh/domain/ports.py`.
- Create `tests/unit/test_settings_service.py`; update `tests/unit/test_config_set.py`.

**Interfaces**
- Add immutable `SettingInfo(key, value, source, read_only)` with source in `cli`, `env`, `file`, or `default`.
- Add `SettingsService(config_path=None, cli_overrides=None)` with `snapshot()`, `inspect(dotted_key)`, and `update(dotted_key, string_value)`. `snapshot()` returns the validated effective `Config`; `inspect()` reports the same effective value plus its source; `update()` refuses overridden values and returns the post-write `SettingInfo`.
- Add `player.name` validation (`mpv` or `vlc`) with default `mpv`, plus an empty-by-default `player.vlc_path` override. Keep raw `player.args` out of the Settings UI.
- Add a small `PlayerInfo` value object and a domain `PlayerProvider` protocol with `status(...)` and `create(...)`; it must not import config or infrastructure modules.
- Make config writes atomic (temporary file in the same directory plus replace) after type coercion and validation, so a failed update leaves the last valid file intact.

**Steps and tests**
1. Add tests first: `test_player_name_defaults_to_mpv_and_rejects_unknown`, `test_setting_origin_follows_cli_env_file_default_precedence`, `test_overrides_are_visible_read_only_and_leave_toml_unchanged`, `test_invalid_update_preserves_original_toml`, and `test_replace_failure_preserves_original_toml`.
2. Run each new test and confirm the expected initial failure.
3. Implement source attribution using explicit CLI override keys, `ANIME_SH_<SECTION>__<FIELD>` environment names, raw TOML presence, and otherwise defaults. Return the effective value, not the lower-precedence file value.
4. Make update validate before write, reject CLI/env-locked fields without touching the file, and preserve list/boolean/path typing through the existing config setter.
5. Run:
   ```powershell
   uv run pytest tests/unit/test_settings_service.py tests/unit/test_config_set.py -q
   ```

**Acceptance**
- Default player is mpv; only mpv/VLC are accepted.
- Source order is CLI > env > file > default; overridden fields cannot be edited through the TUI.
- Invalid values and failed writes preserve the prior TOML content.
- `PlayerProvider` remains an app-facing port; no architecture-layer violation is introduced.

**Commit:** `feat: add typed settings service`

## Task 4 — Build the unified categorized Settings screen

**Files**
- Create `src/anime_sh/tui/screens/settings.py` and `tests/tui/test_settings_screen.py`.
- Modify `src/anime_sh/tui/app.py`, `src/anime_sh/tui/app.tcss`, `src/anime_sh/tui/screens/help.py`, `src/anime_sh/cli/container.py`, and `scripts/tui_shots.py`.
- Adapt `src/anime_sh/tui/screens/themes.py` and `src/anime_sh/tui/screens/providers.py` as thin compatibility shims; do not delete either file.
- Update `tests/tui/test_themes.py`, `tests/tui/test_providers_screen.py`, and `tests/tui/test_focus_and_keys.py` to exercise the unified screen.

**Screen contract**
- One non-modal `SettingsScreen` with the categories Appearance, Playback, Sources, and Downloads; category selection works with left/right and is visually compact at both audited terminal sizes.
- `comma` opens Settings; `t` opens Settings on Appearance; `p` opens it on Sources. Help/footer text names the entry points. All pre-existing bindings remain intact.
- Appearance edits theme, density, and episode layout; theme preview reverts on Escape and persists on Enter.
- Playback edits player, audio, quality, auto-next, intro/outro skip, and prefer-downloads. Show VLC availability/path through the injected `PlayerProvider`; do not instantiate a concrete player in a widget.
- Sources edits provider and resolver enabled state; retain the at-least-one-provider guard and label that changes apply next launch.
- Downloads edits `downloads.dir`; reject empty/NUL paths, expand `~`, and do not create directories until a download job.
- Every overridden value displays its source and is disabled. Save/validation errors are visible and leave the prior valid value unchanged.
- Persist through `SettingsService`, not direct `set_config_value` calls from widgets. Keep Sources and Downloads service effects at their documented next-launch/next-job boundary.

**Steps and tests**
1. Add pilot tests first: `test_comma_t_and_p_open_unified_settings_categories`, `test_settings_tabs_change_with_left_and_right`, `test_override_fields_show_source_and_are_disabled`, `test_provider_toggle_keeps_one_enabled`, `test_resolver_toggle_persists_through_settings_service`, `test_theme_preview_reverts_on_escape_and_saves_on_enter`, and `test_settings_save_failure_keeps_last_valid_value`.
2. Run the tests and confirm they fail against the old separate modal screens.
3. Implement `SettingsScreen`, inject `SettingsService`/`PlayerProvider` through `TuiServices`, route the old shortcuts into the correct category, update help and styles, and convert old screen modules into shims without deleting them.
4. Extend `scripts/tui_shots.py` with `--screen settings` and `--settings-category appearance|playback|sources|downloads`; use deterministic fake services and ensure both text and SVG exports include the selected category.
5. Run:
   ```powershell
   uv run pytest tests/tui/test_settings_screen.py tests/tui/test_themes.py tests/tui/test_providers_screen.py tests/tui/test_focus_and_keys.py -q
   $shots = Join-Path $env:TEMP 'anime-sh-settings-review'
   uv run python scripts/tui_shots.py $shots --screen settings --settings-category playback --sizes 80x24,120x40
   uv run python scripts/tui_shots.py $shots --screen settings --settings-category sources --sizes 80x24,120x40
   ```
   Inspect the generated SVG and text captures at both sizes.

**Acceptance**
- All categories work through one Settings screen; shortcuts open the intended category.
- No direct screen-to-config-file writes remain for settings in this scope.
- Overrides, save errors, provider guard, live theme preview, and compact layout are covered by tests.

**Commit:** `feat: add unified settings screen`

## Task 5 — Implement VLC discovery, loopback control, and player adapter

**Files**
- Create `src/anime_sh/infra/players/factory.py`, `src/anime_sh/infra/players/vlc.py`, and `src/anime_sh/infra/players/vlc_http.py`.
- Modify `src/anime_sh/infra/players/__init__.py` and `src/anime_sh/cli/container.py`.
- Create `tests/unit/test_player_provider.py`, `tests/unit/test_vlc_http.py`, and `tests/unit/test_vlc_player.py`.

**Steps and tests**
1. Add unit tests first: `test_player_provider_defaults_to_mpv`, `test_player_provider_prefers_configured_vlc_path`, `test_missing_vlc_never_falls_back_to_mpv`, `test_vlc_http_auth_and_status_parsing`, `test_vlc_loopback_auth_uses_ephemeral_port`, `test_vlc_port_collision_retries`, `test_vlc_startup_timeout_cleans_up_owned_process`, `test_vlc_preserves_supported_headers_and_subtitles`, and `test_unsupported_custom_headers_fail_before_launch`.
2. Verify the installed VLC 3.0.23 command-line help for the isolated-instance, HTTP interface, and subtitle flags before encoding them in the launcher. Keep only flags confirmed by that binary.
3. Implement path resolution in this order: explicit `player.vlc_path`, `PATH`, then standard install locations for Windows/macOS/Linux. `status()` is read-only and reports the resolved executable; `create()` returns mpv or VLC from the requested name and raises an actionable `PlayerError` if the requested backend is unavailable.
4. Launch VLC as an isolated child process. Select a runtime port, bind the HTTP interface to `127.0.0.1`, generate a fresh `secrets.token_urlsafe(32)` password, and poll an authenticated `requests/status.json` endpoint with a bounded readiness timeout. Retry a port collision at most three times. Do not write the password to config, settings, or logs.
5. Implement `VlcHttpClient` for authenticated status, pause, seek, and stop requests. Implement `VlcPlayer`/handle with explicit ownership of the child process; on failure/stop, clean up only that child.
6. Map supported User-Agent/Referer and subtitle tracks to VLC input options. Reject any remaining custom header before process creation with a message naming the unsupported behavior. Do not silently lose stream metadata.
7. Run:
   ```powershell
   uv run pytest tests/unit/test_player_provider.py tests/unit/test_vlc_http.py tests/unit/test_vlc_player.py -q
   ```

**Acceptance**
- VLC is isolated, loopback-only, authenticated, uses a runtime port and per-run secret, and never kills unrelated instances.
- Path override and discovery work; missing VLC does not fall back to mpv.
- HTTP readiness, status, pause, seek, stop, headers, subtitles, error cleanup, and secret-safe logging are covered without requiring VLC in unit tests.

**Commit:** `feat: add VLC playback backend`

## Task 6 — Resolve settings per playback/download job and integrate completion

**Files**
- Modify `src/anime_sh/app/playback.py`, `src/anime_sh/app/download.py`, and `src/anime_sh/cli/container.py`.
- Add or update focused tests in `tests/unit/test_playback.py`, `tests/unit/test_playback_robust.py`, `tests/unit/test_skip_autonext.py`, and create `tests/unit/test_download_dir_settings.py`.

**Steps and tests**
1. Add tests first: `test_settings_change_applies_to_next_playback_not_active_session`, `test_vlc_progress_resume_and_skip_use_shared_playback_flow`, `test_vlc_eof_uses_near_end_threshold`, `test_unknown_duration_never_autonexts`, `test_stop_outside_threshold_does_not_autonext`, `test_manual_stop_near_end_is_best_effort`, and `test_download_directory_change_applies_to_next_job`.
2. At the beginning of a new playback, snapshot the effective config once, create the selected player through `PlayerProvider`, and capture the playback settings for that session. Do not switch player or settings during the active session. Preserve the existing static-player injection path used by unit tests.
3. Route VLC position/duration/paused/stopped events through the existing `PlaybackService` progress-save, resume, skip, completion, and auto-next paths. Use the approved near-end threshold exactly; unknown duration and non-qualifying stops do not mark completion. Preserve the documented near-end manual-stop false-positive as best-effort behavior.
4. Make `DownloadService` accept a current-directory provider while preserving its lazy playback/proxy construction. Snapshot the directory once when a download job starts so a settings change affects the next job, not the active one; `destination()`/`local_path()` use the currently configured directory for subsequent jobs.
5. Run:
   ```powershell
   uv run pytest tests/unit/test_playback.py tests/unit/test_playback_robust.py tests/unit/test_skip_autonext.py tests/unit/test_download_dir_settings.py tests/unit/test_player_provider.py tests/unit/test_vlc_http.py tests/unit/test_vlc_player.py -q
   ```

**Acceptance**
- mpv regressions remain green; each new playback session uses the current effective player/settings.
- Resume/progress/skip/auto-next use the shared service; unknown duration never produces false completion.
- A download path update is effective on the next job and does not move an active download.

**Commit:** `feat: apply settings per playback and download session`

## Task 7 — Windows VLC smoke test, documentation, and final verification

**Files**
- Create opt-in `tests/integration/test_vlc_playback.py` and `docs/PLAYERS.md`.
- Modify `README.md` to link to `docs/PLAYERS.md`.
- Modify `scripts/tui_shots.py` only if Task 4's settings capture needs a final correction.

**Steps**
1. Add an integration test guarded by `ANIME_SH_LIVE_VLC=1` and skip it by default on CI. On Ani's Windows PC, generate a short local test MP4 with the already-installed `ffmpeg`, serve it from a local HTTP fixture that records request headers, then exercise real VLC status, resume start position, seek, pause/resume, stop, and cleanup. Use a temporary config path; do not read or modify Ani's real config.
2. Run the opt-in test on the PC with the configured `C:\Program Files\VideoLAN\VLC\vlc.exe`. Confirm the loopback HTTP control endpoint is reachable at `127.0.0.1` only and that the test process owns/cleans up its VLC child. If the SSH-launched test cannot prove a visible desktop window, record that limitation rather than claiming visible playback was verified; do not close any pre-existing VLC process.
3. Document player selection/default, path override, precedence/read-only behavior, loopback interface/password handling, supported stream metadata, startup errors, and the known near-end auto-next limitation in `docs/PLAYERS.md`; link it from `README.md`.
4. Run the Settings captures at both sizes for all four categories and inspect SVG plus text output. Then run:
   ```powershell
   uv run pytest tests/tui -q
   uv run pytest -q
   uv run lint-imports
   uv run pytest tests/integration/test_vlc_playback.py -q
   ```
   The last command is the default (skipped) path; also run the same test with `$env:ANIME_SH_LIVE_VLC='1'` on the Windows PC, then clear that temporary environment variable.
5. Check `git diff --check`, the complete test summary, and `git status --short --branch`. Do not push.

**Acceptance**
- Full suite and import-linter pass; the live VLC smoke passes on the target PC or its exact blocker is recorded.
- mpv remains default; Settings changes are correctly scoped; screenshots show no overflow at 80x24 or 120x40.
- No credentials/passwords are persisted or logged, no unrelated VLC processes are touched, and documentation states the known heuristic limitation.

**Commit:** `docs: document VLC playback and verification`

---

## Final completion criteria

- Every task acceptance above is verified from real test/tool output.
- The audit report states the exact reproduced findings or explicitly records none.
- The approved design spec remains the source of truth; any necessary deviation is raised to Ani before implementation expands scope.
- All implementation commits remain local on `master`; nothing is pushed or published.