"""Top-level Typer app for `wt`."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console

from .commands import new as new_cmd
from .commands import rm as rm_cmd
from .commands import status as status_cmd
from .commands import tenant as tenant_cmd


app = typer.Typer(
    name="wt",
    help="Worktree manager — provision, inspect, and tear down git worktrees with their ports / DBs / tenants in sync.",
    no_args_is_help=True,
)

err = Console(stderr=True)


@app.callback()
def _main_callback() -> None:
    """Force multi-command mode so subcommands keep their names."""


def _die(message: str, code: int = 1) -> None:
    err.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code)


@app.command("status")
def status() -> None:
    """Show all worktrees for the project at the current directory."""
    try:
        rc = status_cmd.run(Path.cwd())
    except FileNotFoundError as e:
        _die(str(e))
        return
    raise typer.Exit(rc)


@app.command("new")
def new(
    shorthand: str = typer.Argument(..., help="Shorthand for the new worktree."),
    branch: str | None = typer.Argument(
        None,
        help="Branch to check out. Defaults to <shorthand>; created from HEAD if it doesn't exist.",
    ),
    tenant: str | None = typer.Option(
        None,
        "--tenant",
        "-t",
        help="Tenant to point DEPLOYMENT_ROOT at. Defaults to the primary's tenant.",
    ),
    skip_migrate: bool = typer.Option(
        False, "--skip-migrate", help="Don't run the db migrate command."
    ),
) -> None:
    """Provision a new worktree: git worktree add, createdb, env patch, migrate, register."""
    try:
        rc = new_cmd.run(
            start=Path.cwd(),
            shorthand=shorthand,
            branch=branch,
            tenant=tenant,
            skip_migrate=skip_migrate,
        )
    except FileNotFoundError as e:
        _die(str(e))
        return
    raise typer.Exit(rc)


@app.command("rm")
def rm(
    shorthand: str | None = typer.Argument(
        None,
        help="Shorthand of the worktree to remove. Omit with --auto to scan everything.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Scan all worktrees, prompt y/N per eligible one (the legacy cleanup flow).",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the per-worktree confirmation prompt."
    ),
) -> None:
    """Safely tear down a worktree and its DB (refuses dirty/unpushed/unmerged)."""
    try:
        if auto:
            if shorthand is not None:
                _die("pass either <shorthand> or --auto, not both.")
                return
            rc = rm_cmd.run_auto(Path.cwd())
        else:
            if shorthand is None:
                _die("pass a <shorthand> or use --auto.")
                return
            rc = rm_cmd.run_one(Path.cwd(), shorthand, assume_yes=yes)
    except FileNotFoundError as e:
        _die(str(e))
        return
    raise typer.Exit(rc)


@app.command("tenants")
def tenants() -> None:
    """List available tenant packages for the current project."""
    try:
        rc = tenant_cmd.list_run(Path.cwd())
    except FileNotFoundError as e:
        _die(str(e))
        return
    raise typer.Exit(rc)


@app.command("tenant")
def tenant(
    shorthand: str = typer.Argument(..., help="Worktree to repoint."),
    tenant: str = typer.Argument(..., help="Tenant name (run `wt tenants` to see options)."),
) -> None:
    """Repoint a worktree's DEPLOYMENT_ROOT to a different tenant package."""
    try:
        rc = tenant_cmd.set_run(Path.cwd(), shorthand, tenant)
    except FileNotFoundError as e:
        _die(str(e))
        return
    raise typer.Exit(rc)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
