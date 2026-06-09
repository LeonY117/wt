"""Core domain types — kept registry-free; values are derived from disk."""

from __future__ import annotations

from pydantic import BaseModel, Field


PRIMARY = "(primary)"


class Worktree(BaseModel):
    """In-memory snapshot of one worktree, built by reading git + its env files.

    Not persisted anywhere — every read derives a fresh snapshot. Mutating an
    instance is a no-op for state; writes happen via env-file patches.
    """

    shorthand: str
    path: str
    branch: str | None = None
    ports: dict[str, int] = Field(default_factory=dict)
    db: str | None = None
    tenant: str | None = None
    tenant_path: str | None = None
