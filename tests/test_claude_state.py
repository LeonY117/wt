from __future__ import annotations

from datetime import date
from pathlib import Path

from wt.claude_state import project_dir, rescue_state, rescue_worktree_state


TODAY = date(2026, 7, 15)


def test_rescue_moves_sessions_and_archives_memory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = tmp_path / "brain-app--feature"
    primary = tmp_path / "brain-app"
    source_dir = project_dir(source, home)
    source_dir.mkdir(parents=True)
    (source_dir / "one.jsonl").write_text("one")
    (source_dir / "two.jsonl").write_text("two")
    (source_dir / "memory").mkdir()
    (source_dir / "memory" / "MEMORY.md").write_text("remember")

    result = rescue_state(
        source=source,
        primary=primary,
        project="brain-app",
        shorthand="feature",
        home=home,
        on_date=TODAY,
    )

    destination = project_dir(primary, home)
    archive = home / ".wt-history/claude-state/brain-app-feature-2026-07-15"
    assert result.sessions_moved == 2
    assert result.archive == archive
    assert (destination / "one.jsonl").read_text() == "one"
    assert (destination / "two.jsonl").read_text() == "two"
    assert (archive / "memory" / "MEMORY.md").read_text() == "remember"
    assert not source_dir.exists()


def test_rescue_never_overwrites_session_collision(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = tmp_path / "brain-app--feature"
    primary = tmp_path / "brain-app"
    source_dir = project_dir(source, home)
    destination = project_dir(primary, home)
    source_dir.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source_dir / "same.jsonl").write_text("source")
    (destination / "same.jsonl").write_text("destination")

    result = rescue_state(
        source=source,
        primary=primary,
        project="brain-app",
        shorthand="feature",
        home=home,
        on_date=TODAY,
    )

    archive = home / ".wt-history/claude-state/brain-app-feature-2026-07-15"
    assert result.sessions_moved == 0
    assert result.collisions == ["same.jsonl"]
    assert (destination / "same.jsonl").read_text() == "destination"
    assert (archive / "same.jsonl").read_text() == "source"


def test_rescue_missing_and_empty_project_dirs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = tmp_path / "brain-app--feature"
    primary = tmp_path / "brain-app"

    missing = rescue_state(
        source=source,
        primary=primary,
        project="brain-app",
        shorthand="feature",
        home=home,
        on_date=TODAY,
    )
    assert not missing.found

    source_dir = project_dir(source, home)
    source_dir.mkdir(parents=True)
    empty = rescue_state(
        source=source,
        primary=primary,
        project="brain-app",
        shorthand="feature",
        home=home,
        on_date=TODAY,
    )
    assert empty.found
    assert empty.sessions_moved == 0
    assert empty.archive is None
    assert not source_dir.exists()


def test_worktree_rescue_includes_subdirectory_launched_state(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = tmp_path / "brain-app--feature"
    primary = tmp_path / "brain-app"
    root_state = project_dir(source, home)
    backend_state = project_dir(source / "backend", home)
    for state, session in (
        (root_state, "root.jsonl"),
        (backend_state, "backend.jsonl"),
    ):
        state.mkdir(parents=True)
        (state / session).write_text(session)
        (state / "memory").mkdir()
        (state / "memory/MEMORY.md").write_text(session)

    results = rescue_worktree_state(
        source=source,
        primary=primary,
        project="brain-app",
        shorthand="feature",
        home=home,
        on_date=TODAY,
    )

    destination = project_dir(primary, home)
    history = home / ".wt-history/claude-state"
    assert len(results) == 2
    assert {result.sessions_moved for result in results} == {1}
    assert (destination / "root.jsonl").exists()
    assert (destination / "backend.jsonl").exists()
    assert (history / "brain-app-feature-2026-07-15/memory/MEMORY.md").exists()
    assert (
        history
        / "brain-app-feature-backend-2026-07-15/memory/MEMORY.md"
    ).exists()


def test_worktree_rescue_skips_lossy_munge_collision_with_existing_sibling(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    source = tmp_path / "brain-app--feature"
    sibling = tmp_path / "brain-app--feature--served"
    primary = tmp_path / "brain-app"
    source.mkdir()
    sibling.mkdir()
    primary.mkdir()
    backend_state = project_dir(source / "backend", home)
    sibling_state = project_dir(sibling, home)
    for state, session in (
        (backend_state, "backend.jsonl"),
        (sibling_state, "sibling.jsonl"),
    ):
        state.mkdir(parents=True)
        (state / session).write_text(session)

    results = rescue_worktree_state(
        source=source,
        primary=primary,
        project="brain-app",
        shorthand="feature",
        home=home,
        on_date=TODAY,
    )

    assert len(results) == 1
    assert results[0].source == backend_state
    assert (project_dir(primary, home) / "backend.jsonl").exists()
    assert (sibling_state / "sibling.jsonl").exists()
