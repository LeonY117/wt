from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from wt.commands import rm
from wt.manifest import DBConfig, Manifest
from wt.project import Project
from wt.types import PRIMARY, Worktree


def _project(tmp_path: Path) -> tuple[Project, Worktree]:
    primary = tmp_path / "brain-app"
    worktree = tmp_path / "brain-app--feature"
    primary.mkdir()
    worktree.mkdir()
    manifest = Manifest(
        project="brain-app",
        worktree_prefix="brain-app--",
        services=[],
        db=DBConfig(name_template="brain_app_{shorthand_underscored}"),
    )
    project = Project(manifest, primary)
    target = Worktree(
        shorthand="feature",
        path=str(worktree),
        branch="feature",
        db="brain_app_feature",
    )
    return project, target


def test_db_plan_drops_only_an_owned_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, target = _project(tmp_path)
    monkeypatch.setattr(rm, "_db_exists", lambda name: True)

    owned = rm._plan_db_removal(project, target, worktrees=[target])
    assert owned.expected == "brain_app_feature"
    assert owned.should_drop

    target.db = "brain_app_demo"
    mismatched = rm._plan_db_removal(project, target, worktrees=[target])
    assert not mismatched.should_drop
    assert mismatched.reason == "ownership mismatch"
    assert mismatched.ownership_warning == (
        "DB brain_app_demo not owned by this worktree "
        "(expected brain_app_feature); drop manually if intended."
    )


def test_mismatched_db_does_not_block_worktree_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, target = _project(tmp_path)
    target.db = "brain_app_demo"
    checks = rm.WorktreeChecks(
        shorthand=target.shorthand,
        path=Path(target.path),
        branch=target.branch,
    )
    commands: list[list[str]] = []

    monkeypatch.setattr(rm, "_gitignored_content", lambda *args: [])
    monkeypatch.setattr(rm, "_print_cleanup_checklist", lambda *args: None)
    monkeypatch.setattr(rm, "rescue_worktree_state", lambda **kwargs: [])
    monkeypatch.setattr(
        rm,
        "_dump_database",
        lambda *args, **kwargs: pytest.fail("mismatched DB must not be dumped"),
    )

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(rm.subprocess, "run", fake_run)

    with rm.console.capture() as capture:
        removed = rm._remove_one(
            project,
            target,
            checks,
            assume_yes=True,
            worktrees=[target],
        )

    assert removed
    assert any(cmd[:3] == ["git", "worktree", "remove"] for cmd in commands)
    assert not any(cmd[0] == "dropdb" for cmd in commands)
    assert (
        "DB brain_app_demo not owned by this worktree "
        "(expected brain_app_feature); drop manually if intended."
        in capture.get().replace("\n", "")
    )


def test_cross_reference_refuses_drop_even_when_removal_is_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, target = _project(tmp_path)
    primary = Worktree(
        shorthand=PRIMARY,
        path=str(project.primary),
        branch="main",
        db=target.db,
    )
    checks = rm.WorktreeChecks(
        shorthand=target.shorthand,
        path=Path(target.path),
        branch=target.branch,
    )
    commands: list[list[str]] = []

    monkeypatch.setattr(rm, "_db_exists", lambda name: True)
    monkeypatch.setattr(rm, "_gitignored_content", lambda *args: [])
    monkeypatch.setattr(rm, "_print_cleanup_checklist", lambda *args: None)
    monkeypatch.setattr(rm, "rescue_worktree_state", lambda **kwargs: [])
    monkeypatch.setattr(
        rm,
        "_dump_database",
        lambda *args, **kwargs: pytest.fail("cross-referenced DB must not be dumped"),
    )

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(rm.subprocess, "run", fake_run)

    assert rm._remove_one(
        project,
        target,
        checks,
        force=True,
        assume_yes=True,
        worktrees=[primary, target],
    )
    assert any(cmd[:3] == ["git", "worktree", "remove"] for cmd in commands)
    assert not any(cmd[0] == "dropdb" for cmd in commands)


def test_explicit_drop_db_bypasses_ownership_but_not_cross_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, target = _project(tmp_path)
    legacy_db = "brain_app_legacy"
    monkeypatch.setattr(rm, "_db_exists", lambda name: True)

    explicit = rm._plan_db_removal(
        project,
        target,
        drop_db=legacy_db,
        worktrees=[target],
    )
    assert explicit.should_drop
    assert explicit.ownership_warning is not None

    primary = Worktree(
        shorthand=PRIMARY,
        path=str(project.primary),
        branch="main",
        db=legacy_db,
    )
    referenced = rm._plan_db_removal(
        project,
        target,
        drop_db=legacy_db,
        worktrees=[primary, target],
    )
    assert not referenced.should_drop
    assert referenced.references == [PRIMARY]
    assert referenced.reason == f"referenced by other worktree(s): {PRIMARY}"


@pytest.mark.parametrize("dump_succeeds", [True, False])
def test_removal_dumps_before_drop_and_refuses_drop_on_dump_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dump_succeeds: bool,
) -> None:
    project, target = _project(tmp_path)
    checks = rm.WorktreeChecks(
        shorthand=target.shorthand,
        path=Path(target.path),
        branch=target.branch,
    )
    events: list[str] = []

    monkeypatch.setattr(rm, "_db_exists", lambda name: True)
    monkeypatch.setattr(rm, "_gitignored_content", lambda *args: [])
    monkeypatch.setattr(rm, "_print_cleanup_checklist", lambda *args: None)
    monkeypatch.setattr(rm, "rescue_worktree_state", lambda **kwargs: [])

    def fake_dump(name: str):
        events.append("dump")
        if dump_succeeds:
            return tmp_path / "dump.sql.gz", None
        return None, "pg_dump failed"

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "worktree", "remove"]:
            events.append("remove")
        elif cmd[0] == "dropdb":
            events.append("drop")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(rm, "_dump_database", fake_dump)
    monkeypatch.setattr(rm.subprocess, "run", fake_run)

    assert rm._remove_one(
        project,
        target,
        checks,
        assume_yes=True,
        worktrees=[target],
    )
    assert events[:2] == ["dump", "remove"]
    assert events == (["dump", "remove", "drop"] if dump_succeeds else ["dump", "remove"])


def test_database_dump_is_compressed_and_timestamped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pg_dump = bin_dir / "pg_dump"
    pg_dump.write_text("#!/bin/sh\nprintf 'fixture dump'\n")
    pg_dump.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    destination, error = rm._dump_database(
        "brain_app_feature",
        home=tmp_path / "home",
        now=datetime(2026, 7, 17, 12, 34, 56),
    )

    assert error is None
    assert destination is not None
    assert destination.name == "brain_app_feature-2026-07-17-123456.sql.gz"
    decompressed = subprocess.run(
        ["gzip", "-dc", str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert decompressed.stdout == "fixture dump"


def test_database_dump_expiry_removes_only_files_older_than_30_days(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 17, 12, 0, 0)
    dump_dir = tmp_path / "home/.wt-history/db-dumps"
    dump_dir.mkdir(parents=True)
    old = dump_dir / "old-2026-06-01-000000.sql.gz"
    fresh = dump_dir / "fresh-2026-07-01-000000.sql.gz"
    old.write_bytes(b"old")
    fresh.write_bytes(b"fresh")
    old_time = (now - timedelta(days=31)).timestamp()
    fresh_time = (now - timedelta(days=29)).timestamp()
    os.utime(old, (old_time, old_time))
    os.utime(fresh, (fresh_time, fresh_time))

    removed = rm._expire_old_db_dumps(home=tmp_path / "home", now=now)

    assert removed == [old]
    assert not old.exists()
    assert fresh.exists()
