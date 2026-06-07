"""LLM provider facade: Anthropic by default, Venice as an explicit fallback.

The public functions in this module are intentionally stable because generation
and copilot call them directly:

  is_available, parse, complete, stream, budget_exhausted, tokens_used

The LLM is confined to phrasing and light structuring. Safety and
personalization stay in the graph, so missing keys or exhausted budgets degrade
to deterministic plans and retrieved graph facts instead of breaking the app.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Iterator, TypeVar

from pydantic import BaseModel, ValidationError

from .config import settings

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


def _usage_get(usage, *names: str) -> int:
    if usage is None:
        return 0
    for name in names:
        if isinstance(usage, dict):
            value = usage.get(name)
        else:
            value = getattr(usage, name, None)
        if value:
            return int(value)
    return 0


def _account(usage) -> None:
    """Map provider usage objects into the shared durable token counter."""
    global _tokens_used
    if usage is None:
        return
    input_tokens = _usage_get(usage, "input_tokens", "prompt_tokens")
    output_tokens = _usage_get(usage, "output_tokens", "completion_tokens")
    delta = input_tokens + output_tokens
    if delta <= 0:
        delta = _usage_get(usage, "total_tokens")
    if delta <= 0:
        return
    _tokens_used += delta  # mirror first, so a DB failure still records spend
    try:
        _db_add(delta)
    except Exception:
        pass  # durable write failed — in-memory mirror already updated


# --- provider model selection ----------------------------------------------

def _provider_name() -> str:
    return (settings.llm_provider or "anthropic").strip().lower()


def _parse_role(schema: type[BaseModel]) -> str:
    # RouteDecision is copilot routing. Other structured outputs currently
    # support generation planning/assembly, so they use the intent model slot.
    return "copilot" if schema.__name__ == "RouteDecision" else "intent"


def _stream_role(system: str) -> str:
    return "copilot" if "copilot" in system.lower() else "narrate"


def _venice_model(role: str) -> str:
    if role == "copilot":
        return settings.model_copilot
    if role == "narrate":
        return settings.model_narrate
    return settings.model_intent


def _schema_name(schema: type[BaseModel]) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in schema.__name__)


# --- providers --------------------------------------------------------------

class _Provider:
    def has_key(self) -> bool:
        raise NotImplementedError

    def parse(self, system: str, user: str, schema: type[T], *,
              max_tokens: int) -> T | None:
        raise NotImplementedError

    def complete(self, system: str, user: str, *, max_tokens: int) -> str | None:
        raise NotImplementedError

    def stream(self, system: str, user: str, *, max_tokens: int) -> Iterator[str]:
        raise NotImplementedError


class _AnthropicProvider(_Provider):
    def has_key(self) -> bool:
        return bool(settings.anthropic_api_key)

    @lru_cache(maxsize=1)
    def _client(self):
        import anthropic
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)

    @staticmethod
    def _system(text: str) -> list[dict]:
        """Stable, cacheable system prefix for Anthropic prompt caching."""
        return [{"type": "text", "text": text,
                 "cache_control": {"type": "ephemeral"}}]

    def parse(self, system: str, user: str, schema: type[T], *,
              max_tokens: int) -> T | None:
        resp = self._client().messages.parse(
            model=settings.claude_model,
            max_tokens=max_tokens,
            system=self._system(system),
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        _account(getattr(resp, "usage", None))
        return resp.parsed_output

    def complete(self, system: str, user: str, *, max_tokens: int) -> str | None:
        resp = self._client().messages.create(
            model=settings.claude_model,
            max_tokens=max_tokens,
            system=self._system(system),
            messages=[{"role": "user", "content": user}],
        )
        _account(getattr(resp, "usage", None))
        return "".join(b.text for b in resp.content if b.type == "text")

    def stream(self, system: str, user: str, *, max_tokens: int) -> Iterator[str]:
        with self._client().messages.stream(
            model=settings.claude_model,
            max_tokens=max_tokens,
            system=self._system(system),
            messages=[{"role": "user", "content": user}],
        ) as s:
            yield from s.text_stream
            _account(getattr(s.get_final_message(), "usage", None))


class _VeniceProvider(_Provider):
    def has_key(self) -> bool:
        return bool(settings.venice_api_key)

    @lru_cache(maxsize=1)
    def _client(self):
        from openai import OpenAI
        return OpenAI(api_key=settings.venice_api_key, base_url=settings.llm_base_url)

    @staticmethod
    def _messages(system: str, user: str) -> list[dict]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    @staticmethod
    def _extra_body() -> dict:
        return {
            "venice_parameters": {
                "disable_thinking": True,
                "include_venice_system_prompt": False,
            },
        }

    def parse(self, system: str, user: str, schema: type[T], *,
              max_tokens: int) -> T | None:
        resp = self._client().chat.completions.create(
            model=_venice_model(_parse_role(schema)),
            messages=self._messages(system, user),
            max_completion_tokens=max_tokens,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": _schema_name(schema),
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            },
            extra_body=self._extra_body(),
        )
        _account(getattr(resp, "usage", None))
        content = resp.choices[0].message.content if resp.choices else ""
        try:
            return schema.model_validate_json(content or "{}")
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
            try:
                return schema.model_validate(json.loads(content or "{}"))
            except Exception:
                return None

    def complete(self, system: str, user: str, *, max_tokens: int) -> str | None:
        resp = self._client().chat.completions.create(
            model=_venice_model("narrate"),
            messages=self._messages(system, user),
            max_completion_tokens=max_tokens,
            extra_body=self._extra_body(),
        )
        _account(getattr(resp, "usage", None))
        return resp.choices[0].message.content if resp.choices else ""

    def stream(self, system: str, user: str, *, max_tokens: int) -> Iterator[str]:
        chunks = self._client().chat.completions.create(
            model=_venice_model(_stream_role(system)),
            messages=self._messages(system, user),
            max_completion_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
            extra_body=self._extra_body(),
        )
        for chunk in chunks:
            _account(getattr(chunk, "usage", None))
            for choice in getattr(chunk, "choices", []) or []:
                delta = getattr(choice, "delta", None)
                content = getattr(delta, "content", None)
                if content:
                    yield content


@lru_cache(maxsize=1)
def _provider() -> _Provider:
    name = _provider_name()
    if name == "anthropic":
        return _AnthropicProvider()
    if name == "venice":
        return _VeniceProvider()
    raise ValueError(f"Unsupported LLM_PROVIDER={settings.llm_provider!r}")


# --- public facade ----------------------------------------------------------

def is_available() -> bool:
    """True only when the active provider has a key and budget remains."""
    return _provider().has_key() and not budget_exhausted()


def parse(system: str, user: str, schema: type[T], *, max_tokens: int = 2000) -> T | None:
    """Structured output → a validated Pydantic instance (None if unavailable)."""
    if not is_available():
        return None
    return _provider().parse(system, user, schema, max_tokens=max_tokens)


def complete(system: str, user: str, *, max_tokens: int = 1500) -> str | None:
    if not is_available():
        return None
    return _provider().complete(system, user, max_tokens=max_tokens)


def stream(system: str, user: str, *, max_tokens: int = 1500) -> Iterator[str]:
    """Yield text deltas for SSE. Yields nothing if no key (caller falls back)."""
    if not is_available():
        return
    yield from _provider().stream(system, user, max_tokens=max_tokens)
