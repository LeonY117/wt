"""`wt new --db-from` and `wt rm --purge` against real Postgres and real git worktrees."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from wt import pg
from wt.claude_state import project_dir
from wt.commands import new as new_cmd
from wt.commands import rm
from wt.project import Project

pytestmark = pytest.mark.skipif(
    subprocess.run(["psql", "-lqt"], capture_output=True).returncode != 0,
    reason="needs a reachable local Postgres",
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _sql(db: str, sql: str) -> str:
    return subprocess.run(
        ["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", "-d", db, "-c", sql],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A repo whose manifest names per-test DBs, a source DB with one row, a fake HOME."""
    tag = uuid.uuid4().hex[:8]
    repo = tmp_path / "proj"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "wt-tests@example.com")
    git(repo, "config", "user.name", "wt tests")
    (repo / ".wt.yaml").write_text(
        "project: proj\nworktree_prefix: proj--\nservices: []\n"
        f"db:\n  name_template: \"wt_test_{tag}_{{shorthand_underscored}}\"\n"
        "env_patches:\n  - file: .env\n    set:\n"
        "      DATABASE_URL: \"postgresql://localhost/{db}\"\n"
        "import_hints:\n  db:\n    file: .env\n    key: DATABASE_URL\n"
        "    pattern: \".*/([^/?]+)\"\n"
    )
    (repo / ".gitignore").write_text(".env\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    source = f"wt_test_{tag}_source"
    subprocess.run(["createdb", source], check=True)
    _sql(source, "CREATE TABLE t (v text); INSERT INTO t VALUES ('from-source');")
    yield repo, home, source, tag
    for db in (source, f"wt_test_{tag}_feat", f"wt_test_{tag}_other"):
        subprocess.run(["dropdb", "--if-exists", db], capture_output=True)


def _remove(repo: Path, shorthand: str, **kwargs) -> bool:
    project = Project.discover(repo)
    worktrees = project.worktrees()
    wt = project.find(shorthand, worktrees)
    checks = rm.WorktreeChecks(shorthand=wt.shorthand, path=Path(wt.path), branch=wt.branch)
    return rm._remove_one(project, wt, checks, assume_yes=True, worktrees=worktrees, **kwargs)


def test_db_from_copies_tags_and_rm_drops_the_copy_without_a_dump(scratch) -> None:
    repo, home, source, tag = scratch
    copy = f"wt_test_{tag}_feat"

    assert new_cmd.run(start=repo, shorthand="feat", branch=None, tenant=None, db_from=source) == 0
    assert _sql(copy, "SELECT v FROM t") == "from-source"
    assert pg.copy_source(copy) == source

    assert _remove(repo, "feat")
    assert not pg.db_exists(copy)
    assert pg.db_exists(source)
    assert not (home / ".wt-history" / "db-dumps").exists()


def test_db_from_refuses_a_missing_source_and_an_existing_target(scratch) -> None:
    repo, _, source, tag = scratch

    assert new_cmd.run(
        start=repo, shorthand="feat", branch=None, tenant=None, db_from=f"wt_test_{tag}_nope"
    ) == 2
    subprocess.run(["createdb", f"wt_test_{tag}_other"], check=True)
    assert new_cmd.run(start=repo, shorthand="other", branch=None, tenant=None, db_from=source) == 2
    assert pg.copy_source(f"wt_test_{tag}_other") is None


def test_purge_leaves_no_dump_no_claude_state_and_no_branch(scratch) -> None:
    repo, home, _, tag = scratch
    own = f"wt_test_{tag}_feat"
    assert new_cmd.run(start=repo, shorthand="feat", branch=None, tenant=None) == 0
    assert pg.copy_source(own) is None
    worktree = Project.discover(repo).find("feat").path
    state = project_dir(Path(worktree))
    state.mkdir(parents=True)
    (state / "session.jsonl").write_text("{}\n")

    assert _remove(repo, "feat", purge=True)

    assert not pg.db_exists(own)
    assert not (home / ".wt-history").exists()
    assert not state.exists()
    assert not project_dir(repo).exists()  # sessions were deleted, not moved to the primary
    assert git(repo, "branch", "--list", "feat/feat").strip() == ""
