"""`wt served` — rebuild the local served-data farm for one or all worktrees.

`wt new` and `wt tenant` already build the farm, and its symlinks track table
*contents* for free. Only a change to the table *set* — a `dvc pull` adding a
table, a refresh dropping one — needs this.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from ..project import Project
from ..served import FarmError, FarmResult, apply_farm
from ..types import PRIMARY


console = Console()
err = Console(stderr=True)


def report(result: FarmResult | None, console: Console, *, indent: str = "  ") -> None:
    """Print what a farm apply did. Shared by `wt new` / `wt tenant` / here."""
    if result is None:
        return
    if result.skipped is not None:
        console.print(f"{indent}[dim]served farm: not needed — {result.skipped}[/dim]")
        if result.removed:
            console.print(
                f"{indent}[yellow]cleared {len(result.removed)} stale farm "
                f"link(s)[/yellow]"
            )
        console.print(f"{indent}served: {result.served_root}")
        return

    changes = []
    if result.linked:
        changes.append(f"{len(result.linked)} linked")
    if result.unchanged:
        changes.append(f"{len(result.unchanged)} unchanged")
    if result.removed:
        changes.append(f"{len(result.removed)} removed")
    console.print(
        f"{indent}served farm: [green]{result.table_count} tables[/green] "
        f"({', '.join(changes) or 'empty'}) → {result.farm}"
    )
    if result.pruned:
        console.print(
            f"{indent}[dim]pruned (no data file): {', '.join(result.pruned)}[/dim]"
        )


def apply_and_report(
    manifest, *, worktree: Path, tenant_path: str | None, indent: str = "  "
) -> FarmResult | None:
    """Apply the farm, reporting either the result or a loud non-fatal warning.

    A failure here must never abort provisioning — but it must never pass
    quietly either, because the failure mode it exists to prevent (an app
    silently reading a near-empty dataset) is itself silent.
    """
    if manifest.served_data is None:
        return None
    try:
        result = apply_farm(
            manifest,
            worktree=worktree,
            tenant_path=Path(tenant_path) if tenant_path else None,
        )
    except (FarmError, OSError) as exc:
        err.print(
            f"[bold red]WARNING:[/bold red] served-data farm not built: {exc}. "
            f"{manifest.served_data.env_var} was left unchanged — the app may read "
            f"a partial dataset."
        )
        return None
    report(result, console, indent=indent)
    return result


def run(start: Path, shorthand: str | None = None) -> int:
    project = Project.discover(start)
    manifest = project.manifest

    if manifest.served_data is None:
        err.print("[red]error:[/red] no served_data config in .wt.yaml.")
        return 2

    worktrees = project.worktrees()
    if shorthand is not None:
        match = next((w for w in worktrees if w.shorthand == shorthand), None)
        if match is None:
            err.print(f"[red]error:[/red] no worktree {shorthand!r} found on disk.")
            return 2
        worktrees = [match]

    rebuilt = 0
    for wt in sorted(worktrees, key=lambda w: (w.shorthand != PRIMARY, w.shorthand)):
        if wt.tenant_path is None:
            if shorthand is not None:
                err.print(
                    f"[red]error:[/red] {wt.shorthand} has no tenant; nothing to farm."
                )
                return 2
            continue
        console.print(f"[cyan]{wt.shorthand}[/cyan] ({wt.tenant or '—'})")
        apply_and_report(manifest, worktree=Path(wt.path), tenant_path=wt.tenant_path)
        rebuilt += 1

    if rebuilt == 0:
        console.print("[dim]no worktrees with a tenant to farm.[/dim]")
    return 0
