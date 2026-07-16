---
title: "[canary] {{ env.PROVIDER }} provider is failing"
labels: provider-broken, canary
---

The nightly canary could not complete the read path
(match → episodes → candidates) for the **{{ env.PROVIDER }}** provider against
the real site.

This is expected to happen periodically — providers change their markup, rotate
endpoints, or go behind new anti-bot checks. It is a *degraded* state, not an
outage: other providers still serve, and metadata/library keep working.

**To fix**

1. Reproduce locally: `uv run python scripts/canary.py --provider {{ env.PROVIDER }}`
2. Compare the live responses against the provider's parser in
   `src/anime_sh/providers/{{ env.PROVIDER }}/`.
3. Regenerate any fixtures and re-run `uv run pytest`.

Run: {{ env.GITHUB_SERVER_URL }}/{{ env.GITHUB_REPOSITORY }}/actions/runs/{{ env.GITHUB_RUN_ID }}

_This issue is updated (not duplicated) by the canary while the provider stays broken._
