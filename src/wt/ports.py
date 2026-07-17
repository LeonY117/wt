"""Port allocation and live-bind detection."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable


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


def allocate_port(used: Iterable[int], default: int) -> int:
    """Pick the lowest free port at or above ``default``.

    Ports assigned to other worktrees and ports bound by unrelated processes
    are skipped. Gaps left by removed worktrees are reused.
    """
    used_set = set(used)
    candidate = default
    while candidate in used_set or is_port_bound(candidate):
        candidate += 1
        if candidate > 65535:
            raise RuntimeError("ran out of ports")
    return candidate
