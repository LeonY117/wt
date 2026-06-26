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
wt rm <shorthand> --force                  # bypass safety refusals (stern confirm)
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
  checklist:                      # extra "did you clean this up?" reminders,
    - "demo-data/ copied out?"    # appended to the built-in cleanup checklist

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
- **Squash-merges are recognised.** When the worktree's tip is exactly the resolved PR's head commit, `wt rm` trusts the merge and skips the upstream / unpushed-commit checks. That's the squash-merge path: GitHub squashes the branch into one new commit and auto-deletes the branch, so the original commits never land in `main`'s history and the remote branch (and its tracking ref, after a prune) is gone — which would otherwise read as "no upstream" and wrongly block a cleanly-merged worktree. A tip that has moved *past* the merged head still refuses (it carries un-captured local work).
- **`wt rm` surfaces gitignored content** before the prompt — mockups, drafts, scratch notes the safety floor can't see — and prints a **cleanup checklist** reminding you to migrate folders (mockups, notes, exports) out before the directory is destroyed. Known regenerable junk (`node_modules`, `.venv`, `.next`, `__pycache__`, `.DS_Store`, `.env*`, etc.) is filtered from the gitignored sweep; extend it via `cleanup.gitignored_exclude`, and add project-specific checklist reminders via `cleanup.checklist`.
- **`wt new` rolls back** the worktree + DB if any later step fails. The branch is left in place — re-running `wt new <shorthand>` picks it up. (Branches are cheap; deleting a ref that may have been pushed is not.)
- **`--force` is the deliberate escape hatch.** `wt rm <shorthand> --force` bypasses the dirty / unpushed / unmerged refusals, but only behind a stern gate: it prints every bypassed reason plus the cleanup checklist, then makes you **retype the worktree's exact name** to proceed (`--yes` does not skip this; on a non-interactive run it aborts and prints the `echo '<name>' | wt rm <name> --force` one-liner). The primary and `cleanup.protected` worktrees are never force-removable.
