"""Project discovery + linkage of manifest, registry, and git worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .manifest import Manifest
from .registry import PRIMARY, Registry, Worktree


MANIFEST_FILENAME = ".wt.yaml"


def find_manifest(start: Path) -> Path | None:
    """Walk up from start looking for .wt.yaml."""
    p = start.resolve()
    while True:
        candidate = p / MANIFEST_FILENAME
        if candidate.exists():
            return candidate
        if p.parent == p:
            return None
        p = p.parent


def git(args: list[str], cwd: Path) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return out.stdout


def primary_worktree_path(any_worktree_path: Path) -> Path:
    """Return the path of the primary worktree (the one holding the common git dir)."""
    common = git(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        any_worktree_path,
    ).strip()
    # common is usually .../<primary>/.git
    return Path(common).parent


def list_git_worktrees(cwd: Path) -> list[dict]:
    """Parse `git worktree list --porcelain` into list of {path, branch}."""
    raw = git(["worktree", "list", "--porcelain"], cwd)
    out: list[dict] = []
    current: dict = {}
    for line in raw.splitlines():
        if line.startswith("worktree "):
            if current:
                out.append(current)
            current = {"path": line[len("worktree "):], "branch": None}
        elif line.startswith("branch refs/heads/"):
            current["branch"] = line[len("branch refs/heads/"):]
        elif line == "":
            if current:
                out.append(current)
                current = {}
    if current:
        out.append(current)
    return out


def shorthand_for(path: Path, primary: Path, prefix: str) -> str:
    """Derive a worktree's shorthand from its directory name."""
    if path.resolve() == primary.resolve():
        return PRIMARY
    name = path.name
    if name.startswith(prefix):
        return name[len(prefix):]
    return name


def shorthand_underscored(shorthand: str) -> str:
    safe = shorthand if shorthand != PRIMARY else "dev"
    return safe.replace("-", "_")


class Project:
    """Bundle of manifest + registry + primary worktree path for a project."""

    def __init__(self, manifest: Manifest, registry: Registry, primary: Path):
        self.manifest = manifest
        self.registry = registry
        self.primary = primary

    @classmethod
    def discover(cls, start: Path) -> "Project":
        manifest_path = find_manifest(start)
        if manifest_path is None:
            raise FileNotFoundError(
                f"No {MANIFEST_FILENAME} found walking up from {start}. "
                f"Create one at the project root."
            )
        manifest = Manifest.load(manifest_path)
        primary = primary_worktree_path(manifest.project_root)
        registry = Registry.load(manifest.project, primary)
        # Keep registry.project_root fresh if the repo moves.
        registry.project_root = str(primary)
        return cls(manifest, registry, primary)

    def save(self) -> None:
        self.registry.save()

    # ---- worktree lookups ----

    def all_git_worktrees(self) -> list[dict]:
        return list_git_worktrees(self.primary)

    def known_shorthands(self) -> set[str]:
        return {w.shorthand for w in self.registry.worktrees}

    def shorthand_in_use(self, shorthand: str) -> bool:
        return self.registry.find(shorthand) is not None

    def derive_shorthand(self, path: Path) -> str:
        return shorthand_for(path, self.primary, self.manifest.worktree_prefix)
