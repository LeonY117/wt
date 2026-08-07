"""`served_data:` is now read-only config: wt reports the tree, never builds it."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

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


def test_a_suffix_named_dir_without_a_package_is_still_a_tenant(tmp_path: Path) -> None:
    """`--served` only hides a sibling; a tenant that merely ends in it stays."""
    search = tmp_path / "tenants"
    (search / "acme--served").mkdir(parents=True)  # no `acme` package beside it

    assert [n for n, _ in list_tenants(_manifest(search))] == ["acme--served"]


def test_an_unparseable_stamp_does_not_take_down_the_status_table(
    tmp_path: Path,
) -> None:
    package = tmp_path / "tenants" / "warwick-prod"
    package.mkdir(parents=True)
    served = package.parent / "warwick-prod--served"
    (served / "sys-snap-20261332T000000Z-abc123" / "customers").mkdir(parents=True)
    (served / "data").symlink_to("sys-snap-20261332T000000Z-abc123")  # month 13
    project = SimpleNamespace(manifest=_manifest())
    wt = Worktree(shorthand="fix", path=str(tmp_path / "wt"), tenant_path=str(package))

    cell = status_cmd._served_cell(project, wt)  # falls back to mtime, never raises

    assert "1" in cell and "d" in cell


def test_the_retired_manifest_keys_are_rejected_not_ignored(tmp_path: Path) -> None:
    """A stale block is the signature of worktrees still carrying a pin."""
    manifest = tmp_path / ".wt.yaml"
    manifest.write_text(
        "worktree_prefix: brain-app--\nservices: []\n"
        "served_data:\n  env_var: SERVED_DATA_ROOT\n  farm_dir: .local-served/data\n"
    )

    with pytest.raises(ValidationError) as excinfo:
        Manifest.load(manifest)

    assert "env_var" in str(excinfo.value) and "farm_dir" in str(excinfo.value)
