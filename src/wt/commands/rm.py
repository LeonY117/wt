"""`wt rm` — safely tear down worktrees.

Mirrors `scripts/cleanup-merged-worktrees`. Refuses anything unusual.
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


@dataclass
class WorktreeChecks:
    shorthand: str
    path: Path
    branch: str | None
    reasons: list[str] = field(default_factory=list)
    merged_at: str | None = None
    merged_sha: str | None = None


def _git_at(path: Path, args: list[str]) -> str:
    r = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
    )
    return r.stdout if r.returncode == 0 else ""


def _gh_merged_pr(branch: str) -> tuple[str | None, str | None]:
    """Return (mergedAt, headRefOid) for the most recent merged PR for branch."""
    r = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "merged",
            "--limit",
            "1",
            "--json",
            "mergedAt,headRefOid",
            "--jq",
            r'.[0] | "\(.mergedAt // "")|\(.headRefOid // "")"',
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return None, None
    line = r.stdout.strip()
    if not line or "|" not in line:
        return None, None
    merged_at, _, merged_sha = line.partition("|")
    return (merged_at or None), (merged_sha or None)


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

    # Merged-PR check.
    merged_at, merged_sha = _gh_merged_pr(wt.branch)
    checks.merged_at = merged_at
    checks.merged_sha = merged_sha
    if not merged_at:
        checks.reasons.append(f"no merged PR on remote for {wt.branch}")
    else:
        local_sha = _git_at(path, ["rev-parse", "HEAD"]).strip()
        if merged_sha and local_sha and local_sha != merged_sha:
            checks.reasons.append(
                f"local tip {local_sha[:7]} differs from merged SHA "
                f"{merged_sha[:7]} (amended/force-pushed since merge?)"
            )

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


def _db_exists(name: str) -> bool:
    r = subprocess.run(["psql", "-lqt"], capture_output=True, text=True)
    if r.returncode != 0:
        return False
    for line in r.stdout.splitlines():
        if line.split("|", 1)[0].strip() == name:
            return True
    return False


def _remove_one(
    project: Project, wt: Worktree, *, force: bool = False, assume_yes: bool = False
) -> bool:
    """Remove a single worktree + its DB. Returns True on success."""
    path = Path(wt.path)

    console.print()
    console.print(f"[bold]about to remove:[/bold] {wt.shorthand}")
    console.print(f"  worktree: {path}")
    drop_db = False
    if wt.db and _db_exists(wt.db):
        console.print(f"  database: {wt.db} (will dropdb)")
        drop_db = True
    elif wt.db:
        console.print(f"  database: [dim]{wt.db} (not present, will skip)[/dim]")

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

    ok = _remove_one(project, wt, assume_yes=assume_yes)
    return 0 if ok else 1


def run_auto(start: Path) -> int:
    """Scan all worktrees, prompt y/N per eligible one. The bash-script flow."""
    project = Project.discover(start)

    console.print("scanning worktrees…\n")
    eligible: list[Worktree] = []
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
            console.print(
                f"  [green] ok [/green]  {checks.path}  "
                f"[dim](merged {checks.merged_at}, clean)[/dim]"
            )
            eligible.append(wt)

    console.print()
    if not eligible:
        console.print("nothing to remove.")
        return 0
    console.print(f"{len(eligible)} worktree(s) eligible for removal.")

    for wt in eligible:
        _remove_one(project, wt)

    console.print()
    console.print("done.")
    return 0
