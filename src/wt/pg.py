"""Local Postgres helpers shared by `wt new` and `wt rm`.

A database `wt new --db-from` copies carries its origin as a database comment,
``wt:db-from=<source>``. The tag lives on the database itself, so `wt rm` can
tell a disposable copy from a worktree's own data without a registry.
"""

from __future__ import annotations

import subprocess

SOURCE_TAG = "wt:db-from="


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run(cmd: list[str], sql: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, input=sql, capture_output=True, text=True)
    except OSError as exc:  # no psql on PATH reads as "no database", never a crash
        return subprocess.CompletedProcess(cmd, 127, stdout="", stderr=str(exc))


def _psql(sql: str) -> subprocess.CompletedProcess[str]:
    return _run(["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", "-d", "postgres"], sql)


def db_exists(name: str) -> bool:
    r = _run(["psql", "-lqt"])
    if r.returncode != 0:
        return False
    return any(line.split("|", 1)[0].strip() == name for line in r.stdout.splitlines())


def create_copy(name: str, source: str) -> None:
    """Create ``name`` from ``source`` as a template, then tag it with its origin."""
    subprocess.run(["createdb", "-T", source, name], check=True)
    tagged = _psql(f"COMMENT ON DATABASE {_ident(name)} IS {_literal(SOURCE_TAG + source)};")
    if tagged.returncode != 0:
        # An untagged copy would be dumped like owned data on rm; never leave one.
        subprocess.run(["dropdb", name], capture_output=True)
        raise subprocess.CalledProcessError(
            tagged.returncode, "COMMENT ON DATABASE", tagged.stdout, tagged.stderr
        )


def copy_source(name: str) -> str | None:
    """The database ``name`` was copied from by `wt new --db-from`, else None."""
    r = _psql(
        "SELECT shobj_description(oid, 'pg_database') FROM pg_database "
        f"WHERE datname = {_literal(name)};"
    )
    comment = r.stdout.strip() if r.returncode == 0 else ""
    return comment.removeprefix(SOURCE_TAG) if comment.startswith(SOURCE_TAG) else None
