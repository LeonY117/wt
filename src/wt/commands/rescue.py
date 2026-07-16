"""`wt rescue` — find and recover orphaned Claude Code project state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console

from ..claude_state import munge_path, projects_dir, rescue_project_dir
from ..project import Project


console = Console()
err = Console(stderr=True)


@dataclass(frozen=True)
class Orphan:
    path: Path
    shorthand: str
    sessions: int
    modified: float


def _last_modified(path: Path) -> float:
    modified = path.stat().st_mtime
    for child in path.rglob("*"):
        try:
            modified = max(modified, child.stat().st_mtime)
        except FileNotFoundError:
            pass
    return modified


def _orphan_shorthand(project: Project, name: str) -> str:
    root = project.manifest.resolved_worktree_root(project.primary)
    if root is not None:
        root_prefix = f"{munge_path(root)}-"
        if name.startswith(root_prefix):
            return name[len(root_prefix) :]
    primary_prefix = f"{munge_path(project.primary)}-"
    return name[len(primary_prefix) :].lstrip("-")


def find_orphans(project: Project, home: Path | None = None) -> list[Orphan]:
    """Find prefix-matching Claude dirs excluded from git's live worktrees."""
    root = projects_dir(home)
    if not root.is_dir():
        return []

    live = {
        munge_path(Path(entry["path"]))
        for entry in project.all_git_worktrees()
    }
    primary_name = munge_path(project.primary)
    prefix = f"{primary_name}-"
    legacy_prefix = munge_path(
        project.primary.parent / project.manifest.worktree_prefix
    )
    configured_root = project.manifest.resolved_worktree_root(project.primary)
    configured_prefix = (
        f"{munge_path(configured_root)}-" if configured_root is not None else None
    )

    def belongs_to_live_worktree(name: str) -> bool:
        if name in live:
            return True
        for live_name in live - {primary_name}:
            if name.startswith(f"{live_name}-"):
                return True

        # A primary subdirectory launch also starts with the primary's munged
        # name. Preserve the known legacy/new worktree namespaces, which share
        # that prefix because Claude's munge is lossy.
        if name.startswith(prefix):
            is_legacy_worktree = name.startswith(legacy_prefix)
            is_configured_worktree = bool(
                configured_prefix and name.startswith(configured_prefix)
            )
            if not is_legacy_worktree and not is_configured_worktree:
                return True
        return False

    orphans: list[Orphan] = []
    for candidate in root.iterdir():
        if not candidate.is_dir():
            continue
        if not candidate.name.startswith(prefix) or belongs_to_live_worktree(
            candidate.name
        ):
            continue
        orphans.append(
            Orphan(
                path=candidate,
                shorthand=_orphan_shorthand(project, candidate.name),
                sessions=len(list(candidate.glob("*.jsonl"))),
                modified=_last_modified(candidate),
            )
        )
    return sorted(orphans, key=lambda item: item.path.name)


def _print_result(result) -> None:
    console.print(
        f"  [green]✓[/green] {result.sessions_moved} session(s) moved to "
        f"{result.destination}"
    )
    for name in result.collisions:
        console.print(
            f"  [yellow]collision:[/yellow] {name} already exists at the destination; "
            "the source was preserved"
        )
    if result.archive is not None:
        console.print(f"  [green]✓[/green] memory archived to {result.archive}")
    if result.archive_collision is not None:
        console.print(
            f"  [yellow]collision:[/yellow] archive {result.archive_collision} already "
            "exists; remaining source state was left in place"
        )


def run(start: Path, *, apply: bool = False) -> int:
    project = Project.discover(start)
    orphans = find_orphans(project)
    if not orphans:
        console.print("no orphaned Claude Code state found.")
        return 0

    console.print(
        f"{'rescuing' if apply else 'would rescue'} {len(orphans)} orphaned Claude "
        f"project dir(s){'' if apply else ' (dry run)'}:"
    )
    for orphan in orphans:
        modified = datetime.fromtimestamp(orphan.modified).astimezone().isoformat(
            timespec="minutes"
        )
        console.print(
            f"  {orphan.path} — {orphan.sessions} session(s), last modified {modified}"
        )
        if apply:
            try:
                result = rescue_project_dir(
                    source_dir=orphan.path,
                    primary=project.primary,
                    project=project.manifest.project or project.primary.name,
                    shorthand=orphan.shorthand,
                )
            except OSError as exc:
                err.print(f"  [red]rescue failed:[/red] {exc}")
                return 1
            _print_result(result)

    if not apply:
        console.print("run `wt rescue --apply` to move and archive this state.")
    return 0
