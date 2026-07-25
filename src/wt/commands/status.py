"""`wt status` — table of worktrees, ports, db, tenant, running services."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..importer import derive_worktrees
from ..ports import is_port_bound
from ..project import Project
from ..served import farm_dir
from ..types import PRIMARY, Worktree


def _running_cell(wt: Worktree) -> str:
    parts: list[str] = []
    for service, port in wt.ports.items():
        mark = "[green]✓[/green]" if is_port_bound(port) else "[dim]✗[/dim]"
        parts.append(f"{service[:2]}{mark}")
    return " ".join(parts) if parts else "—"


def _served_cell(project: Project, wt: Worktree) -> str:
    """Where this worktree's app reads served data from, and how much is there."""
    config = project.manifest.served_data
    if config is None or wt.served_root is None:
        return "[dim]—[/dim]"
    root = Path(wt.served_root)
    tables = (
        len([c for c in root.iterdir() if c.is_dir() and not c.name.startswith(".")])
        if root.is_dir()
        else 0
    )
    if root == farm_dir(config, Path(wt.path)):
        return f"[green]farm {tables}[/green]"
    return f"[dim]snapshot {tables}[/dim]"


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
