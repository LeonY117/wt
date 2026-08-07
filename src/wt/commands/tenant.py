"""`wt tenant` and `wt tenants` — manage the DEPLOYMENT_ROOT pointer per worktree."""

from __future__ import annotations

from itertools import islice
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..envfile import patch_env, read_env
from ..manifest import Manifest
from ..placeholders import build_context, resolve
from ..ports import is_port_bound
from ..project import Project
from ..tenant import list_tenants, resolve_tenant, tenant_identity_from_path
from ..types import PRIMARY


console = Console()
err = Console(stderr=True)


def _fed_by(value: str, old_package: Path) -> bool:
    """True if `value` names a data root still fed by the previous tenant.

    Two shapes, because wt has produced both: a path inside the old package
    tree, and a directory of symlinks INTO it — the retired served farm lived
    inside the *worktree*, so a prefix test alone would miss exactly the case
    that matters.
    """
    path = Path(value).expanduser()
    try:
        if path.is_relative_to(old_package):
            return True
        if not path.is_dir():
            return False
        for child in islice(sorted(path.iterdir()), 50):
            if child.is_symlink() and child.resolve().is_relative_to(old_package):
                return True
    except (OSError, ValueError):
        return False
    return False


def _stale_pins(
    manifest: Manifest, worktree: Path, old_tenant_path: str | None
) -> list[str]:
    """`file:KEY` for every env entry still pinned to the previous tenant.

    wt no longer writes a served-root variable, so it can no longer keep one
    in step with the tenant either — but worktrees provisioned before the farm
    was retired still carry one, and it outranks the app's own default. A
    switch that left it in place would serve the previous tenant's tables,
    which is the one thing `wt tenant` must never do. Values are never
    printed; the key is enough to act on.
    """
    if old_tenant_path is None:
        return []
    old_package = Path(old_tenant_path).expanduser()
    tenant_keys = {manifest.tenant.env_var} if manifest.tenant else set()
    pins: list[str] = []
    for patch in manifest.env_patches:
        for key, value in read_env(worktree / patch.file).items():
            if key not in tenant_keys and _fed_by(value, old_package):
                pins.append(f"{patch.file}:{key}")
    return pins


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
        wt.tenant for wt in project.worktrees() if wt.tenant
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

    wt = project.find(shorthand)
    if wt is None:
        err.print(f"[red]error:[/red] no worktree {shorthand!r} found on disk.")
        return 2

    tenant_path = resolve_tenant(manifest, tenant)
    if tenant_path is None:
        err.print(
            f"[red]error:[/red] tenant {tenant!r} not found. "
            f"Run `wt tenants` to see available tenants."
        )
        return 2

    old_tenant = wt.tenant
    old_tenant_path = wt.tenant_path
    tenant_identity, identity_warning = tenant_identity_from_path(
        manifest, tenant_path
    )
    if identity_warning is not None:
        err.print(f"[bold red]WARNING:[/bold red] {identity_warning}.")

    # Re-apply only the env patches that touch tenant placeholders. We re-render
    # all patches but only write keys whose resolved value differs — keeps the
    # change surgical and avoids stomping on unrelated values. The env-file
    # patch IS the source of truth; next `wt status` will reflect it.
    context = build_context(
        manifest=manifest,
        shorthand=shorthand,
        ports=wt.ports,
        db=wt.db,
        tenant=tenant,
        tenant_path=str(tenant_path),
    )
    worktree_path = Path(wt.path)
    tenant_writes: list[tuple[Path, str, str]] = []
    for patch in manifest.env_patches:
        target = worktree_path / patch.file
        if not target.exists():
            continue
        updates: dict[str, str] = {}
        for key, template in patch.set.items():
            if "{tenant" not in template:
                continue
            updates[key] = resolve(template, context)
        if (
            manifest.tenant.identity_env is not None
            and manifest.tenant.env_var in updates
        ):
            updates.pop(manifest.tenant.identity_env, None)
            if tenant_identity is not None:
                updates[manifest.tenant.identity_env] = tenant_identity
        if updates:
            patch_env(target, updates)
            if manifest.tenant.env_var in updates:
                tenant_writes.append(
                    (target, manifest.tenant.env_var, updates[manifest.tenant.env_var])
                )
                if (
                    manifest.tenant.identity_env is not None
                    and manifest.tenant.identity_env in updates
                ):
                    tenant_writes.append(
                        (
                            target,
                            manifest.tenant.identity_env,
                            updates[manifest.tenant.identity_env],
                        )
                    )

    console.print(
        f"[green]✓[/green] {shorthand}: tenant "
        f"[yellow]{old_tenant or '—'}[/yellow] → [yellow]{tenant}[/yellow]"
    )
    if manifest.tenant.identity_env is None:
        console.print(f"  path: {tenant_path}")
    else:
        for target, key, value in tenant_writes:
            console.print(
                f"  wrote {target.relative_to(worktree_path)}: {key}={value}"
            )

    for pin in _stale_pins(manifest, worktree_path, old_tenant_path):
        err.print(
            f"[bold red]WARNING:[/bold red] {pin} still points at data fed by "
            f"[yellow]{old_tenant or 'the previous tenant'}[/yellow]. It outranks "
            f"the app's own default, so this worktree keeps serving the old "
            f"tenant's tables. Delete that line — wt stopped writing (and so "
            f"stopped correcting) it when the served farm was retired."
        )

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
