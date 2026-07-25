"""Local served-data farm — union a tenant's package data with its served snapshot.

Production and local development disagree about which tree holds real data. In
production a pull worker materialises every table into ``<tenant>--served/data``
and the package clone carries pointers only. Locally the inversion holds: the
package tree has the real parquets and the served sibling holds just whatever
was last refreshed (on warwick: 2 of 25 tables). An app that reads the served
tree — because that is the correct thing to do in production — therefore sees a
near-empty dataset locally, and says so to the user.

`wt` fixes the *environment*, never the app: it builds a per-worktree farm of
symlinks unioning both trees and points the app's served-root env var at it.
Zero bytes are copied. Where a table exists in both trees the package copy wins,
so the farm is one coherent vintage — mixed vintages are worse than slightly
stale ones for reproducible local testing.

Everything here is driven by `served_data:` in `.wt.yaml`; there is no project
knowledge in this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .envfile import patch_env
from .manifest import Manifest, ServedDataConfig


PACKAGE = "package"
SERVED = "served"


class FarmError(Exception):
    """The farm directory holds something we refuse to touch."""


@dataclass(frozen=True)
class FarmEntry:
    table: str
    source: Path
    origin: str  # PACKAGE | SERVED


@dataclass
class FarmPlan:
    """What the farm should contain. `skip_reason` set means: don't build one."""

    farm: Path
    default_served_root: Path
    entries: list[FarmEntry] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    skip_reason: str | None = None

    @property
    def wanted(self) -> bool:
        return self.skip_reason is None


@dataclass
class FarmResult:
    farm: Path
    served_root: Path
    linked: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    entries: list[FarmEntry] = field(default_factory=list)
    skipped: str | None = None

    @property
    def table_count(self) -> int:
        return len(self.entries)


def package_data_dir(config: ServedDataConfig, tenant_path: Path) -> Path:
    return tenant_path / config.data_subdir


def served_data_dir(config: ServedDataConfig, tenant_path: Path) -> Path:
    """The app's default served root: the `--served` sibling of the package."""
    sibling = tenant_path.parent / f"{tenant_path.name}{config.served_suffix}"
    return sibling / config.data_subdir


def farm_dir(config: ServedDataConfig, worktree: Path) -> Path:
    return worktree / config.farm_dir


def _tables(root: Path, require_file: str | None) -> tuple[dict[str, Path], list[str]]:
    """Map table-name → directory, plus the names pruned for lacking data.

    A tenant package's `data/` can hold non-table directories (warwick keeps a
    `parquets/` folder of retired snapshots). Anything without `require_file`
    would otherwise show up as a bogus table in the app's listing.
    """
    tables: dict[str, Path] = {}
    pruned: list[str] = []
    if not root.is_dir():
        return tables, pruned
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if require_file and not (child / require_file).exists():
            pruned.append(child.name)
            continue
        tables[child.name] = child
    return tables, pruned


def plan_farm(
    config: ServedDataConfig, *, worktree: Path, tenant_path: Path
) -> FarmPlan:
    package_root = package_data_dir(config, tenant_path)
    served_root = served_data_dir(config, tenant_path)
    plan = FarmPlan(
        farm=farm_dir(config, worktree),
        default_served_root=served_root,
    )

    package_tables, package_pruned = _tables(package_root, config.require_file)
    served_tables, _ = _tables(served_root, config.require_file)

    if not package_tables and not served_tables:
        plan.skip_reason = f"no data tables under {package_root} or {served_root}"
        return plan

    # The served snapshot already being a superset means the app's default is
    # correct as-is; a farm would add indirection and buy nothing.
    if package_tables and set(package_tables) <= set(served_tables):
        plan.skip_reason = (
            f"served snapshot already covers all {len(package_tables)} package tables"
        )
        return plan

    entries = [
        FarmEntry(table=name, source=path, origin=PACKAGE)
        for name, path in package_tables.items()
    ]
    # Tables that exist ONLY in the snapshot (warwick: `prices`). Dropping these
    # would silently shrink the table set rather than grow it.
    entries += [
        FarmEntry(table=name, source=path, origin=SERVED)
        for name, path in served_tables.items()
        if name not in package_tables
    ]
    plan.entries = sorted(entries, key=lambda e: e.table)
    plan.pruned = package_pruned
    return plan


def _clear_symlinks(farm: Path, keep: set[str]) -> list[str]:
    """Remove farm entries not in `keep`. Only ever unlinks symlinks."""
    removed: list[str] = []
    if not farm.is_dir():
        return removed
    for child in sorted(farm.iterdir()):
        if child.name in keep:
            continue
        if not child.is_symlink():
            raise FarmError(
                f"{child} is not a symlink; refusing to remove it. Move it aside "
                f"and re-run."
            )
        child.unlink()
        removed.append(child.name)
    return removed


def build_farm(plan: FarmPlan) -> FarmResult:
    """Materialise `plan` as symlinks. Idempotent; never deletes real files."""
    farm = plan.farm
    result = FarmResult(
        farm=farm,
        served_root=farm,
        pruned=plan.pruned,
        entries=plan.entries,
    )

    if farm.exists() and not farm.is_dir():
        raise FarmError(f"{farm} exists and is not a directory")
    farm.mkdir(parents=True, exist_ok=True)

    for entry in plan.entries:
        target = farm / entry.table
        if target.is_symlink():
            if Path(os.readlink(target)) == entry.source:
                result.unchanged.append(entry.table)
                continue
            target.unlink()
        elif target.exists():
            raise FarmError(
                f"{target} is a real file or directory, not a farm symlink; "
                f"refusing to replace it."
            )
        target.symlink_to(entry.source, target_is_directory=True)
        result.linked.append(entry.table)

    result.removed = _clear_symlinks(farm, {e.table for e in plan.entries})
    return result


def teardown_farm(plan: FarmPlan) -> FarmResult:
    """Empty a farm we no longer want and fall back to the app's default root.

    Called when a worktree moves to a tenant that needs no farm — leaving the
    old symlinks in place would silently keep serving the previous tenant's data.
    """
    result = FarmResult(
        farm=plan.farm,
        served_root=plan.default_served_root,
        skipped=plan.skip_reason,
    )
    if plan.farm.is_dir():
        result.removed = _clear_symlinks(plan.farm, set())
    return result


def apply_farm(
    manifest: Manifest, *, worktree: Path, tenant_path: Path | None
) -> FarmResult | None:
    """Bring a worktree's farm and served-root env var in line with its tenant.

    Returns None when the project or worktree has nothing to do. Raises
    `FarmError` if the farm directory holds anything we won't touch. Writing
    the env var on the skip path matters as much as on the build path: a
    worktree moving to a tenant that needs no farm must stop pointing at the
    previous tenant's farm.
    """
    config = manifest.served_data
    if config is None or tenant_path is None:
        return None

    plan = plan_farm(config, worktree=worktree, tenant_path=Path(tenant_path))
    result = build_farm(plan) if plan.wanted else teardown_farm(plan)

    env_target = worktree / config.env_file
    env_target.parent.mkdir(parents=True, exist_ok=True)
    patch_env(env_target, {config.env_var: str(result.served_root)})
    return result
