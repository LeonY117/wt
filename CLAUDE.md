# wt — agent orientation

Per-project worktree manager. See `README.md` for the user-facing docs. This file is the orientation for agents editing the tool itself.

## Layout

```
src/wt/
  cli.py            — Typer app, top-level command wiring
  manifest.py       — Pydantic models for .wt.yaml
  types.py          — Worktree model + PRIMARY constant (in-memory only)
  project.py        — Discovery: walk-up for .wt.yaml; Project.worktrees() / find() read disk
  envfile.py        — Minimal .env reader + surgical patcher (never logs values)
  claude_state.py   — Path munging + move-only Claude session/memory rescue
  ports.py          — allocate_port, is_port_bound (uses lsof)
  placeholders.py   — Resolve {db}, {<service>_port}, {tenant_path}, etc.
  tenant.py         — List + resolve tenant packages
  importer.py       — derive_worktrees: walk git + read each worktree's .env files
  commands/
    status.py       — wt status
    new.py          — wt new (provision)
    rm.py           — wt rm (teardown; mirrors the old bash script)
    rescue.py       — wt rescue orphan scan + apply flow
    tenant.py       — wt tenant + wt tenants
```

## State model

No persistent registry. `Project.worktrees()` is the single read API — it walks `git worktree list` and calls `importer.infer_worktree` to read each worktree's `.env` files. Every command takes this path; staleness is impossible by construction. Write paths (`new`, `tenant`, `rm`) mutate files only — no side index to keep in sync.

## Conventions

- **Never log .env values.** `envfile.py` only returns dicts; callers must avoid printing values. Ports and DB names are not secrets; Clerk keys, API keys, DB passwords are.
- **Atomic writes** for patched .env files (tempfile + rename).
- **Disk is the source of truth.** Don't add a side index, cache, or registry. If a command needs to know about a worktree, derive it from `git worktree list` + env files via `Project.worktrees()`.
- **Mixed worktree locations are normal.** `worktree_root` affects only the target of `wt new`; discovery and every later command remain based on `git worktree list`. Do not add migration or old/new transition logic.
- **Claude state is move-only.** Before removing a worktree, rescue Claude project dirs for both its root and subdirectory launches: move top-level `*.jsonl` sessions to the primary project dir without overwriting, then move all remaining state to `~/.wt-history/claude-state/`. Never unlink session or memory files, including under `--force`; an empty project directory may be removed.
- **Refuse by default; force is a stern, deliberate escape hatch.** `wt rm` mirrors the old bash script's safety floor. `wt rm --force` exists for the cases the floor can't anticipate, but it's gated behind a retype-the-worktree-name confirmation (see `_confirm_force`) — not a casual `--yes`. The primary and `cleanup.protected` worktrees are never force-removable; keep it that way. `wt new` still rolls back on failure rather than leaving half-provisioned state.
- **Trust the resolved PR over git's view of merge state.** Squash-merges leave the branch's commits out of `main`'s history and (after the remote branch is deleted + pruned) strip the upstream ref. `_check` keys off the `gh` PR state plus a tip-equals-PR-head test (`tip_is_pr_head`), not `git branch --merged`, so a clean squash-merge isn't mistaken for unmerged/unpushed work.
- **Manifest-driven.** Every project-specific behaviour goes through `.wt.yaml`. If brain-app needs special handling that wouldn't fit any other project, push back on the design before hard-coding it.
- **One command per file** under `commands/`. Keep `cli.py` thin — argparse-ish wiring only.

## Manifest placement schema

`worktree_prefix` remains required for legacy sibling naming and shorthand
derivation. `worktree_root` is optional:

```yaml
worktree_prefix: brain-app--
worktree_root: ../brain-app--worktrees  # absolute, primary-relative, or ~/...
```

With `worktree_root`, `wt new foo` creates `<resolved-root>/foo` without adding
the prefix and creates missing parent directories. Without it, the target remains
`primary.parent / f"{worktree_prefix}{shorthand}"`. Relative values are resolved
from the primary worktree root. Existing worktrees in either layout are handled
together through git discovery.

## Removal and rescue

`wt rm` refuses when `lsof -a -d cwd` finds a live process rooted anywhere
inside the target worktree and prints the process name/PID. `--force` can bypass
that reason only through the existing retype gate. After confirmation and before
`git worktree remove`, Claude state rescue always runs, including under force.

`wt rescue` scans Claude project directories matching the primary's munged path
prefix and determines orphanhood by excluding the munged primary, every path
currently returned by `git worktree list`, and every project dir prefixed by a
live worktree (a subdirectory launch). It is a dry run unless `--apply` is passed.
Apply uses the same move-and-archive primitive as `wt rm`.

## Adding a command

1. New file under `src/wt/commands/`.
2. Export a `run(...)` function that returns an exit code.
3. Wire it into `cli.py` as a `@app.command()`.
4. Update the command table in `README.md`.

## Testing

Run the automated suite with `uv run pytest`. The optional manual acceptance path is:
- `wt status` on brain-app (read-only, safe to run).
- `wt new <throwaway>` in a scratch worktree (verify ports/DB/env patches).
- `wt rm <throwaway>` (verify safety checks + cleanup).

Tests live under `tests/` and use `pytest` + `tmp_path`; git operations run against fixture repos, while process and external-service shell-outs should be monkeypatched where appropriate.
