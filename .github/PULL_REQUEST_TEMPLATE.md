## What this changes

<!-- The behaviour, in one or two sentences. If it fixes a bug, describe what
     the bug looked like from the user's seat, not just where the code was. -->

## Why

<!-- The reasoning. If a non-obvious choice was forced by something real — a
     provider quirk, a platform rule, a bug that bit us — say so here and leave
     a comment in the code saying it too. That comment is what stops the next
     person simplifying the fix back into the bug. -->

## Checklist

From [`docs/ENGINEERING_STANDARDS.md` §4](../blob/master/docs/ENGINEERING_STANDARDS.md#4-pull-request-checklist).
Strike out anything that genuinely does not apply.

- [ ] Regression test added, and **verified to fail when the fix is reverted**
- [ ] Does this bug class exist elsewhere? (searched, and either fixed or ruled out)
- [ ] Docstrings/comments describing changed behaviour updated
- [ ] README / `--help` updated if a command, option or default changed
- [ ] User-facing strings say what happened **and** what to do next
- [ ] New config keys validated on write, lenient on read
- [ ] Text from users or the network is NFKC-folded before parsing
- [ ] Any generated filesystem name tested for collisions and length
- [ ] New resources have an owner that closes them
- [ ] `uv run pytest -q` and `uv run lint-imports` pass
