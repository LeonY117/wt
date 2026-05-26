"""`wt rm` — safely tear down worktrees.

A worktree is eligible when its branch has a most-recent PR that's MERGED
or CLOSED (either is an explicit human decision), the working tree is
clean, and the branch has no unpushed commits. Open PRs and never-PR'd
branches are refused.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from ..project import Project
from ..registry import PRIMARY, Worktree


console = Console()
err = Console(stderr=True)


# Substrings of gitignored paths that `wt rm` should *not* surface during its
# pre-removal sweep — known regenerable build junk and per-worktree env files.
# Extend per-project via `cleanup.gitignored_exclude` in `.wt.yaml`.
DEFAULT_GITIGNORED_EXCLUDES = (
    "node_modules",
    ".venv",
    ".next",
    ".turbo",
    ".ruff_cache",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".DS_Store",
    "tsbuildinfo",
    ".env",  # also catches .env.local, .env.staging, etc.
)


@dataclass
class WorktreeChecks:
    shorthand: str
    path: Path
    branch: str | None
    reasons: list[str] = field(default_factory=list)
    pr_state: str | None = None  # "MERGED" or "CLOSED"
    pr_resolved_at: str | None = None  # mergedAt for MERGED, closedAt for CLOSED
    merged_sha: str | None = None  # only set when pr_state == "MERGED"


def _git_at(path: Path, args: list[str]) -> str:
    r = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
    )
    return r.stdout if r.returncode == 0 else ""


def _gh_latest_pr(branch: str) -> tuple[str | None, str | None, str | None]:
    """Return (state, resolved_at, headRefOid) for the single most recent PR.

    state is one of "OPEN", "MERGED", "CLOSED", or None when no PR exists.
    resolved_at is mergedAt for MERGED, closedAt for CLOSED, otherwise None.

    Looking at the *most recent* PR matters: a branch may have had an old
    merged PR and then been reused for new work that's now open — that open
    PR is the active state, and the worktree shouldn't be removed.
    """
    r = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "all",
            "--limit",
            "1",
            "--json",
            "state,mergedAt,closedAt,headRefOid",
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return None, None, None
    import json

    try:
        prs = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return None, None, None
    if not prs:
        return None, None, None
    pr = prs[0]
    state = pr.get("state")
    head_sha = pr.get("headRefOid") or None
    if state == "MERGED":
        return "MERGED", pr.get("mergedAt") or None, head_sha
    if state == "CLOSED":
        return "CLOSED", pr.get("closedAt") or None, head_sha
    if state == "OPEN":
        return "OPEN", None, head_sha
    return None, None, None


def _check(project: Project, wt: Worktree) -> WorktreeChecks | None:
    """Run the merged/dirty/unpushed checks. Returns None if the worktree should
    be silently skipped (protected, primary, non-prefixed). Otherwise returns a
    WorktreeChecks with .reasons populated for any blockers (empty list = ok).
    """
    manifest = project.manifest
    path = Path(wt.path)

    if wt.shorthand == PRIMARY:
        return None
    if wt.shorthand in manifest.cleanup.protected:
        return None
    if not path.name.startswith(manifest.worktree_prefix):
        return None

    checks = WorktreeChecks(shorthand=wt.shorthand, path=path, branch=wt.branch)

    if not path.exists():
        checks.reasons.append("worktree directory missing on disk")
        return checks

    if wt.branch is None:
        checks.reasons.append("detached HEAD — skipping")
        return checks

    # PR-state check. A MERGED or CLOSED PR both count as a human decision
    # that the branch is done — either is safe. An OPEN PR means active work.
    # No PR at all means the work was never reviewed, so refuse.
    state, resolved_at, head_sha = _gh_latest_pr(wt.branch)
    checks.pr_state = state
    checks.pr_resolved_at = resolved_at
    if state == "MERGED":
        checks.merged_sha = head_sha
        # Catch the case where the branch was amended/force-pushed after the
        # merge — local tip would then carry post-merge work we'd silently lose.
        local_sha = _git_at(path, ["rev-parse", "HEAD"]).strip()
        if head_sha and local_sha and local_sha != head_sha:
            checks.reasons.append(
                f"local tip {local_sha[:7]} differs from merged SHA "
                f"{head_sha[:7]} (amended/force-pushed since merge?)"
            )
    elif state == "CLOSED":
        # No SHA-match check: closed-without-merge has no canonical "final"
        # SHA to compare against. The unpushed-commits check below still
        # catches local-only work.
        pass
    elif state == "OPEN":
        checks.reasons.append(f"open PR on remote for {wt.branch}")
    else:
        checks.reasons.append(f"no PR on remote for {wt.branch}")

    # Dirty / untracked.
    porcelain = _git_at(path, ["status", "--porcelain"]).strip()
    if porcelain:
        dirty_count = len(porcelain.splitlines())
        checks.reasons.append(f"{dirty_count} uncommitted/untracked file(s)")

    # Unpushed commits.
    upstream = _git_at(path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]).strip()
    if not upstream:
        checks.reasons.append(f"no upstream set for {wt.branch}")
    else:
        ahead = _git_at(path, ["rev-list", "--count", f"{upstream}..HEAD"]).strip()
        if ahead and ahead != "0":
            checks.reasons.append(f"{ahead} unpushed commit(s) vs {upstream}")

    return checks


def _gitignored_content(path: Path, extra_excludes: list[str]) -> list[str]:
    """List gitignored paths under `path` that aren't known regenerable junk.

    `git status --ignored --short` reports gitignored entries with a `!!`
    prefix. We filter out anything whose path contains a substring from the
    built-in default list (build outputs, caches, .env files) or from the
    project's `cleanup.gitignored_exclude`. What remains is content the user
    probably wants to know about before we delete the worktree directory —
    ad-hoc mockups, draft docs, scratch notes.
    """
    excludes = tuple(DEFAULT_GITIGNORED_EXCLUDES) + tuple(extra_excludes)
    raw = _git_at(path, ["status", "--ignored", "--short"])
    out: list[str] = []
    for line in raw.splitlines():
        if not line.startswith("!! "):
            continue
        entry = line[3:].strip()
        if not entry:
            continue
        if any(token in entry for token in excludes):
            continue
        out.append(entry)
    return out


def _db_exists(name: str) -> bool:
    r = subprocess.run(["psql", "-lqt"], capture_output=True, text=True)
    if r.returncode != 0:
        return False
    for line in r.stdout.splitlines():
        if line.split("|", 1)[0].strip() == name:
            return True
    return False


def _remove_one(
    project: Project,
    wt: Worktree,
    checks: WorktreeChecks | None = None,
    *,
    force: bool = False,
    assume_yes: bool = False,
) -> bool:
    """Remove a single worktree + its DB. Returns True on success."""
    path = Path(wt.path)

    console.print()
    console.print(f"[bold]about to remove:[/bold] {wt.shorthand}")
    console.print(f"  worktree: {path}")
    if checks and checks.pr_state == "CLOSED":
        when = f" ({checks.pr_resolved_at})" if checks.pr_resolved_at else ""
        console.print(
            f"  [yellow]PR was closed without merging{when} — verify the work is "
            f"preserved elsewhere before confirming.[/yellow]"
        )
    drop_db = False
    if wt.db and _db_exists(wt.db):
        console.print(f"  database: {wt.db} (will dropdb)")
        drop_db = True
    elif wt.db:
        console.print(f"  database: [dim]{wt.db} (not present, will skip)[/dim]")

    # Gitignored content the safety floor doesn't see — mockups, drafts, scratch
    # notes — would be silently destroyed by `git worktree remove`. Surface the
    # list so the caller can migrate (`mv`) anything they want to keep before
    # confirming. Doesn't block removal; informational + included in the prompt
    # context so a careless `y` after seeing the list is still the caller's
    # call.
    ignored = _gitignored_content(path, project.manifest.cleanup.gitignored_exclude)
    if ignored:
        console.print(
            f"  [yellow]gitignored content ({len(ignored)} entr"
            f"{'y' if len(ignored) == 1 else 'ies'}) — will be destroyed:[/yellow]"
        )
        for entry in ignored:
            console.print(f"    [yellow]· {entry}[/yellow]")
        console.print(
            "  [dim]extend cleanup.gitignored_exclude in .wt.yaml to suppress "
            "regenerable patterns.[/dim]"
        )

    if not assume_yes:
        ans = input("proceed? [y/N] ").strip().lower()
        if ans not in {"y", "yes"}:
            console.print("[dim]skipped[/dim]")
            return False

    # git worktree remove (never --force unless explicitly forced).
    cmd = ["git", "worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(str(path))
    r = subprocess.run(cmd, cwd=project.primary, capture_output=True, text=True)
    if r.returncode != 0:
        err.print(f"[red]git worktree remove failed:[/red] {r.stderr.strip()}")
        return False
    console.print("  [green]✓[/green] worktree removed")

    if drop_db and wt.db:
        rd = subprocess.run(["dropdb", wt.db], capture_output=True, text=True)
        if rd.returncode == 0:
            console.print(f"  [green]✓[/green] dropped {wt.db}")
        else:
            err.print(f"  [yellow]warning:[/yellow] dropdb failed: {rd.stderr.strip()}")

    project.registry.remove(wt.shorthand)
    project.save()
    return True


def run_one(start: Path, shorthand: str, assume_yes: bool = False) -> int:
    """Remove one specific worktree by shorthand. Same safety checks as --auto."""
    project = Project.discover(start)
    wt = project.registry.find(shorthand)
    if wt is None:
        err.print(f"[red]error:[/red] no worktree {shorthand!r} in registry.")
        return 2

    if wt.shorthand == PRIMARY:
        err.print("[red]error:[/red] refusing to remove the primary worktree.")
        return 2
    if wt.shorthand in project.manifest.cleanup.protected:
        err.print(
            f"[red]error:[/red] {shorthand!r} is in cleanup.protected — refusing."
        )
        return 2

    checks = _check(project, wt)
    if checks is None:
        # Shouldn't happen given the explicit checks above, but be safe.
        err.print(f"[red]error:[/red] {shorthand!r} is not eligible for removal.")
        return 2

    if checks.reasons:
        err.print(f"[yellow]hold[/yellow] {checks.path}")
        for r in checks.reasons:
            err.print(f"  ↳ {r}")
        err.print()
        err.print(
            "refusing to remove. Resolve the reasons above or run with manual "
            "git/dropdb commands if you know what you're doing."
        )
        return 1

    ok = _remove_one(project, wt, checks, assume_yes=assume_yes)
    return 0 if ok else 1


def run_auto(start: Path) -> int:
    """Scan all worktrees, prompt y/N per eligible one. The bash-script flow."""
    project = Project.discover(start)

    console.print("scanning worktrees…\n")
    eligible: list[tuple[Worktree, WorktreeChecks]] = []
    for wt in project.registry.worktrees:
        if wt.shorthand == PRIMARY:
            console.print(f"  [dim]skip[/dim]  {wt.path}  [dim](primary)[/dim]")
            continue
        if wt.shorthand in project.manifest.cleanup.protected:
            console.print(f"  [dim]skip[/dim]  {wt.path}  [dim](protected)[/dim]")
            continue
        checks = _check(project, wt)
        if checks is None:
            continue
        if checks.reasons:
            console.print(f"  [yellow]hold[/yellow]  {checks.path}")
            for r in checks.reasons:
                console.print(f"         [yellow]↳ {r}[/yellow]")
        else:
            verb = "merged" if checks.pr_state == "MERGED" else "closed without merge"
            console.print(
                f"  [green] ok [/green]  {checks.path}  "
                f"[dim]({verb} {checks.pr_resolved_at}, clean)[/dim]"
            )
            eligible.append((wt, checks))

    console.print()
    if not eligible:
        console.print("nothing to remove.")
        return 0
    console.print(f"{len(eligible)} worktree(s) eligible for removal.")

    for wt, checks in eligible:
        _remove_one(project, wt, checks)

    console.print()
    console.print("done.")
    return 0
