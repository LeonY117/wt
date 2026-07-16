"""Safe, move-only rescue of Claude Code state tied to worktree paths."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


def munge_path(path: Path) -> str:
    """Return Claude Code's project-directory name for an absolute path.

    Verified against the existing entries in ``~/.claude/projects``: path
    separators and dots become hyphens, while existing hyphens are preserved.
    """
    return str(path.expanduser().resolve()).replace("/", "-").replace(".", "-")


def projects_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".claude" / "projects"


def project_dir(path: Path, home: Path | None = None) -> Path:
    return projects_dir(home) / munge_path(path)


@dataclass
class RescueResult:
    source: Path
    destination: Path
    found: bool = False
    sessions_moved: int = 0
    collisions: list[str] = field(default_factory=list)
    archive: Path | None = None
    archive_collision: Path | None = None


def rescue_state(
    *,
    source: Path,
    primary: Path,
    project: str,
    shorthand: str,
    home: Path | None = None,
    on_date: date | None = None,
) -> RescueResult:
    """Rescue the Claude project directory keyed by ``source``."""
    return rescue_project_dir(
        source_dir=project_dir(source, home),
        primary=primary,
        project=project,
        shorthand=shorthand,
        home=home,
        on_date=on_date,
    )


def rescue_worktree_state(
    *,
    source: Path,
    primary: Path,
    project: str,
    shorthand: str,
    home: Path | None = None,
    on_date: date | None = None,
) -> list[RescueResult]:
    """Rescue state launched from a worktree root or any of its subdirectories."""
    source_dir = project_dir(source, home)
    candidates: list[Path] = []
    if source_dir.is_dir():
        candidates.append(source_dir)

    root = projects_dir(home)
    if root.is_dir():
        protected_munges = {
            protected
            for protected in _other_existing_path_munges(source, primary)
            if protected == source_dir.name
            or protected.startswith(f"{source_dir.name}-")
        }
        descendants = sorted(
            candidate
            for candidate in root.iterdir()
            if candidate.is_dir()
            and candidate.name.startswith(f"{source_dir.name}-")
            and not any(
                candidate.name == protected
                or candidate.name.startswith(f"{protected}-")
                for protected in protected_munges
            )
        )
        candidates.extend(descendants)

    results: list[RescueResult] = []
    for candidate in candidates:
        archive_shorthand = shorthand
        if candidate != source_dir:
            suffix = candidate.name[len(source_dir.name) + 1 :]
            archive_shorthand = f"{shorthand}-{suffix}"
        results.append(
            rescue_project_dir(
                source_dir=candidate,
                primary=primary,
                project=project,
                shorthand=archive_shorthand,
                home=home,
                on_date=on_date,
            )
        )
    return results


def _other_existing_path_munges(source: Path, primary: Path) -> set[str]:
    """Return Claude keys belonging to paths other than the removed worktree.

    Claude's path munge is lossy: a sibling named ``<worktree>--served`` can
    look like a subdirectory launch under ``<worktree>``. Protect both on-disk
    siblings and every other worktree still reported by git.
    """
    source = source.expanduser().resolve()
    paths: set[Path] = set()
    try:
        paths.update(
            sibling.resolve()
            for sibling in source.parent.iterdir()
            if sibling.is_dir() and sibling.resolve() != source
        )
    except OSError:
        pass

    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=primary,
            capture_output=True,
            text=True,
        )
    except OSError:
        result = None
    if result is not None and result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree ")).expanduser().resolve()
                if path != source:
                    paths.add(path)

    return {munge_path(path) for path in paths}


def rescue_project_dir(
    *,
    source_dir: Path,
    primary: Path,
    project: str,
    shorthand: str,
    home: Path | None = None,
    on_date: date | None = None,
) -> RescueResult:
    """Move sessions to the primary and archive every remaining entry.

    Existing destination files and archive directories are never overwritten.
    Empty Claude project directories may be removed; files are only moved.
    """
    primary_dir = project_dir(primary, home)
    result = RescueResult(source=source_dir, destination=primary_dir)
    if not source_dir.is_dir():
        return result

    result.found = True
    sessions = sorted(source_dir.glob("*.jsonl"))
    if sessions:
        primary_dir.mkdir(parents=True, exist_ok=True)
    for session in sessions:
        target = primary_dir / session.name
        if target.exists():
            result.collisions.append(session.name)
            continue
        # Source and destination are both below the user's home directory, so
        # rename is an atomic move and never falls back to copy-then-delete.
        session.rename(target)
        result.sessions_moved += 1

    if any(source_dir.iterdir()):
        archive_root = (home or Path.home()) / ".wt-history" / "claude-state"
        archive_root.mkdir(parents=True, exist_ok=True)
        archive = archive_root / f"{project}-{shorthand}-{on_date or date.today():%Y-%m-%d}"
        if archive.exists():
            # Preserve both trees in place instead of merging or overwriting.
            result.archive_collision = archive
        else:
            source_dir.rename(archive)
            result.archive = archive
    else:
        source_dir.rmdir()

    return result
