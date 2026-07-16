"""Tenant package resolution (DEPLOYMENT_ROOT pointing)."""

from __future__ import annotations

from pathlib import Path

import yaml

from .manifest import Manifest


def list_tenants(manifest: Manifest) -> list[tuple[str, Path]]:
    """Return [(name, absolute_path), ...] across all configured search paths."""
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for base in manifest.expanded_tenant_search_paths():
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith(".") or child.name.startswith("_"):
                continue
            if child.name in seen:
                continue
            seen.add(child.name)
            out.append((child.name, child.resolve()))
    return out


def resolve_tenant(manifest: Manifest, name: str) -> Path | None:
    for n, path in list_tenants(manifest):
        if n == name:
            return path
    return None


def tenant_name_from_path(manifest: Manifest, path: str | None) -> str | None:
    """Reverse-resolve: given a DEPLOYMENT_ROOT path, find which tenant it is."""
    if not path:
        return None
    target = Path(path).expanduser().resolve()
    for name, p in list_tenants(manifest):
        if p == target:
            return name
    # Not under a known search path; fall back to basename so something useful
    # shows up in `wt status`.
    return target.name if target.exists() else None


def tenant_identity_from_path(
    manifest: Manifest, path: Path
) -> tuple[str | None, str | None]:
    """Read the configured tenant identity from a package manifest.

    Returns ``(value, warning)``. The caller must leave the identity env var
    untouched when ``warning`` is present: guessing from a package directory
    name is unsafe because package names and tenant identities can differ.
    ``identity_source`` supports dotted mappings for manifests that nest the
    identity field.
    """
    config = manifest.tenant
    if config is None or config.identity_env is None:
        return None, None
    if not config.identity_source:
        return None, (
            "tenant.identity_env is configured without tenant.identity_source; "
            f"{config.identity_env} was left unchanged"
        )

    package_manifest = path / "manifest.yaml"
    try:
        with package_manifest.open() as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        return None, (
            f"could not read tenant identity from {package_manifest}: {exc}; "
            f"{config.identity_env} was left unchanged"
        )

    value: object = data
    for part in config.identity_source.split("."):
        if not isinstance(value, dict) or part not in value:
            return None, (
                f"tenant identity field {config.identity_source!r} is missing from "
                f"{package_manifest}; {config.identity_env} was left unchanged"
            )
        value = value[part]

    if not isinstance(value, str) or not value.strip():
        return None, (
            f"tenant identity field {config.identity_source!r} in "
            f"{package_manifest} is not a non-empty string; "
            f"{config.identity_env} was left unchanged"
        )
    return value, None
