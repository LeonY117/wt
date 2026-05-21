"""Placeholder resolution for env_patches `set:` values."""

from __future__ import annotations

import re

from .manifest import Manifest
from .project import shorthand_underscored


_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def build_context(
    *,
    manifest: Manifest,
    shorthand: str,
    ports: dict[str, int],
    db: str | None,
    tenant: str | None,
    tenant_path: str | None,
) -> dict[str, str]:
    ctx: dict[str, str] = {
        "shorthand": shorthand,
        "shorthand_underscored": shorthand_underscored(shorthand),
        "project_root": str(manifest.project_root),
    }
    if db is not None:
        ctx["db"] = db
    if tenant is not None:
        ctx["tenant"] = tenant
    if tenant_path is not None:
        ctx["tenant_path"] = tenant_path
    for service, port in ports.items():
        ctx[f"{service}_port"] = str(port)
    return ctx


def resolve(template: str, context: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in context:
            raise KeyError(
                f"placeholder {{{key}}} not available; "
                f"known: {sorted(context)}"
            )
        return context[key]

    return _PLACEHOLDER_RE.sub(sub, template)


def render_db_name(manifest: Manifest, shorthand: str) -> str | None:
    if manifest.db is None:
        return None
    return resolve(
        manifest.db.name_template,
        {
            "shorthand": shorthand,
            "shorthand_underscored": shorthand_underscored(shorthand),
        },
    )
