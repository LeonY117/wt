"""`wt status` — table of worktrees, ports, db, tenant, running services."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..importer import derive_worktrees
from ..ports import is_port_bound
from ..project import Project
from ..tenant import served_root
from ..types import PRIMARY, Worktree


# `<anything>-<UTC stamp>-<hash>` — the snapshot naming both the app's pull
# worker and `sys serve` use for a materialised tree.
_STAMP_RE = re.compile(r"(\d{8}T\d{6})Z")


def _running_cell(wt: Worktree) -> str:
    parts: list[str] = []
    for service, port in wt.ports.items():
        mark = "[green]✓[/green]" if is_port_bound(port) else "[dim]✗[/dim]"
        parts.append(f"{service[:2]}{mark}")
    return " ".join(parts) if parts else "—"


def _vintage(root: Path) -> str:
    """How old the served snapshot is — from the tree it points at, else mtime."""
    m = _STAMP_RE.search(os.readlink(root) if root.is_symlink() else "")
    when = (
        datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        if m
        else datetime.fromtimestamp(root.stat().st_mtime, timezone.utc)
    )
    days = (datetime.now(timezone.utc) - when).days
    return f"{days}d" if days else "<1d"


def _served_cell(project: Project, wt: Worktree) -> str:
    """How fresh the served data this worktree reads is.

    wt neither builds nor wires this tree: the app's own env default points at
    it and the tenant's snapshot tool publishes it, shared by every worktree on
    that tenant. All that is left worth showing is the vintage — the staleness
    a per-worktree farm used to hide.
    """
    if project.manifest.served_data is None or wt.tenant_path is None:
        return "[dim]—[/dim]"
    root = served_root(project.manifest, wt.tenant_path)
    if root is None or not root.is_dir():
        return "[dim]none[/dim]"
    tables = len([c for c in root.iterdir() if c.is_dir() and not c.name.startswith(".")])
    return f"[green]{tables}[/green] [dim]·[/dim] {_vintage(root)}"


def _branch_cell(branch: str | None) -> str:
    if branch is None:
        return "[dim](detached)[/dim]"
    return branch


def run(start: Path) -> int:
    project = Project.discover(start)
    console = Console()

    # Sort: primary first, then by shorthand. Worktrees are derived purely
    # from `git worktree list` + env files — no registry read.
    rows = sorted(
        derive_worktrees(project),
        key=lambda w: (w.shorthand != PRIMARY, w.shorthand),
    )

    table = Table(
        title=f"[bold]{project.manifest.project}[/bold] — {project.primary}",
        title_justify="left",
        show_lines=False,
    )
    table.add_column("shorthand", style="cyan")
    table.add_column("branch")
    for service in project.manifest.services:
        table.add_column(service.name, justify="right")
    table.add_column("db", style="magenta")
    if project.manifest.tenant is not None:
        table.add_column("tenant", style="yellow")
    if project.manifest.served_data is not None:
        table.add_column("served")
    table.add_column("running")

    for wt in rows:
        cells: list[str] = [wt.shorthand, _branch_cell(wt.branch)]
        for service in project.manifest.services:
            port = wt.ports.get(service.name)
            cells.append(str(port) if port is not None else "[dim]—[/dim]")
        cells.append(wt.db or "[dim]—[/dim]")
        if project.manifest.tenant is not None:
            cells.append(wt.tenant or "[dim]—[/dim]")
        if project.manifest.served_data is not None:
            cells.append(_served_cell(project, wt))
        cells.append(_running_cell(wt))
        table.add_row(*cells)

    console.print(table)
    return 0
