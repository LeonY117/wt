"""Project discovery + the single read API onto a project's worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .manifest import Manifest
from .types import PRIMARY, Worktree


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
    """Bundle of manifest + primary worktree path for a project.

    There's no persistent registry: every read derives the live set of
    worktrees from `git worktree list` + each worktree's env files.
    """

    def __init__(self, manifest: Manifest, primary: Path):
        self.manifest = manifest
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
        return cls(manifest, primary)

    # ---- worktree lookups ----

    def all_git_worktrees(self) -> list[dict]:
        return list_git_worktrees(self.primary)

    def derive_shorthand(self, path: Path) -> str:
        return shorthand_for(path, self.primary, self.manifest.worktree_prefix)

    def worktrees(self) -> list[Worktree]:
        """The single read API. Walks git + reads env files for every worktree.

        Cheap enough for typical project sizes (<20 worktrees, ~5-20ms per
        shell-out); cache the result at the call site if you'll iterate it.
        """
        from .importer import derive_worktrees

        return derive_worktrees(self)

    def find(self, shorthand: str, worktrees: list[Worktree] | None = None) -> Worktree | None:
        """Look up one worktree by shorthand. Pass `worktrees` to reuse a cached list."""
        if worktrees is None:
            worktrees = self.worktrees()
        for wt in worktrees:
            if wt.shorthand == shorthand:
                return wt
        return None
