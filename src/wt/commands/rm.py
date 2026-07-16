"""`wt rm` — safely tear down worktrees.

A worktree is eligible when its branch has a most-recent PR that's MERGED
or CLOSED (either is an explicit human decision), the working tree is
clean, and the branch has no unpushed commits. Open PRs and never-PR'd
branches are refused.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console

from ..claude_state import rescue_worktree_state
from ..placeholders import render_db_name
from ..project import Project
from ..types import PRIMARY, Worktree


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


# Static reminders printed before `wt rm` destroys a worktree — and when it
# refuses one. The gitignored sweep below shows what's *actually* on disk;
# this checklist is the discipline-prompt for the agent driving the tool, since
# `git worktree remove` deletes the whole directory and anything not migrated
# out is gone for good. Extend per-project via `cleanup.checklist` in `.wt.yaml`.
DEFAULT_CLEANUP_CHECKLIST = (
    "Mockups / prototype files moved out of the worktree (e.g. a `mockups/` dir)?",
    "Notes / scratch docs migrated to the repo's `notes/` or the vault?",
    "Generated reports, exports, or screenshots you wanted copied elsewhere?",
    "Nothing under this worktree is still referenced by work in progress?",
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


@dataclass(frozen=True)
class CwdProcess:
    pid: int
    name: str
    cwd: Path


@dataclass
class DBRemovalPlan:
    name: str | None
    expected: str | None
    explicit: bool = False
    exists: bool = False
    should_drop: bool = False
    reason: str | None = None
    ownership_warning: str | None = None
    references: list[str] = field(default_factory=list)


def _live_cwd_processes(path: Path) -> list[CwdProcess]:
    """Return processes whose cwd is the worktree or one of its descendants."""
    r = subprocess.run(
        ["lsof", "-a", "-d", "cwd", "-Fpcn"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return []

    root = path.resolve()
    found: list[CwdProcess] = []
    pid: int | None = None
    name = "unknown"
    for line in r.stdout.splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
            name = "unknown"
        elif line.startswith("c"):
            name = line[1:] or "unknown"
        elif line.startswith("n") and pid is not None:
            cwd = Path(line[1:])
            try:
                cwd.resolve().relative_to(root)
            except (ValueError, OSError):
                continue
            found.append(CwdProcess(pid=pid, name=name, cwd=cwd))
    return found


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
    be silently skipped (protected or primary). Otherwise returns a
    WorktreeChecks with .reasons populated for any blockers (empty list = ok).
    """
    manifest = project.manifest
    path = Path(wt.path)

    if wt.shorthand == PRIMARY:
        return None
    if wt.shorthand in manifest.cleanup.protected:
        return None
    checks = WorktreeChecks(shorthand=wt.shorthand, path=path, branch=wt.branch)

    if not path.exists():
        checks.reasons.append("worktree directory missing on disk")
        return checks

    for process in _live_cwd_processes(path):
        checks.reasons.append(
            f"live process has cwd inside worktree: {process.name} "
            f"(PID {process.pid}, cwd {process.cwd})"
        )

    if wt.branch is None:
        checks.reasons.append("detached HEAD — skipping")
        return checks

    # PR-state check. A MERGED or CLOSED PR both count as a human decision
    # that the branch is done — either is safe. An OPEN PR means active work.
    # No PR at all means the work was never reviewed, so refuse.
    state, resolved_at, head_sha = _gh_latest_pr(wt.branch)
    checks.pr_state = state
    checks.pr_resolved_at = resolved_at
    local_sha = _git_at(path, ["rev-parse", "HEAD"]).strip()

    # When the local tip is *exactly* the PR's head commit, the branch holds
    # nothing the PR didn't already capture — there is no local-only work to
    # lose, regardless of what the remote branch looks like now. This is the
    # squash-merge happy path: GitHub squashes the branch into a single new
    # commit on main, then auto-deletes the branch. The original commits never
    # land in main's history and the remote branch (and, after a prune, its
    # tracking ref) is gone — which would otherwise trip the "no upstream" /
    # "unpushed commits" checks below and wrongly refuse a cleanly-merged
    # worktree. Trusting the resolved PR + matching tip sidesteps that.
    tip_is_pr_head = bool(head_sha and local_sha and local_sha == head_sha)

    if state == "MERGED":
        checks.merged_sha = head_sha
        # Branch amended/force-pushed after the merge — local tip carries
        # post-merge work we'd silently lose. (tip_is_pr_head is False here.)
        if head_sha and local_sha and local_sha != head_sha:
            checks.reasons.append(
                f"local tip {local_sha[:7]} differs from merged SHA "
                f"{head_sha[:7]} (amended/force-pushed since merge?)"
            )
    elif state == "CLOSED":
        # No SHA-match *reason*: closed-without-merge has no canonical "final"
        # SHA. But if the tip matches the PR head we still treat it as clean
        # (tip_is_pr_head) and skip the upstream checks; otherwise those checks
        # below catch local-only work.
        pass
    elif state == "OPEN":
        checks.reasons.append(f"open PR on remote for {wt.branch}")
    else:
        checks.reasons.append(f"no PR on remote for {wt.branch}")

    # Dirty / untracked — always relevant, the PR never captures these.
    porcelain = _git_at(path, ["status", "--porcelain"]).strip()
    if porcelain:
        dirty_count = len(porcelain.splitlines())
        checks.reasons.append(f"{dirty_count} uncommitted/untracked file(s)")

    # Unpushed commits. Skipped when the tip already matches the resolved PR
    # head (see tip_is_pr_head above) — there, a missing upstream means the
    # remote branch was deleted post-merge, not that work is stranded.
    if not tip_is_pr_head:
        upstream = _git_at(
            path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
        ).strip()
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


def _print_cleanup_checklist(project: Project, out: Console) -> None:
    """Print the static "did you clean this up?" reminder for the agent.

    Built-in items (`DEFAULT_CLEANUP_CHECKLIST`) plus any project-specific ones
    from `cleanup.checklist`. Purely advisory — it doesn't block, it nudges.
    """
    items = list(DEFAULT_CLEANUP_CHECKLIST) + list(project.manifest.cleanup.checklist)
    out.print("  [bold]cleanup checklist[/bold] — verify before the directory is destroyed:")
    for item in items:
        out.print(f"    [ ] {item}")


def _confirm_force(shorthand: str, reasons: list[str]) -> bool:
    """Stern, deliberate gate for `wt rm --force`. Returns True only when the
    caller types the worktree's exact shorthand back.

    Forcing bypasses every safety reason, so the confirmation is intentionally
    harder than a casual `y`: the operator must retype the worktree name. Reads
    from stdin; on EOF (non-interactive run) it aborts and prints the one-liner
    that supplies the confirmation, so an agent can opt in deliberately.
    """
    err.print()
    err.print("[bold red]⚠  FORCE REMOVE — every safety check below is being bypassed:[/bold red]")
    for r in reasons:
        err.print(f"     [red]↳ {r}[/red]")
    err.print()
    err.print(
        "[red]This permanently deletes the worktree even though it is NOT in a known-safe\n"
        "state. Uncommitted changes, unpushed commits, and gitignored files (mockups,\n"
        "notes, drafts listed above) will be gone for good — there is no undo.[/red]"
    )
    err.print()
    err.print(
        f"Have you double-checked [bold]every[/bold] item above and the cleanup checklist?\n"
        f"If so, type the worktree name exactly ([bold]{shorthand}[/bold]) to proceed. "
        f"Anything else aborts."
    )
    try:
        ans = input("confirm worktree name: ").strip()
    except EOFError:
        err.print("[dim]no confirmation received — aborted.[/dim]")
        err.print(
            f"[dim]to force non-interactively: "
            f"echo '{shorthand}' | wt rm {shorthand} --force[/dim]"
        )
        return False
    if ans != shorthand:
        err.print("[dim]name did not match — aborted.[/dim]")
        return False
    return True


def _db_exists(name: str) -> bool:
    r = subprocess.run(["psql", "-lqt"], capture_output=True, text=True)
    if r.returncode != 0:
        return False
    for line in r.stdout.splitlines():
        if line.split("|", 1)[0].strip() == name:
            return True
    return False


def _plan_db_removal(
    project: Project,
    wt: Worktree,
    *,
    drop_db: str | None = None,
    worktrees: list[Worktree] | None = None,
) -> DBRemovalPlan:
    """Decide whether a DB may be dropped, without mutating anything."""
    name = drop_db if drop_db is not None else wt.db
    expected = render_db_name(project.manifest, wt.shorthand)
    plan = DBRemovalPlan(name=name, expected=expected, explicit=drop_db is not None)
    if name is None:
        return plan

    ownership_matches = expected is not None and name == expected
    if not ownership_matches:
        expected_display = expected or "<no db.name_template configured>"
        if plan.explicit:
            plan.ownership_warning = (
                f"DB {name} does not match this worktree (expected "
                f"{expected_display}); explicit --drop-db override requested."
            )
        else:
            plan.ownership_warning = (
                f"DB {name} not owned by this worktree (expected {expected_display}); "
                "drop manually if intended."
            )
        if not plan.explicit:
            plan.reason = "ownership mismatch"
            return plan

    plan.exists = _db_exists(name)
    if not plan.exists:
        plan.reason = "not present"
        return plan

    if worktrees is None:
        worktrees = project.worktrees()
    target_path = Path(wt.path).resolve()
    plan.references = [
        other.shorthand
        for other in worktrees
        if other.db == name and Path(other.path).resolve() != target_path
    ]
    if plan.references:
        plan.reason = "referenced by other worktree(s): " + ", ".join(plan.references)
        return plan

    plan.should_drop = True
    return plan


def _db_dump_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".wt-history" / "db-dumps"


def _dump_database(
    name: str,
    *,
    home: Path | None = None,
    now: datetime | None = None,
) -> tuple[Path | None, str | None]:
    """Stream ``pg_dump`` through ``gzip`` into the DB history directory."""
    dump_dir = _db_dump_dir(home)
    try:
        dump_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return None, str(exc)
    when = now or datetime.now()
    safe_name = name.replace("/", "_")
    destination = dump_dir / f"{safe_name}-{when:%Y-%m-%d-%H%M%S}.sql.gz"
    while destination.exists():
        when += timedelta(seconds=1)
        destination = dump_dir / f"{safe_name}-{when:%Y-%m-%d-%H%M%S}.sql.gz"

    temp_path: Path | None = None
    dump: subprocess.Popen[bytes] | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=dump_dir, delete=False) as output:
            temp_path = Path(output.name)
            with tempfile.TemporaryFile() as pg_error:
                dump = subprocess.Popen(
                    ["pg_dump", "--dbname", name],
                    stdout=subprocess.PIPE,
                    stderr=pg_error,
                )
                assert dump.stdout is not None
                gzip = subprocess.Popen(
                    ["gzip"],
                    stdin=dump.stdout,
                    stdout=output,
                    stderr=subprocess.PIPE,
                )
                dump.stdout.close()
                _, gzip_stderr = gzip.communicate()
                dump_returncode = dump.wait()
                pg_error.seek(0)
                dump_stderr = pg_error.read().decode(errors="replace").strip()

        if dump_returncode != 0 or gzip.returncode != 0:
            details = dump_stderr or (gzip_stderr or b"").decode(
                errors="replace"
            ).strip()
            temp_path.unlink(missing_ok=True)
            return None, details or "pg_dump/gzip exited unsuccessfully"
        temp_path.replace(destination)
        return destination, None
    except OSError as exc:
        if dump is not None and dump.poll() is None:
            dump.terminate()
            dump.wait()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        return None, str(exc)


def _expire_old_db_dumps(
    *,
    home: Path | None = None,
    now: datetime | None = None,
) -> list[Path]:
    """Delete compressed DB dumps older than the 30-day retention window."""
    dump_dir = _db_dump_dir(home)
    if not dump_dir.is_dir():
        return []
    cutoff = (now or datetime.now()).timestamp() - timedelta(days=30).total_seconds()
    removed: list[Path] = []
    for dump in dump_dir.glob("*.sql.gz"):
        try:
            if dump.stat().st_mtime < cutoff:
                dump.unlink()
                removed.append(dump)
        except OSError:
            # Retention cleanup is best-effort and must not interfere with the
            # safety decision for the DB associated with this removal.
            continue
    return removed


def _remove_one(
    project: Project,
    wt: Worktree,
    checks: WorktreeChecks | None = None,
    *,
    force: bool = False,
    assume_yes: bool = False,
    drop_db: str | None = None,
    worktrees: list[Worktree] | None = None,
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
    db_plan = _plan_db_removal(
        project,
        wt,
        drop_db=drop_db,
        worktrees=worktrees,
    )
    if db_plan.name is not None:
        if db_plan.ownership_warning is not None:
            console.print(
                "  [bold red]WARNING: database name does not match this "
                "worktree's db.name_template.[/bold red]"
            )
            console.print(f"  [yellow]{db_plan.ownership_warning}[/yellow]")
        if db_plan.should_drop:
            console.print(f"  database: {db_plan.name} (will dump, then dropdb)")
        else:
            console.print(
                f"  DB: {db_plan.name} — NOT dropped "
                f"({db_plan.reason or 'no database selected'})"
            )

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

    # Static reminder, right before the confirmation gate.
    _print_cleanup_checklist(project, console)

    blocked = bool(checks and checks.reasons)
    if blocked and force:
        # Forcing past real blockers — demand the stern, retype-the-name gate
        # regardless of --yes. (A clean worktree with no reasons never reaches
        # this branch; force just means "git worktree remove --force".)
        if not _confirm_force(wt.shorthand, checks.reasons):
            return False
    elif not assume_yes:
        ans = input("proceed? [y/N] ").strip().lower()
        if ans not in {"y", "yes"}:
            console.print("[dim]skipped[/dim]")
            return False

    if db_plan.should_drop and db_plan.name is not None:
        dump_path, dump_error = _dump_database(db_plan.name)
        if dump_path is None:
            db_plan.should_drop = False
            db_plan.reason = f"insurance dump failed: {dump_error or 'unknown error'}"
            err.print(
                f"  [bold red]DB: {db_plan.name} — NOT dropped "
                f"({db_plan.reason})[/bold red]"
            )
        else:
            console.print(f"  [green]✓[/green] database dump saved to {dump_path}")

    # Claude Code keys sessions and memory by the worktree's absolute path.
    # Rescue that state before the directory disappears, even under --force.
    try:
        rescued = rescue_worktree_state(
            source=path,
            primary=project.primary,
            project=project.manifest.project or project.primary.name,
            shorthand=wt.shorthand,
        )
    except OSError as exc:
        err.print(f"[red]Claude state rescue failed; removal aborted:[/red] {exc}")
        return False

    for result in rescued:
        if not result.found:
            continue
        console.print(
            f"  [green]✓[/green] {result.sessions_moved} session(s) moved to "
            f"{result.destination}"
        )
        for name in result.collisions:
            console.print(
                f"  [yellow]collision:[/yellow] {name} already exists at the "
                "destination; the source was preserved"
            )
        if result.archive is not None:
            console.print(f"  [green]✓[/green] memory archived to {result.archive}")
        if result.archive_collision is not None:
            console.print(
                f"  [yellow]collision:[/yellow] archive {result.archive_collision} "
                "already exists; remaining source state was left in place"
            )

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

    if db_plan.should_drop and db_plan.name is not None:
        rd = subprocess.run(["dropdb", db_plan.name], capture_output=True, text=True)
        if rd.returncode == 0:
            console.print(f"  [green]✓[/green] dropped {db_plan.name}")
        else:
            err.print(f"  [yellow]warning:[/yellow] dropdb failed: {rd.stderr.strip()}")

    # No registry mutation — once the worktree dir is gone, `derive_worktrees`
    # stops returning it on the next read.
    return True


def run_one(
    start: Path,
    shorthand: str,
    assume_yes: bool = False,
    force: bool = False,
    drop_db: str | None = None,
) -> int:
    """Remove one specific worktree by shorthand. Same safety checks as --auto.

    `force` bypasses the safety *reasons* (dirty / unpushed / unmerged / live
    cwd process) behind a stern retype-the-name confirmation. The primary and
    `cleanup.protected` worktrees are never force-removable — those refusals stand.
    """
    project = Project.discover(start)
    _expire_old_db_dumps()
    worktrees = project.worktrees()
    wt = project.find(shorthand, worktrees)
    if wt is None:
        err.print(f"[red]error:[/red] no worktree {shorthand!r} found on disk.")
        return 2

    if wt.shorthand == PRIMARY:
        err.print("[red]error:[/red] refusing to remove the primary worktree.")
        return 2
    if wt.shorthand in project.manifest.cleanup.protected:
        err.print(
            f"[red]error:[/red] {shorthand!r} is in cleanup.protected — refusing."
            " (not bypassable with --force)"
        )
        return 2

    checks = _check(project, wt)
    if checks is None:
        # Shouldn't happen given the explicit checks above, but be safe.
        err.print(f"[red]error:[/red] {shorthand!r} is not eligible for removal.")
        return 2

    if checks.reasons and not force:
        err.print(f"[yellow]hold[/yellow] {checks.path}")
        for r in checks.reasons:
            err.print(f"  ↳ {r}")
        err.print()
        err.print("[bold]before removing, make sure you have cleaned up the worktree:[/bold]")
        _print_cleanup_checklist(project, err)
        err.print()
        err.print(
            "refusing to remove. Resolve the reasons above, or — once you have "
            f"verified the checklist — force past them with [bold]wt rm {shorthand} "
            "--force[/bold] (you'll be asked to confirm the worktree name)."
        )
        return 1

    ok = _remove_one(
        project,
        wt,
        checks,
        force=force,
        assume_yes=assume_yes,
        drop_db=drop_db,
        worktrees=worktrees,
    )
    return 0 if ok else 1


def run_auto(start: Path) -> int:
    """Scan all worktrees, prompt y/N per eligible one. The bash-script flow."""
    project = Project.discover(start)
    _expire_old_db_dumps()

    console.print("scanning worktrees…\n")
    eligible: list[tuple[Worktree, WorktreeChecks]] = []
    worktrees = project.worktrees()
    for wt in worktrees:
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
        _remove_one(project, wt, checks, worktrees=worktrees)

    console.print()
    console.print("done.")
    return 0
