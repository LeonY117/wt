"""`served_data:` is now read-only config: wt reports the tree, never builds it."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from wt.commands import status as status_cmd
from wt.manifest import Manifest, ServedDataConfig, TenantConfig
from wt.tenant import list_tenants, served_root
from wt.types import Worktree


def _manifest(search_path: Path | None = None) -> Manifest:
    return Manifest(
        project="brain-app",
        worktree_prefix="brain-app--",
        services=[],
        served_data=ServedDataConfig(),
        tenant=TenantConfig(
            env_var="DEPLOYMENT_ROOT",
            search_paths=[str(search_path)] if search_path else [],
        ),
    )


def _tenant_tree(tmp_path: Path, *, stamp: str, tables: int = 3) -> Path:
    """A package plus the served sibling a snapshot tool publishes beside it."""
    package = tmp_path / "tenants" / "warwick-prod"
    package.mkdir(parents=True)
    tree = package.parent / "warwick-prod--served" / f"sys-snap-{stamp}-abc123"
    for i in range(tables):
        (tree / f"table{i}").mkdir(parents=True)
    (package.parent / "warwick-prod--served" / "data").symlink_to(tree.name)
    return package


def test_served_root_is_derived_from_the_package_never_from_an_env_var(
    tmp_path: Path,
) -> None:
    package = _tenant_tree(tmp_path, stamp="20200105T101500Z")

    root = served_root(_manifest(), str(package))

    # the app's own default resolves to this same path — wt only reads it
    assert root == package.parent / "warwick-prod--served" / "data"
    assert root.is_dir()


def test_status_reports_the_vintage_of_the_shared_tree(tmp_path: Path) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y%m%dT%H%M%SZ")
    package = _tenant_tree(tmp_path, stamp=stamp, tables=27)
    project = SimpleNamespace(manifest=_manifest())
    wt = Worktree(shorthand="fix", path=str(tmp_path / "wt"), tenant_path=str(package))

    cell = status_cmd._served_cell(project, wt)

    assert "27" in cell and "2d" in cell


def test_status_says_none_when_no_tree_has_been_published(tmp_path: Path) -> None:
    package = tmp_path / "tenants" / "warwick-prod"
    package.mkdir(parents=True)
    project = SimpleNamespace(manifest=_manifest())
    wt = Worktree(shorthand="fix", path=str(tmp_path / "wt"), tenant_path=str(package))

    assert "none" in status_cmd._served_cell(project, wt)


def test_status_falls_back_to_mtime_for_an_unstamped_tree(tmp_path: Path) -> None:
    package = tmp_path / "tenants" / "warwick-prod"
    (package.parent / "warwick-prod--served" / "data" / "customers").mkdir(parents=True)
    package.mkdir(parents=True)
    root = package.parent / "warwick-prod--served" / "data"
    old = (datetime.now(timezone.utc) - timedelta(days=9)).timestamp()
    os.utime(root, (old, old))
    project = SimpleNamespace(manifest=_manifest())
    wt = Worktree(shorthand="fix", path=str(tmp_path / "wt"), tenant_path=str(package))

    assert "9d" in status_cmd._served_cell(project, wt)


def test_served_siblings_stay_out_of_the_tenant_list(tmp_path: Path) -> None:
    package = _tenant_tree(tmp_path, stamp="20200105T101500Z")

    names = [name for name, _ in list_tenants(_manifest(package.parent))]

    assert names == ["warwick-prod"]  # never the --served sibling
