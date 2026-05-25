"""Pydantic models for `.wt.yaml`."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Service(BaseModel):
    name: str
    default_port: int


class EnvPatch(BaseModel):
    file: str
    template: str | None = None
    set: dict[str, str] = Field(default_factory=dict)


class DBConfig(BaseModel):
    name_template: str
    migrate: str | None = None


class TenantConfig(BaseModel):
    env_var: str
    search_paths: list[str] = Field(default_factory=list)


class CleanupConfig(BaseModel):
    protected: list[str] = Field(default_factory=list)
    # Substrings (matched against each gitignored path) added to the built-in
    # list of regenerable build-junk patterns that `wt rm` should *not*
    # surface during its pre-removal gitignored-content sweep. Default list
    # is in `commands.rm.DEFAULT_GITIGNORED_EXCLUDES`.
    gitignored_exclude: list[str] = Field(default_factory=list)


class ImportHint(BaseModel):
    file: str
    key: str
    pattern: str = "(.+)"  # regex with one capturing group; default = whole value


class ImportHints(BaseModel):
    """Optional hints used by `wt status` to auto-import existing worktrees."""

    ports: dict[str, ImportHint] = Field(default_factory=dict)
    db: ImportHint | None = None
    tenant: ImportHint | None = None


class Manifest(BaseModel):
    project: str | None = None
    worktree_prefix: str
    services: list[Service]
    env_patches: list[EnvPatch] = Field(default_factory=list)
    db: DBConfig | None = None
    tenant: TenantConfig | None = None
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    import_hints: ImportHints = Field(default_factory=ImportHints)

    project_root: Path = Field(exclude=True, default=Path("."))
    manifest_path: Path = Field(exclude=True, default=Path("."))

    @classmethod
    def load(cls, manifest_path: Path) -> "Manifest":
        with manifest_path.open() as f:
            data = yaml.safe_load(f) or {}
        m = cls.model_validate(data)
        m.manifest_path = manifest_path
        m.project_root = manifest_path.parent
        if m.project is None:
            m.project = m.project_root.name
        return m

    def service(self, name: str) -> Service:
        for s in self.services:
            if s.name == name:
                return s
        raise KeyError(f"service {name!r} not declared in {self.manifest_path}")

    def expanded_tenant_search_paths(self) -> list[Path]:
        if not self.tenant:
            return []
        return [Path(p).expanduser() for p in self.tenant.search_paths]
