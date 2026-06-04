"""Lightweight tracing — records each agent step, tool call, and graph query with
timing, so a generation request is auditable end-to-end (the observability
nice-to-have). The trace is returned alongside the result and rendered in the UI.
"""
from __future__ import annotations

import time
from contextlib import contextmanager


class Trace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    @contextmanager
    def step(self, kind: str, name: str, **detail):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.events.append({
                "kind": kind, "name": name,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                **detail,
            })

    def add(self, kind: str, name: str, **detail) -> None:
        self.events.append({"kind": kind, "name": name, **detail})

    def as_list(self) -> list[dict]:
        return self.events
