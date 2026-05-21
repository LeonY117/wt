"""Per-project JSON registry at ~/.config/wt/<project>.json. Atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field


PRIMARY = "(primary)"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    d = root / "wt"
    d.mkdir(parents=True, exist_ok=True)
    return d


def registry_path(project: str) -> Path:
    return config_dir() / f"{project}.json"


class Worktree(BaseModel):
    shorthand: str
    path: str
    branch: str | None = None
    ports: dict[str, int] = Field(default_factory=dict)
    db: str | None = None
    tenant: str | None = None
    tenant_path: str | None = None


class Registry(BaseModel):
    project: str
    project_root: str
    worktrees: list[Worktree] = Field(default_factory=list)

    @classmethod
    def load(cls, project: str, project_root: Path) -> "Registry":
        path = registry_path(project)
        if not path.exists():
            return cls(project=project, project_root=str(project_root))
        with path.open() as f:
            return cls.model_validate(json.load(f))

    def save(self) -> None:
        path = registry_path(self.project)
        # Atomic write: tempfile in same dir + rename.
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False, suffix=".tmp"
        ) as tf:
            json.dump(self.model_dump(), tf, indent=2)
            tf.write("\n")
            tmp_name = tf.name
        os.replace(tmp_name, path)

    def find(self, shorthand: str) -> Worktree | None:
        for w in self.worktrees:
            if w.shorthand == shorthand:
                return w
        return None

    def upsert(self, wt: Worktree) -> None:
        for i, existing in enumerate(self.worktrees):
            if existing.shorthand == wt.shorthand:
                self.worktrees[i] = wt
                return
        self.worktrees.append(wt)

    def remove(self, shorthand: str) -> bool:
        for i, w in enumerate(self.worktrees):
            if w.shorthand == shorthand:
                del self.worktrees[i]
                return True
        return False
