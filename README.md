# wt

Per-project worktree manager. Provisions, inspects, and tears down git worktrees with their ports, databases, and tenant pointers all in sync.

## Why

Multi-worktree dev means juggling six variables per worktree:
1. Backend port
2. Frontend port
3. Per-worktree Postgres database
4. `ALLOWED_ORIGINS` matching the frontend port (CORS)
5. Clerk / auth keys carrying over from the primary worktree
6. Database migrations actually applied

Skip any of them and you get a misleading CORS error, a Clerk 401, or a half-provisioned worktree on a port nobody remembers. `wt` makes the six-variable setup one command, and `wt status` answers "which branch is on which port" in one place.

## Install

```bash
uv tool install --from git+https://github.com/LeonY117/wt wt
```

Now `wt` is on `$PATH`. Re-run to upgrade to the latest commit. For local development, install from a clone instead:

```bash
uv tool install --from /path/to/wt wt
```

## Use

Define a manifest at the project root (`.wt.yaml`) — see [brain-app's manifest](../../whitespace/brain-app/.wt.yaml) for a working example.

```bash
wt status                                  # table of all worktrees
wt new <shorthand> [<branch>] [--tenant X] # provision a fresh worktree
wt rm <shorthand>                          # tear down a single worktree
wt rm --auto                               # scan all, prompt y/N per eligible
wt tenants                                 # list available tenant packages
wt tenant <shorthand> <name>               # repoint a worktree to a different tenant
```

## Manifest

```yaml
project: brain-app                # human label (defaults to repo dir name)
worktree_prefix: brain-app--      # required — used to build & detect worktrees

services:
  - { name: backend,  default_port: 8000 }
  - { name: frontend, default_port: 3000 }

env_patches:                      # files to copy-and-patch on `wt new`
  - file: .env
    template: .env.example        # used if primary's file doesn't exist
    set:
      DATABASE_URL: "postgresql://leon@localhost:5432/{db}"
      ALLOWED_ORIGINS: "http://localhost:{frontend_port}"
      DEPLOYMENT_ROOT: "{tenant_path}"

db:                               # optional
  name_template: "brain_app_{shorthand_underscored}"
  migrate: "cd backend && uv run alembic upgrade head"

tenant:                           # optional — enables --tenant flag and `wt tenant`
  env_var: DEPLOYMENT_ROOT
  search_paths:
    - ~/dev/projects/whitespace/tenants
    - ~/dev/projects/whitespace/demo-data

cleanup:
  protected: [demo-main]          # shorthands `wt rm` will refuse
  gitignored_exclude:             # extends the built-in regenerable-junk list
    - "mockups/.cache"            # surfaced by default; suppress per project

import_hints:                     # how every wt command reads ports/db/tenant
  ports:                          # back from each worktree's .env files
    backend:
      file: frontend/.env.local
      key: NEXT_PUBLIC_BACKEND_URL
      pattern: "http://localhost:([0-9]+)"
  db:
    file: .env
    key: DATABASE_URL
    pattern: ".*/([^/?]+)"
  tenant:
    file: .env
    key: DEPLOYMENT_ROOT
```

### Placeholders

Available inside `env_patches.set` values and `db.name_template`:

- `{shorthand}`, `{shorthand_underscored}` — hyphens → underscores
- `{db}` — resolved from `db.name_template`
- `{<service>_port}` — one per service
- `{tenant}`, `{tenant_path}` — name + resolved absolute path
- `{project_root}` — repo root absolute path

## State model

There's no persistent registry — disk is the source of truth. Every `wt` command walks `git worktree list` and reads each worktree's `.env` files via the manifest's `import_hints` to recover ports, db names, and the current tenant pointer. A worktree that gets removed by hand (or never created by `wt`) shows up correctly on the next `wt status` without any reconcile step.

`wt new` provisions a worktree by creating files (git worktree + db + patched `.env`s); `wt tenant` rewrites the `.env`; `wt rm` deletes files. None of them maintain side metadata.

## Safety semantics

- **`wt rm` refuses** dirty / unpushed worktrees, branches without a resolved (merged or closed) PR, the primary worktree, and anything listed in `cleanup.protected`. A closed-without-merge PR is treated as a deliberate decision — the prompt surfaces a warning so you can verify the work is preserved elsewhere before confirming.
- **`wt rm` surfaces gitignored content** before the y/N prompt — mockups, drafts, scratch notes the safety floor can't see. Known regenerable junk (`node_modules`, `.venv`, `.next`, `__pycache__`, `.DS_Store`, `.env*`, etc.) is filtered out; project-specific patterns extend the list via `cleanup.gitignored_exclude`.
- **`wt new` rolls back** the worktree + DB if any later step fails. The branch is left in place — re-running `wt new <shorthand>` picks it up. (Branches are cheap; deleting a ref that may have been pushed is not.)
- **No `--force` anywhere.** Resolve the underlying issue rather than bypassing checks.
