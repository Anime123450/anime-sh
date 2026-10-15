# anime-sh UI audit — 2026-10-15

## Scope and baseline

Audited the existing TUI before the Settings/VLC implementation, at the repository state based on `e96dfdb`. No application code was changed during this audit.

The fresh baseline command was `uv run pytest -q`: **1 failed, 1002 passed, 8 skipped in 393.83s**. The failure was `tests/unit/test_autonext_unknown_total.py::test_it_still_stops_at_the_last_aired_episode`. Investigation found a date-sensitive fixture fixed to 2026-10-05; by 2026-10-15, `Anime.aired_through()` correctly treated that episode as aired. Following Ani's instruction to fix it only if within scope, the fixture was changed to use a future-relative date (test-only change). The affected file then passed: **4 passed in 0.10s**. A full rerun after that correction completed: **1003 passed, 8 skipped in 432.58s**. The initial failure remains recorded as the baseline result; another full run is still required after implementation.

Audited TUI pilot tests passed: `uv run pytest tests/tui/test_app.py tests/tui/test_focus_and_keys.py tests/tui/test_empty_search.py tests/tui/test_home_design.py tests/tui/test_providers_screen.py -q` → **52 passed in 57.46s**. This covers My List (`l`), provider-screen state and guards, search/empty-state behavior, focus/keys, home layout, and app routing. `tui_shots.py` does not render those two routes directly, so the pilot tests were used for them.

## Captures inspected

Each capture has both an SVG and plain-text compositor export. The SVGs were rendered to contact sheets for visual review; text exports were checked for exact cell-level truncation. A direct Textual Pilot probe also typed `zzz` into the focused no-result search field and inspected its state. Original artifacts are under `C:\Users\anime\AppData\Local\Temp\anime-sh-audit-2026-10-15`.

| State | 80×24 artifacts | 120×40 artifacts |
|---|---|---|
| Home | `home-80x24.svg`, `home-80x24.txt` | `home-120x40.svg`, `home-120x40.txt` |
| Search hit (`frie`) | `search-frie\search-80x24.svg`, `.txt` | `search-frie\search-120x40.svg`, `.txt` |
| Search miss (`zzz`) | `search-zzz\search-80x24.svg`, `.txt` | `search-zzz\search-120x40.svg`, `.txt` |
| Detail | `detail-80x24.svg`, `detail-80x24.txt` | `detail-120x40.svg`, `detail-120x40.txt` |
| Help | `help-80x24.svg`, `help-80x24.txt` | `help-120x40.svg`, `help-120x40.txt` |

The captures are 994×636 SVGs at 80×24 and 1482×1026 at 120×40. Review against `docs/DESIGN.md` found the compact home's omitted hero, shelf scrolling, truncated card titles, and detail/list content below the fold consistent with documented responsive/scroll behavior. Search hit and miss both show the intended result/empty states. No overlap or broken route was reproduced in the captured screens. The headless search text export showed a block before `zzz`, but a direct Pilot probe reported `value='zzz'`, `cursor_position=3`, and `focused=True`; the same block remained in the headless export. That export discrepancy does not establish a runtime focus/cursor defect, so it is excluded from code changes. The help annotation below is the only finding. Exact finding artifacts:

- Help: `C:/Users/anime/AppData/Local/Temp/anime-sh-audit-2026-10-15/help-120x40.txt` and `help-120x40.svg`.

## Findings

### 1. Help annotation is horizontally clipped

- **State/size:** Help modal, 120×40.
- **Reproduction:** Open help with `?` and inspect the explanatory sentence below the key list.
- **Expected:** The full sentence, “The large panel at the top always describes the poster under the cursor.”, is visible.
- **Observed:** The text export ends the row at “...poster under the”; the next row starts “Dimmed episodes have not aired yet.” The final word “cursor.” is not rendered. The source string contains that word, so this is a rendering/layout truncation, not missing copy.
- **Artifacts:** `...\help-120x40.txt` (rows 30–31) and `...\help-120x40.svg`.
- **Regression test to add:** `tests/tui/test_ui_audit_regressions.py::test_help_description_wraps_to_fit_modal`.

## Task-1 disposition

One reproducible, narrowly scoped UI defect was found: the help annotation is horizontally clipped. No stylistic differences were logged as defects. The next audit task should add the regression test first and fix only this behavior; the design direction and unaffected screens remain unchanged.