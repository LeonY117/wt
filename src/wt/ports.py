"""Port allocation and live-bind detection."""

from __future__ import annotations

import shutil
import subprocess

from .registry import Registry


def is_port_bound(port: int) -> bool:
    """True if something is currently listening on `port` on the local host."""
    lsof = shutil.which("lsof")
    if lsof is None:
        # Fall back to assuming free; better than crashing on systems w/o lsof.
        return False
    result = subprocess.run(
        [lsof, "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def used_ports_for_service(registry: Registry, service: str) -> set[int]:
    return {
        wt.ports[service]
        for wt in registry.worktrees
        if service in wt.ports
    }


def allocate_port(registry: Registry, service: str, default: int) -> int:
    """Pick the next free port for `service`.

    Strategy: highest currently-registered port for this service + 1; if that
    port is bound by some other process, skip until a free one is found. If
    no worktree has this service yet, start from `default`.
    """
    used = used_ports_for_service(registry, service)
    if used:
        candidate = max(used) + 1
    else:
        candidate = default
    while candidate in used or is_port_bound(candidate):
        candidate += 1
        if candidate > 65535:
            raise RuntimeError(f"ran out of ports for service {service!r}")
    return candidate
