from __future__ import annotations

import pytest

from wt import ports


def test_allocate_port_reuses_lowest_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ports, "is_port_bound", lambda port: False)

    assert ports.allocate_port({8000, 8001, 8003, 8048}, 8000) == 8002


def test_allocate_port_skips_bound_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ports, "is_port_bound", lambda port: port == 8002)

    assert ports.allocate_port({8000, 8001, 8003}, 8000) == 8004
