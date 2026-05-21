"""One-shot auto-import: register git-existing worktrees we don't know about."""

from __future__ import annotations

import re
from pathlib import Path

from .envfile import read_env
from .manifest import ImportHint, Manifest
from .project import Project
from .registry import Worktree
from .tenant import tenant_name_from_path


def _extract(hint: ImportHint, env: dict[str, str]) -> str | None:
    raw = env.get(hint.key)
    if raw is None:
        return None
    m = re.search(hint.pattern, raw)
    if not m:
        return None
    return m.group(1) if m.groups() else m.group(0)


def _read_env_at(worktree_path: Path, relpath: str) -> dict[str, str]:
    return read_env(worktree_path / relpath)


def infer_worktree(project: Project, *, path: Path, branch: str | None) -> Worktree:
    """Build a Worktree by inferring fields from the worktree's env files.

    Falls back to service defaults / Nones where inference fails.
    """
    manifest = project.manifest
    shorthand = project.derive_shorthand(path)

    # Cache env reads by file.
    env_cache: dict[str, dict[str, str]] = {}

    def env_for(relpath: str) -> dict[str, str]:
        if relpath not in env_cache:
            env_cache[relpath] = _read_env_at(path, relpath)
        return env_cache[relpath]

    # Ports
    ports: dict[str, int] = {}
    for service in manifest.services:
        hint = manifest.import_hints.ports.get(service.name)
        port = None
        if hint is not None:
            val = _extract(hint, env_for(hint.file))
            if val and val.isdigit():
                port = int(val)
        ports[service.name] = port if port is not None else service.default_port

    # DB
    db_name: str | None = None
    if manifest.import_hints.db is not None:
        db_name = _extract(manifest.import_hints.db, env_for(manifest.import_hints.db.file))

    # Tenant
    tenant_name: str | None = None
    tenant_path: str | None = None
    if manifest.import_hints.tenant is not None and manifest.tenant is not None:
        raw_path = _extract(
            manifest.import_hints.tenant,
            env_for(manifest.import_hints.tenant.file),
        )
        if raw_path:
            tenant_path = str(Path(raw_path).expanduser().resolve())
            tenant_name = tenant_name_from_path(manifest, raw_path)

    return Worktree(
        shorthand=shorthand,
        path=str(path.resolve()),
        branch=branch,
        ports=ports,
        db=db_name,
        tenant=tenant_name,
        tenant_path=tenant_path,
    )


def import_missing(project: Project) -> list[str]:
    """Add registry entries for any git worktree not yet known.

    Returns list of shorthands imported.
    """
    known = project.known_shorthands()
    added: list[str] = []
    for entry in project.all_git_worktrees():
        wt_path = Path(entry["path"])
        shorthand = project.derive_shorthand(wt_path)
        if shorthand in known:
            continue
        wt = infer_worktree(project, path=wt_path, branch=entry.get("branch"))
        project.registry.upsert(wt)
        added.append(shorthand)
    if added:
        project.save()
    return added
