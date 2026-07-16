from __future__ import annotations

import subprocess
from pathlib import Path

from wt.claude_state import project_dir
from wt.commands.rescue import find_orphans
from wt.project import Project


def test_orphans_are_detected_by_excluding_live_git_worktrees(
    repo_factory, tmp_path: Path
) -> None:
    repo = repo_factory(worktree_root="../brain-app--worktrees")
    root = tmp_path / "brain-app--worktrees"
    root.mkdir()
    live = root / "live"
    subprocess.run(
        ["git", "worktree", "add", "-b", "live", str(live)],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    home = tmp_path / "home"
    old_orphan = tmp_path / "brain-app--gone"
    new_orphan = root / "new-gone"
    unrelated = tmp_path / "different-project--gone"
    live_subdir = live / "backend"
    primary_subdir = repo / "backend"
    for path in (
        repo,
        live,
        live_subdir,
        primary_subdir,
        old_orphan,
        new_orphan,
        unrelated,
    ):
        state = project_dir(path, home)
        state.mkdir(parents=True)
        (state / "session.jsonl").write_text("session")

    project = Project.discover(repo)
    orphans = find_orphans(project, home)

    assert {item.path for item in orphans} == {
        project_dir(old_orphan, home),
        project_dir(new_orphan, home),
    }
    assert {item.shorthand for item in orphans} == {"gone", "new-gone"}
    assert all(item.sessions == 1 for item in orphans)
