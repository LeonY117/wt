"""Minimal .env reader and surgical patcher.

We DO NOT want to dump env values to stdout/logs anywhere — `.env` may carry
secrets even when most keys (like ports) are mundane. All callers should pass
through specific keys they want and avoid printing values.
"""

from __future__ import annotations

import re
from pathlib import Path


# Matches `KEY=value` and `KEY="quoted value"` / `KEY='quoted value'`. Lines
# beginning with `#` or whitespace-only are ignored. `export KEY=...` is
# tolerated.
_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<val>.*?)\s*$"
)


def _strip_quotes(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}:
        return v[1:-1]
    return v


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        out[m["key"]] = _strip_quotes(m["val"])
    return out


def patch_env(path: Path, updates: dict[str, str]) -> None:
    """Update existing KEY= lines in place; append missing ones at end.

    Preserves comments, ordering, and unrelated lines. Atomic write.
    """
    existing_lines = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    new_lines: list[str] = []
    for line in existing_lines:
        m = _LINE_RE.match(line)
        if m and m["key"] in updates:
            key = m["key"]
            new_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            new_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")
    # Trailing newline.
    text = "\n".join(new_lines) + ("\n" if new_lines else "")
    tmp = path.with_suffix(path.suffix + ".wt.tmp")
    tmp.write_text(text)
    tmp.replace(path)
