"""Claude client wrapper — prompt caching, structured output, streaming, and
graceful degradation when ANTHROPIC_API_KEY is absent.

The LLM is confined to *phrasing and structuring*: it never decides safety —
the graph does. So with no key the platform still works end-to-end: it returns
the deterministic plan + provenance and simply skips natural-language narration.

Model defaults to the latest Opus (`claude-opus-4-8`); set CLAUDE_MODEL to a
faster model for the ~5s latency target. Uses adaptive thinking off (these are
phrasing tasks, not reasoning tasks — reasoning lives in the graph).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Iterator, TypeVar

from pydantic import BaseModel

from .config import settings

MODEL = settings.claude_model
T = TypeVar("T", bound=BaseModel)

# --- token budget -----------------------------------------------------------
# Cumulative input+output tokens spent. Checked BEFORE each call (so we stop
# querying once over) and incremented AFTER (so the in-flight call completes).
# One call may overshoot the ceiling slightly; that's intentional.
#
# The durable source of truth is a Neo4j singleton (:SystemUsage {id:'llm'}),
# incremented atomically — so the budget is SHARED across replicas and SURVIVES
# a process restart (an in-memory counter would reset to 0 on every redeploy,
# silently handing each new process a fresh budget). The module-level int below
# is a fast mirror and the fallback if Neo4j is momentarily unreachable, so a DB
# blip degrades to per-process accounting rather than crashing the request path.
_USAGE_ID = "llm"
_tokens_used = 0  # in-memory mirror / fallback


def _db_add(delta: int) -> None:
    """Atomically add to the durable counter and sync the in-memory mirror."""
    global _tokens_used
    from .db import run
    rows = run(
        "MERGE (u:SystemUsage {id: $id}) "
        "SET u.tokens = coalesce(u.tokens, 0) + $d "
        "RETURN u.tokens AS total",
        id=_USAGE_ID, d=delta,
    )
    if rows:
        _tokens_used = rows[0]["total"]


def tokens_used() -> int:
    """Durable total from Neo4j; falls back to the in-memory mirror on DB error."""
    global _tokens_used
    try:
        from .db import run
        rows = run(
            "MATCH (u:SystemUsage {id: $id}) RETURN u.tokens AS total", id=_USAGE_ID
        )
        if rows and rows[0]["total"] is not None:
            _tokens_used = rows[0]["total"]
    except Exception:
        pass  # Neo4j unreachable — serve the last-known in-memory value
    return _tokens_used


def budget_exhausted() -> bool:
    return settings.llm_token_budget > 0 and tokens_used() >= settings.llm_token_budget


def _account(usage) -> None:
    global _tokens_used
    if usage is None:
        return
    delta = (getattr(usage, "input_tokens", 0) or 0) + \
            (getattr(usage, "output_tokens", 0) or 0)
    if delta <= 0:
        return
    _tokens_used += delta  # mirror first, so a DB failure still records spend
    try:
        _db_add(delta)
    except Exception:
        pass  # durable write failed — in-memory mirror already updated


def is_available() -> bool:
    """True only when there's a key AND we're under the token budget — so every
    LLM path degrades to the deterministic graph output once the budget is hit."""
    return bool(settings.anthropic_api_key) and not budget_exhausted()


@lru_cache(maxsize=1)
def _client():
    import anthropic
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _system(text: str) -> list[dict]:
    """Stable, cacheable system prefix. Cache only engages above the model's
    minimum prefix; verified via usage.cache_read_input_tokens in observability."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def parse(system: str, user: str, schema: type[T], *, max_tokens: int = 2000) -> T | None:
    """Structured output → a validated Pydantic instance (None if no key)."""
    if not is_available():
        return None
    resp = _client().messages.parse(
        model=MODEL,
        max_tokens=max_tokens,
        system=_system(system),
        messages=[{"role": "user", "content": user}],
        output_format=schema,
    )
    _account(getattr(resp, "usage", None))
    return resp.parsed_output


def complete(system: str, user: str, *, max_tokens: int = 1500) -> str | None:
    if not is_available():
        return None
    resp = _client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=_system(system),
        messages=[{"role": "user", "content": user}],
    )
    _account(getattr(resp, "usage", None))
    return "".join(b.text for b in resp.content if b.type == "text")


def stream(system: str, user: str, *, max_tokens: int = 1500) -> Iterator[str]:
    """Yield text deltas for SSE. Yields nothing if no key (caller falls back)."""
    if not is_available():
        return
    with _client().messages.stream(
        model=MODEL,
        max_tokens=max_tokens,
        system=_system(system),
        messages=[{"role": "user", "content": user}],
    ) as s:
        yield from s.text_stream
        _account(getattr(s.get_final_message(), "usage", None))
