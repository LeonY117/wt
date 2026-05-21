"""Tenant package resolution (DEPLOYMENT_ROOT pointing)."""

from __future__ import annotations

from pathlib import Path

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
