"""`wt tenant` and `wt tenants` — manage the DEPLOYMENT_ROOT pointer per worktree."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..envfile import patch_env
from ..placeholders import build_context, resolve
from ..ports import is_port_bound
from ..project import Project
from ..registry import PRIMARY
from ..tenant import list_tenants, resolve_tenant


console = Console()
err = Console(stderr=True)


def list_run(start: Path) -> int:
    project = Project.discover(start)
    if project.manifest.tenant is None:
        err.print("[red]error:[/red] no tenant config in .wt.yaml.")
        return 2

    tenants = list_tenants(project.manifest)
    if not tenants:
        console.print("[dim]no tenants found in any search path.[/dim]")
        return 0

    # Group by which search path each tenant came from for clearer labelling.
    table = Table(title="[bold]available tenants[/bold]", title_justify="left")
    table.add_column("name", style="cyan")
    table.add_column("path")

    used_paths: set[str] = {
        wt.tenant for wt in project.registry.worktrees if wt.tenant
    }

    for name, path in tenants:
        marker = " [green](in use)[/green]" if name in used_paths else ""
        table.add_row(f"{name}{marker}", str(path))

    console.print(table)
    return 0


def set_run(start: Path, shorthand: str, tenant: str) -> int:
    project = Project.discover(start)
    manifest = project.manifest

    if manifest.tenant is None:
        err.print("[red]error:[/red] no tenant config in .wt.yaml.")
        return 2

    wt = project.registry.find(shorthand)
    if wt is None:
        err.print(f"[red]error:[/red] no worktree {shorthand!r} in registry.")
        return 2

    tenant_path = resolve_tenant(manifest, tenant)
    if tenant_path is None:
        err.print(
            f"[red]error:[/red] tenant {tenant!r} not found. "
            f"Run `wt tenants` to see available tenants."
        )
        return 2

    old_tenant = wt.tenant
    wt.tenant = tenant
    wt.tenant_path = str(tenant_path)

    # Re-apply only the env patches that touch tenant placeholders. We re-render
    # all patches but only write keys whose resolved value differs — keeps the
    # change surgical and avoids stomping on unrelated values.
    context = build_context(
        manifest=manifest,
        shorthand=shorthand,
        ports=wt.ports,
        db=wt.db,
        tenant=wt.tenant,
        tenant_path=wt.tenant_path,
    )
    worktree_path = Path(wt.path)
    for patch in manifest.env_patches:
        target = worktree_path / patch.file
        if not target.exists():
            continue
        updates: dict[str, str] = {}
        for key, template in patch.set.items():
            if "{tenant" not in template:
                continue
            updates[key] = resolve(template, context)
        if updates:
            patch_env(target, updates)

    project.save()

    console.print(
        f"[green]✓[/green] {shorthand}: tenant "
        f"[yellow]{old_tenant or '—'}[/yellow] → [yellow]{tenant}[/yellow]"
    )
    console.print(f"  path: {tenant_path}")

    # Warn if backend is currently bound — running process won't pick up the
    # new DEPLOYMENT_ROOT until restarted.
    running_services: list[str] = []
    for service, port in wt.ports.items():
        if is_port_bound(port):
            running_services.append(f"{service} (:{port})")
    if running_services:
        console.print()
        console.print(
            "[yellow]restart needed:[/yellow] " + ", ".join(running_services) +
            " — the running process still has the old DEPLOYMENT_ROOT."
        )

    return 0
