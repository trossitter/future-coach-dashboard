from __future__ import annotations

import importlib
import os
from types import SimpleNamespace

import pytest

from app.schemas import RouteDecision


ENV_KEYS = [
    "LLM_PROVIDER",
    "VENICE_API_KEY",
    "ANTHROPIC_API_KEY",
    "LLM_BASE_URL",
    "MODEL_INTENT",
    "MODEL_NARRATE",
    "MODEL_COPILOT",
    "CLAUDE_MODEL",
    "LLM_TOKEN_BUDGET",
    "LOCAL_BASE_URL",
    "LOCAL_MODEL",
    "LOCAL_API_KEY",
]


@pytest.fixture(autouse=True)
def restore_llm_modules():
    original_env = {key: os.environ.get(key) for key in ENV_KEYS}
    yield
    for key, value in original_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    import app.config as config
    import app.llm as llm

    importlib.reload(config)
    importlib.reload(llm)


def load_llm(monkeypatch, **env):
    """Reload config + llm after changing env, so settings stay deterministic."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    import app.config as config
    import app.llm as llm

    importlib.reload(config)
    return importlib.reload(llm)


def usage(prompt_tokens: int, completion_tokens: int):
    return SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


class FakeCompletions:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return iter([
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="hel"),
                        ),
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="lo"),
                        ),
                    ],
                    usage=None,
                ),
                SimpleNamespace(choices=[], usage=usage(2, 3)),
            ])
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"intent":"sleep","confidence":0.92,'
                            '"clarify_question":""}'
                        ),
                    ),
                ),
            ],
            usage=usage(5, 7),
        )


class FakeOpenAIClient:
    def __init__(self):
        self.completions = FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def test_no_key_keeps_deterministic_path(monkeypatch):
    llm = load_llm(monkeypatch)
    provider = llm._provider()

    def fail_if_called():
        raise AssertionError("client should not be constructed without a key")

    monkeypatch.setattr(provider, "_client", fail_if_called)

    assert llm._provider_name() == "anthropic"
    assert llm.is_available() is False
    assert llm.parse("system", "user", RouteDecision, max_tokens=100) is None
    assert llm.complete("system", "user", max_tokens=100) is None
    assert list(llm.stream("system", "user", max_tokens=100)) == []


def test_provider_selection_uses_active_provider_key(monkeypatch):
    llm = load_llm(
        monkeypatch,
        ANTHROPIC_API_KEY="ak-test",
    )
    assert llm.is_available() is True
    assert llm._provider_name() == "anthropic"

    llm = load_llm(
        monkeypatch,
        VENICE_API_KEY="vk-test",
    )
    assert llm.is_available() is False
    assert llm._provider_name() == "anthropic"

    llm = load_llm(
        monkeypatch,
        LLM_PROVIDER="venice",
        VENICE_API_KEY="vk-test",
        ANTHROPIC_API_KEY="ak-test",
    )
    assert llm.is_available() is True
    assert llm._provider_name() == "venice"

    llm = load_llm(
        monkeypatch,
        LLM_PROVIDER="venice",
        ANTHROPIC_API_KEY="ak-test",
    )
    assert llm.is_available() is False

    llm = load_llm(
        monkeypatch,
        LLM_PROVIDER="anthropic",
        VENICE_API_KEY="vk-test",
    )
    assert llm.is_available() is False
    assert llm._provider_name() == "anthropic"


def test_venice_structured_output_streaming_and_accounting(monkeypatch):
    llm = load_llm(
        monkeypatch,
        LLM_PROVIDER="venice",
        VENICE_API_KEY="vk-test",
        MODEL_INTENT="intent-model",
        MODEL_NARRATE="narrate-model",
        MODEL_COPILOT="copilot-model",
    )
    fake = FakeOpenAIClient()
    provider = llm._provider()

    monkeypatch.setattr(provider, "_client", lambda: fake)
    monkeypatch.setattr(llm, "_db_add", lambda _delta: None)
    llm._tokens_used = 0

    parsed = llm.parse("router", "question", RouteDecision, max_tokens=123)

    assert parsed == RouteDecision(
        intent="sleep",
        confidence=0.92,
        clarify_question="",
    )
    parse_call = fake.completions.calls[-1]
    assert parse_call["model"] == "copilot-model"
    assert parse_call["max_completion_tokens"] == 123
    assert parse_call["response_format"]["type"] == "json_schema"
    assert parse_call["response_format"]["json_schema"]["name"] == "RouteDecision"
    assert parse_call["extra_body"] == {
        "venice_parameters": {
            "disable_thinking": True,
            "include_venice_system_prompt": False,
        },
    }
    assert llm._tokens_used == 12

    complete = llm.complete("narrate", "payload", max_tokens=44)

    assert complete is not None
    complete_call = fake.completions.calls[-1]
    assert complete_call["model"] == "narrate-model"
    assert complete_call["max_completion_tokens"] == 44
    assert "response_format" not in complete_call
    assert llm._tokens_used == 24

    chunks = list(llm.stream("copilot answerer", "payload", max_tokens=55))

    assert chunks == ["hel", "lo"]
    stream_call = fake.completions.calls[-1]
    assert stream_call["model"] == "copilot-model"
    assert stream_call["stream"] is True
    assert stream_call["stream_options"] == {"include_usage": True}
    assert stream_call["max_completion_tokens"] == 55
    assert llm._tokens_used == 29


def test_local_provider_is_available_on_base_url_without_a_key(monkeypatch):
    """The local path gates on a configured endpoint, not a key — a local server
    needs none — so the LLM features run with no hosted credential."""
    llm = load_llm(
        monkeypatch,
        LLM_PROVIDER="local",
        LOCAL_BASE_URL="http://localhost:11434/v1",
    )
    assert llm._provider_name() == "local"
    assert llm.is_available() is True

    # No endpoint configured -> unavailable, degrade to the deterministic path.
    llm = load_llm(monkeypatch, LLM_PROVIDER="local", LOCAL_BASE_URL="")
    assert llm.is_available() is False


def test_local_structured_output_uses_one_model_and_no_vendor_extras(monkeypatch):
    """Local routes every role through the single LOCAL_MODEL and sends no
    Venice-specific extra_body — otherwise the wire format matches Venice."""
    llm = load_llm(
        monkeypatch,
        LLM_PROVIDER="local",
        LOCAL_BASE_URL="http://localhost:11434/v1",
        LOCAL_MODEL="local-model",
    )
    fake = FakeOpenAIClient()
    provider = llm._provider()
    monkeypatch.setattr(provider, "_client", lambda: fake)
    monkeypatch.setattr(llm, "_db_add", lambda _delta: None)
    llm._tokens_used = 0

    parsed = llm.parse("router", "question", RouteDecision, max_tokens=123)
    assert parsed == RouteDecision(intent="sleep", confidence=0.92, clarify_question="")
    parse_call = fake.completions.calls[-1]
    assert parse_call["model"] == "local-model"
    assert parse_call["extra_body"] == {}
    assert llm._tokens_used == 12

    llm.complete("narrate", "payload", max_tokens=44)
    assert fake.completions.calls[-1]["model"] == "local-model"

    chunks = list(llm.stream("copilot answerer", "payload", max_tokens=55))
    assert chunks == ["hel", "lo"]
    assert fake.completions.calls[-1]["model"] == "local-model"
    assert llm._tokens_used == 29
