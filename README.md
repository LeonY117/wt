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
uv tool install --from ~/dev/projects/tools/wt wt
```

Now `wt` is on `$PATH`. Re-run after `git pull` to pick up code changes.

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

import_hints:                     # used by `wt status` on first run only
  ports:
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

## Registry

Per project at `~/.config/wt/<project>.json`. Source of truth for ports, DB names, and current tenant pointer. Atomic writes (tempfile + rename). On first `wt status`, missing worktrees are auto-imported by parsing `.env` files against the manifest's `import_hints`.

## Safety semantics

- **`wt rm` refuses** dirty / unpushed / unmerged worktrees, the primary worktree, and anything listed in `cleanup.protected`.
- **`wt new` rolls back** the worktree + DB if any later step fails.
- **No `--force` anywhere.** Resolve the underlying issue rather than bypassing checks.
