from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from wt.commands import new as new_cmd
from wt.commands import tenant as tenant_cmd
from wt.envfile import read_env
from wt.manifest import EnvPatch, Manifest, ServedDataConfig, TenantConfig
from wt.served import FarmError, PACKAGE, SERVED, apply_farm, build_farm, plan_farm
from wt.tenant import list_tenants
from wt.types import Worktree


def _table(root: Path, name: str, *, data: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True)
    if data:
        (d / "data.parquet").write_bytes(b"PAR1")
    return d


def _tenant_tree(
    tmp_path: Path,
    *,
    name: str = "warwick-prod",
    package_tables: tuple[str, ...] = ("products", "customers", "product-list"),
    served_tables: tuple[str, ...] = ("prices", "product-list"),
    junk: tuple[str, ...] = ("parquets",),
) -> Path:
    """Reproduce the warwick shape: fat package tree, thin served snapshot."""
    root = tmp_path / "tenants"
    package = root / name
    for t in package_tables:
        _table(package / "data", t)
    for t in junk:
        _table(package / "data", t, data=False)
    served = root / f"{name}--served"
    for t in served_tables:
        _table(served / "data", t)
    (package / "manifest.yaml").write_text("name: warwick\n")
    return package


def _config(**kwargs) -> ServedDataConfig:
    return ServedDataConfig(env_var="SERVED_DATA_ROOT", **kwargs)


# ---- planning -------------------------------------------------------------


def test_plan_unions_both_trees_preferring_the_package_copy(tmp_path: Path) -> None:
    package = _tenant_tree(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    plan = plan_farm(_config(), worktree=worktree, tenant_path=package)

    assert plan.wanted
    origins = {e.table: e.origin for e in plan.entries}
    assert origins == {
        "products": PACKAGE,
        "customers": PACKAGE,
        # Present in both trees at different vintages — the package wins so the
        # farm is one coherent set.
        "product-list": PACKAGE,
        # Present ONLY in the snapshot; dropping it would shrink the table set.
        "prices": SERVED,
    }
    assert plan.pruned == ["parquets"]


def test_plan_skips_when_the_snapshot_already_covers_the_package(
    tmp_path: Path,
) -> None:
    package = _tenant_tree(
        tmp_path,
        package_tables=("products",),
        served_tables=("products", "prices"),
        junk=(),
    )
    worktree = tmp_path / "wt"
    worktree.mkdir()

    plan = plan_farm(_config(), worktree=worktree, tenant_path=package)

    assert not plan.wanted
    assert "already covers" in (plan.skip_reason or "")


def test_plan_skips_a_tenant_with_no_data_at_all(tmp_path: Path) -> None:
    # kingfisher-prod's real shape today: package and snapshot both empty.
    package = tmp_path / "tenants" / "kingfisher-prod"
    package.mkdir(parents=True)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    plan = plan_farm(_config(), worktree=worktree, tenant_path=package)

    assert not plan.wanted
    assert "no data tables" in (plan.skip_reason or "")


# ---- building -------------------------------------------------------------


def test_build_links_every_table_without_copying_bytes(tmp_path: Path) -> None:
    package = _tenant_tree(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    result = build_farm(plan_farm(_config(), worktree=worktree, tenant_path=package))

    farm = worktree / ".local-served" / "data"
    assert sorted(p.name for p in farm.iterdir()) == [
        "customers",
        "prices",
        "product-list",
        "products",
    ]
    assert all(p.is_symlink() for p in farm.iterdir())
    assert (farm / "products" / "data.parquet").exists()
    assert (farm / "prices").resolve() == (
        package.parent / "warwick-prod--served" / "data" / "prices"
    )
    assert result.table_count == 4
    assert sorted(result.linked) == ["customers", "prices", "product-list", "products"]


def test_rebuild_is_idempotent_and_tracks_table_set_changes(tmp_path: Path) -> None:
    package = _tenant_tree(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    config = _config()

    build_farm(plan_farm(config, worktree=worktree, tenant_path=package))
    second = build_farm(plan_farm(config, worktree=worktree, tenant_path=package))
    assert second.linked == []
    assert len(second.unchanged) == 4

    # A `dvc pull` adds a table and a refresh retires another.
    _table(package / "data", "invoices")
    (package / "data" / "customers" / "data.parquet").unlink()
    third = build_farm(plan_farm(config, worktree=worktree, tenant_path=package))

    assert third.linked == ["invoices"]
    assert third.removed == ["customers"]
    farm = worktree / ".local-served" / "data"
    assert "customers" not in {p.name for p in farm.iterdir()}


def test_build_refuses_to_replace_real_content(tmp_path: Path) -> None:
    package = _tenant_tree(tmp_path)
    worktree = tmp_path / "wt"
    farm = worktree / ".local-served" / "data"
    _table(farm, "products")  # a real directory where a symlink belongs

    with pytest.raises(FarmError, match="real file or directory"):
        build_farm(plan_farm(_config(), worktree=worktree, tenant_path=package))

    assert (farm / "products" / "data.parquet").exists()


# ---- apply (farm + env var together) --------------------------------------


def _manifest(tenant_root: Path) -> Manifest:
    return Manifest(
        project="brain-app",
        worktree_prefix="brain-app--",
        services=[],
        env_patches=[EnvPatch(file=".env", set={"DEPLOYMENT_ROOT": "{tenant_path}"})],
        tenant=TenantConfig(
            env_var="DEPLOYMENT_ROOT",
            search_paths=[str(tenant_root)],
            identity_env="APP_TENANT",
            identity_source="name",
        ),
        served_data=_config(),
    )


def test_apply_points_the_env_var_at_the_farm(tmp_path: Path) -> None:
    package = _tenant_tree(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".env").write_text("DEPLOYMENT_ROOT=/old\n")

    result = apply_farm(
        _manifest(package.parent), worktree=worktree, tenant_path=package
    )

    farm = worktree / ".local-served" / "data"
    assert result is not None and result.served_root == farm
    assert read_env(worktree / ".env")["SERVED_DATA_ROOT"] == str(farm)


def test_apply_to_a_tenant_needing_no_farm_clears_the_previous_one(
    tmp_path: Path,
) -> None:
    """The failure this guards: a repointed worktree still serving old tables."""
    fat = _tenant_tree(tmp_path)
    lean = _tenant_tree(
        tmp_path,
        name="kfd-prod",
        package_tables=("products",),
        served_tables=("products",),
        junk=(),
    )
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".env").write_text("DEPLOYMENT_ROOT=/old\n")
    manifest = _manifest(fat.parent)

    apply_farm(manifest, worktree=worktree, tenant_path=fat)
    result = apply_farm(manifest, worktree=worktree, tenant_path=lean)

    farm = worktree / ".local-served" / "data"
    assert list(farm.iterdir()) == []
    assert result is not None and result.skipped is not None
    assert read_env(worktree / ".env")["SERVED_DATA_ROOT"] == str(
        lean.parent / "kfd-prod--served" / "data"
    )


# ---- command integration --------------------------------------------------


def test_new_provisions_the_farm_automatically(repo_factory, tmp_path: Path) -> None:
    repo = repo_factory()
    package = _tenant_tree(tmp_path)
    (repo / ".env").write_text("DEPLOYMENT_ROOT=/old\nAPP_TENANT=old\n")
    (repo / ".wt.yaml").write_text(
        yaml.safe_dump(
            {
                "project": "brain-app",
                "worktree_prefix": "brain-app--",
                "services": [],
                "env_patches": [
                    {"file": ".env", "set": {"DEPLOYMENT_ROOT": "{tenant_path}"}}
                ],
                "tenant": {
                    "env_var": "DEPLOYMENT_ROOT",
                    "search_paths": [str(package.parent)],
                    "identity_env": "APP_TENANT",
                    "identity_source": "name",
                },
                "served_data": {"env_var": "SERVED_DATA_ROOT"},
            },
            sort_keys=False,
        )
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "served fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    result = new_cmd.run(
        start=repo,
        shorthand="proj-base",
        branch=None,
        tenant="warwick-prod",
        skip_migrate=True,
    )

    target = tmp_path / "brain-app--proj-base"
    farm = target / ".local-served" / "data"
    assert result == 0
    assert read_env(target / ".env")["SERVED_DATA_ROOT"] == str(farm)
    assert len(list(farm.iterdir())) == 4


def test_tenant_switch_rebuilds_the_farm_for_the_new_tenant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fat = _tenant_tree(tmp_path)
    other = _tenant_tree(
        tmp_path,
        name="kfd-prod",
        package_tables=("orders", "sites"),
        served_tables=("orders",),
        junk=(),
    )
    worktree = tmp_path / "brain-app--data-tab"
    worktree.mkdir()
    (worktree / ".env").write_text("DEPLOYMENT_ROOT=/old\nAPP_TENANT=old\n")
    manifest = _manifest(fat.parent)
    entry = Worktree(shorthand="data-tab", path=str(worktree), tenant="warwick-prod")
    project = SimpleNamespace(manifest=manifest, find=lambda s: entry)
    monkeypatch.setattr(tenant_cmd.Project, "discover", lambda start: project)
    monkeypatch.setattr(tenant_cmd, "is_port_bound", lambda port: False)

    apply_farm(manifest, worktree=worktree, tenant_path=fat)
    assert tenant_cmd.set_run(tmp_path, "data-tab", "kfd-prod") == 0

    farm = worktree / ".local-served" / "data"
    assert sorted(p.name for p in farm.iterdir()) == ["orders", "sites"]
    assert (farm / "orders").resolve() == other / "data" / "orders"
    assert read_env(worktree / ".env")["SERVED_DATA_ROOT"] == str(farm)


def test_served_siblings_are_not_listed_as_tenants(tmp_path: Path) -> None:
    package = _tenant_tree(tmp_path)
    # A directory that merely ends in the suffix, with no package beside it,
    # is a real tenant and must stay listable.
    (package.parent / "standalone--served").mkdir()

    names = [n for n, _ in list_tenants(_manifest(package.parent))]

    assert "warwick-prod" in names
    assert "warwick-prod--served" not in names
    assert "standalone--served" in names
