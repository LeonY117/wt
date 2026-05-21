# wt — agent orientation

Per-project worktree manager. See `README.md` for the user-facing docs. This file is the orientation for agents editing the tool itself.

## Layout

```
src/wt/
  cli.py            — Typer app, top-level command wiring
  manifest.py       — Pydantic models for .wt.yaml
  registry.py       — JSON registry I/O, atomic writes, ~/.config/wt/<project>.json
  project.py        — Discovery: walk-up for .wt.yaml, link manifest + registry + primary worktree
  envfile.py        — Minimal .env reader + surgical patcher (never logs values)
  ports.py          — allocate_port, is_port_bound (uses lsof)
  placeholders.py   — Resolve {db}, {<service>_port}, {tenant_path}, etc.
  tenant.py         — List + resolve tenant packages
  importer.py       — Auto-import git-existing worktrees on first run
  commands/
    status.py       — wt status
    new.py          — wt new (provision)
    rm.py           — wt rm (teardown; mirrors the old bash script)
    tenant.py       — wt tenant + wt tenants
```

## Conventions

- **Never log .env values.** `envfile.py` only returns dicts; callers must avoid printing values. Ports and DB names are not secrets; Clerk keys, API keys, DB passwords are.
- **Atomic writes** for both registry and patched .env files (tempfile + rename).
- **Refuse, don't force.** `wt rm` mirrors the old bash script's safety floor — no `--force` flag exposed unless we genuinely need it. Same for `wt new`: roll back on failure, don't leave half-provisioned state.
- **Manifest-driven.** Every project-specific behaviour goes through `.wt.yaml`. If brain-app needs special handling that wouldn't fit any other project, push back on the design before hard-coding it.
- **One command per file** under `commands/`. Keep `cli.py` thin — argparse-ish wiring only.

## Adding a command

1. New file under `src/wt/commands/`.
2. Export a `run(...)` function that returns an exit code.
3. Wire it into `cli.py` as a `@app.command()`.
4. Update the command table in `README.md`.

## Testing

There's no test suite yet. The honest acceptance path is:
- `wt status` on brain-app (read-only, safe to run).
- `wt new <throwaway>` in a scratch worktree (verify ports/DB/env patches).
- `wt rm <throwaway>` (verify safety checks + cleanup).

If we add tests, they should live under `tests/` with `pytest` + `tmp_path` fixtures for the registry and env-patcher; the git/psql/lsof shells-out should be monkeypatched or run against a fixture repo.
