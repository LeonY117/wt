from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wt.commands import new as new_cmd
from wt.manifest import Manifest
from wt.project import shorthand_for


def _write_manifest(path: Path, worktree_root: str) -> Manifest:
    path.mkdir(parents=True)
    manifest_path = path / ".wt.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "worktree_prefix: brain-app--",
                f"worktree_root: {worktree_root}",
                "services: []",
            ]
        )
        + "\n"
    )
    return Manifest.load(manifest_path)


def test_worktree_root_resolves_relative_to_primary(tmp_path: Path) -> None:
    primary = tmp_path / "repos" / "brain-app"
    manifest = _write_manifest(primary, "../brain-app--worktrees")

    assert manifest.resolved_worktree_root() == (
        primary.parent / "brain-app--worktrees"
    ).resolve()


def test_worktree_root_can_be_anchored_to_discovered_primary(tmp_path: Path) -> None:
    linked = tmp_path / "linked" / "feature"
    primary = tmp_path / "repos" / "brain-app"
    manifest = _write_manifest(linked, "../brain-app--worktrees")

    assert manifest.resolved_worktree_root(primary) == (
        primary / "../brain-app--worktrees"
    ).resolve()


def test_worktree_root_accepts_absolute_path(tmp_path: Path) -> None:
    primary = tmp_path / "repos" / "brain-app"
    absolute = tmp_path / "elsewhere" / "trees"
    manifest = _write_manifest(primary, str(absolute))

    assert manifest.resolved_worktree_root() == absolute.resolve()


def test_worktree_root_expands_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    primary = tmp_path / "repos" / "brain-app"
    manifest = _write_manifest(primary, "~/worktrees/brain-app")

    assert manifest.resolved_worktree_root() == (home / "worktrees/brain-app").resolve()


@pytest.mark.parametrize(
    ("worktree_root", "expected"),
    [
        (None, "brain-app--feature"),
        ("../brain-app--worktrees", "brain-app--worktrees/feature"),
    ],
)
def test_new_uses_configured_or_legacy_target(
    repo_factory, tmp_path: Path, worktree_root: str | None, expected: str
) -> None:
    repo = repo_factory(worktree_root=worktree_root)

    result = new_cmd.run(
        start=repo,
        shorthand="feature",
        branch=None,
        tenant=None,
        skip_migrate=True,
    )

    target = tmp_path / expected
    assert result == 0
    assert target.is_dir()
    listed = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert f"worktree {target}" in listed


def test_shorthand_for_bare_named_directory(tmp_path: Path) -> None:
    primary = tmp_path / "brain-app"
    bare = tmp_path / "brain-app--worktrees" / "plain-feature"

    assert shorthand_for(bare, primary, "brain-app--") == "plain-feature"


def test_new_rolls_back_custom_root_worktree_on_failure(
    repo_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repo_factory(worktree_root="../brain-app--worktrees")
    target = tmp_path / "brain-app--worktrees" / "broken"

    def fail_after_add(**kwargs) -> None:
        raise subprocess.CalledProcessError(1, "patch env")

    monkeypatch.setattr(new_cmd, "_apply_env_patches", fail_after_add)

    result = new_cmd.run(
        start=repo,
        shorthand="broken",
        branch=None,
        tenant=None,
        skip_migrate=True,
    )

    assert result == 1
    assert not target.exists()
    listed = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert str(target) not in listed
