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


def is_available() -> bool:
    return bool(settings.anthropic_api_key)


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
