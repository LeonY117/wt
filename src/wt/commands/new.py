"""`wt new` — provision a fresh worktree end-to-end."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from ..envfile import patch_env
from ..manifest import EnvPatch, Manifest
from ..placeholders import build_context, render_db_name, resolve
from ..ports import allocate_port
from ..project import Project
from ..registry import Worktree
from ..tenant import resolve_tenant


console = Console()
err = Console(stderr=True)


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def _branch_exists(repo: Path, branch: str) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        capture_output=True,
    )
    return r.returncode == 0


def _db_exists(name: str) -> bool:
    r = subprocess.run(["psql", "-lqt"], capture_output=True, text=True)
    if r.returncode != 0:
        return False
    for line in r.stdout.splitlines():
        db = line.split("|", 1)[0].strip()
        if db == name:
            return True
    return False


def _create_db(name: str) -> None:
    _run(["createdb", name])


def _drop_db_quiet(name: str) -> None:
    subprocess.run(["dropdb", name], capture_output=True)


def _resolve_env_source(
    primary: Path, worktree: Path, patch: EnvPatch
) -> Path | None:
    """Return a file we should copy contents from before patching.

    Priority: primary's same file (so Clerk keys etc. carry over) → template
    declared in the manifest → None (we'll create a fresh file).
    """
    primary_file = primary / patch.file
    if primary_file.exists():
        return primary_file
    if patch.template is not None:
        primary_template = primary / patch.template
        if primary_template.exists():
            return primary_template
        worktree_template = worktree / patch.template
        if worktree_template.exists():
            return worktree_template
    return None


def _apply_env_patches(
    *, manifest: Manifest, primary: Path, worktree: Path, context: dict[str, str]
) -> None:
    for patch in manifest.env_patches:
        target = worktree / patch.file
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            source = _resolve_env_source(primary, worktree, patch)
            if source is not None:
                shutil.copy2(source, target)
            else:
                target.touch()
        updates = {key: resolve(value, context) for key, value in patch.set.items()}
        patch_env(target, updates)


def _run_migrate(manifest: Manifest, worktree: Path) -> None:
    if manifest.db is None or not manifest.db.migrate:
        return
    console.print(f"[dim]running migrate: {manifest.db.migrate}[/dim]")
    subprocess.run(manifest.db.migrate, shell=True, cwd=worktree, check=True)


def run(
    *,
    start: Path,
    shorthand: str,
    branch: str | None,
    tenant: str | None,
    skip_migrate: bool = False,
) -> int:
    project = Project.discover(start)
    manifest = project.manifest
    registry = project.registry
    primary = project.primary

    # ---- validate shorthand ----
    if shorthand in {"(primary)", ""}:
        err.print("[red]error:[/red] shorthand cannot be empty or '(primary)'.")
        return 2
    if registry.find(shorthand) is not None:
        err.print(f"[red]error:[/red] shorthand {shorthand!r} already registered.")
        return 2

    target_path = (primary.parent / f"{manifest.worktree_prefix}{shorthand}").resolve()
    if target_path.exists():
        err.print(f"[red]error:[/red] {target_path} already exists on disk.")
        return 2

    # ---- resolve branch ----
    branch = branch or shorthand
    create_branch = not _branch_exists(primary, branch)

    # ---- resolve tenant ----
    tenant_name: str | None = None
    tenant_path: str | None = None
    if manifest.tenant is not None:
        if tenant is None:
            # Inherit from primary worktree's registry entry, if known.
            primary_entry = registry.find("(primary)")
            if primary_entry is not None:
                tenant_name = primary_entry.tenant
                tenant_path = primary_entry.tenant_path
        else:
            tp = resolve_tenant(manifest, tenant)
            if tp is None:
                err.print(
                    f"[red]error:[/red] tenant {tenant!r} not found in any search "
                    f"path. Run `wt tenants` to see available tenants."
                )
                return 2
            tenant_name = tenant
            tenant_path = str(tp)
    elif tenant is not None:
        err.print(
            "[red]error:[/red] --tenant given but project has no tenant config "
            "in .wt.yaml."
        )
        return 2

    # ---- allocate ports ----
    ports: dict[str, int] = {}
    for service in manifest.services:
        ports[service.name] = allocate_port(registry, service.name, service.default_port)

    # ---- compute DB name ----
    db_name = render_db_name(manifest, shorthand)

    console.print(
        f"[bold]provisioning[/bold] {shorthand} "
        f"({'new branch ' if create_branch else 'existing branch '}{branch})"
    )
    console.print(f"  path:    {target_path}")
    for service, port in ports.items():
        console.print(f"  {service}: :{port}")
    if db_name:
        console.print(f"  db:      {db_name}")
    if tenant_name:
        console.print(f"  tenant:  {tenant_name} → {tenant_path}")

    # ---- step 1: git worktree add ----
    worktree_created = False
    db_created = False
    try:
        cmd = ["git", "worktree", "add"]
        if create_branch:
            cmd += ["-b", branch, str(target_path)]
        else:
            cmd += [str(target_path), branch]
        _run(cmd, cwd=primary)
        worktree_created = True

        # ---- step 2: createdb ----
        if db_name is not None:
            if _db_exists(db_name):
                console.print(
                    f"[yellow]warning:[/yellow] db {db_name} already exists, "
                    f"reusing it (no createdb)."
                )
            else:
                _create_db(db_name)
                db_created = True

        # ---- step 3: env patches ----
        context = build_context(
            manifest=manifest,
            shorthand=shorthand,
            ports=ports,
            db=db_name,
            tenant=tenant_name,
            tenant_path=tenant_path,
        )
        _apply_env_patches(
            manifest=manifest,
            primary=primary,
            worktree=target_path,
            context=context,
        )

        # ---- step 4: migrate ----
        if not skip_migrate:
            _run_migrate(manifest, target_path)
        else:
            console.print("[dim]skipping migrate (--skip-migrate)[/dim]")

        # ---- step 5: register ----
        registry.upsert(
            Worktree(
                shorthand=shorthand,
                path=str(target_path),
                branch=branch,
                ports=ports,
                db=db_name,
                tenant=tenant_name,
                tenant_path=tenant_path,
            )
        )
        project.save()
    except subprocess.CalledProcessError as e:
        err.print(f"[red]provisioning failed:[/red] {e}")
        # Best-effort rollback.
        if db_created and db_name is not None:
            err.print(f"  rolling back db {db_name}")
            _drop_db_quiet(db_name)
        if worktree_created:
            err.print(f"  rolling back worktree {target_path}")
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(target_path)],
                cwd=primary,
                capture_output=True,
            )
        return 1

    # ---- success: print next-steps ----
    console.print()
    console.print(f"[green]✓[/green] worktree ready at {target_path}")
    console.print()
    console.print("[bold]next:[/bold]")
    console.print(f"  cd {target_path}")
    # We don't know the exact run commands generically — print the ports so
    # the user can adapt their familiar incantations.
    for service, port in ports.items():
        console.print(f"  # {service} runs on :{port}")
    console.print()
    console.print("  wt status   # verify")
    return 0
