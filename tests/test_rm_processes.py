from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wt.claude_state import RescueResult
from wt.commands import rm
from wt.manifest import Manifest
from wt.project import Project
from wt.types import Worktree


def test_live_cwd_processes_are_bounded_to_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "tree"
    inside = worktree / "backend"
    outside = tmp_path / "tree-other"
    inside.mkdir(parents=True)
    outside.mkdir()
    output = "\n".join(
        [
            "p123",
            "cclaude",
            "fcwd",
            f"n{inside}",
            "p456",
            "cnode",
            "fcwd",
            f"n{outside}",
        ]
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=output, stderr="")

    monkeypatch.setattr(rm.subprocess, "run", fake_run)

    assert rm._live_cwd_processes(worktree) == [
        rm.CwdProcess(pid=123, name="claude", cwd=inside)
    ]


def test_force_removal_still_rescues_before_git_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary = tmp_path / "brain-app"
    worktree = tmp_path / "brain-app--feature"
    primary.mkdir()
    worktree.mkdir()
    manifest = Manifest(
        project="brain-app", worktree_prefix="brain-app--", services=[]
    )
    project = Project(manifest, primary)
    entry = Worktree(shorthand="feature", path=str(worktree), branch="feature")
    checks = rm.WorktreeChecks(
        shorthand="feature", path=worktree, branch="feature"
    )
    events: list[str] = []

    monkeypatch.setattr(rm, "_gitignored_content", lambda *args: [])
    monkeypatch.setattr(rm, "_print_cleanup_checklist", lambda *args: None)

    def fake_rescue(**kwargs):
        events.append("rescue")
        return [
            RescueResult(source=tmp_path / "source", destination=tmp_path / "dest")
        ]

    def fake_run(*args, **kwargs):
        events.append("git")
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr(rm, "rescue_worktree_state", fake_rescue)
    monkeypatch.setattr(rm.subprocess, "run", fake_run)

    assert rm._remove_one(
        project, entry, checks, force=True, assume_yes=True
    )
    assert events == ["rescue", "git"]
