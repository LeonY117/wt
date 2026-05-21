"""`wt status` — table of worktrees, ports, db, tenant, running services."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..importer import import_missing
from ..ports import is_port_bound
from ..project import Project
from ..registry import PRIMARY, Worktree


def _running_cell(wt: Worktree) -> str:
    parts: list[str] = []
    for service, port in wt.ports.items():
        mark = "[green]✓[/green]" if is_port_bound(port) else "[dim]✗[/dim]"
        parts.append(f"{service[:2]}{mark}")
    return " ".join(parts) if parts else "—"


def run(start: Path) -> int:
    project = Project.discover(start)
    console = Console()

    imported = import_missing(project)
    if imported:
        console.print(
            f"[dim]imported {len(imported)} worktree(s) into the registry: "
            f"{', '.join(imported)}[/dim]"
        )

    # Sort: primary first, then by shorthand.
    rows = sorted(
        project.registry.worktrees,
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
    table.add_column("running")

    for wt in rows:
        cells: list[str] = [wt.shorthand, wt.branch or "[dim](detached)[/dim]"]
        for service in project.manifest.services:
            port = wt.ports.get(service.name)
            cells.append(str(port) if port is not None else "[dim]—[/dim]")
        cells.append(wt.db or "[dim]—[/dim]")
        if project.manifest.tenant is not None:
            cells.append(wt.tenant or "[dim]—[/dim]")
        cells.append(_running_cell(wt))
        table.add_row(*cells)

    console.print(table)
    return 0
