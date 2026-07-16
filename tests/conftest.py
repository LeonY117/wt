from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@pytest.fixture
def repo_factory(tmp_path: Path):
    def make(*, worktree_root: str | None = None) -> Path:
        repo = tmp_path / "brain-app"
        repo.mkdir()
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.email", "wt-tests@example.com")
        git(repo, "config", "user.name", "wt tests")
        lines = [
            "project: brain-app",
            "worktree_prefix: brain-app--",
            "services: []",
        ]
        if worktree_root is not None:
            lines.insert(2, f"worktree_root: {worktree_root}")
        (repo / ".wt.yaml").write_text("\n".join(lines) + "\n")
        (repo / "README.md").write_text("fixture\n")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "fixture")
        return repo

    return make
