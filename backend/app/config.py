from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "futurepassword"

    # Data lives at the repo root /data, mounted into the container at /app/data.
    data_dir: str = "data"

    anthropic_api_key: str = ""
    venice_api_key: str = ""
    llm_provider: str = "venice"
    llm_base_url: str = "https://api.venice.ai/api/v1"
    model_intent: str = "qwen3-next-80b"
    model_narrate: str = "qwen3-next-80b"
    model_copilot: str = "qwen3-next-80b"
    # The graph owns the reasoning/safety, so the LLM only does light structuring
    # + phrasing — Haiku is the right fit for the ~5s target and token efficiency.
    # Override with CLAUDE_MODEL=claude-opus-4-8 (or sonnet-4-6) for more polish.
    claude_model: str = "claude-haiku-4-5"
    # Soft ceiling on cumulative LLM tokens (input+output) for this process. 0 =
    # unlimited. When hit, every LLM path degrades to the deterministic graph
    # output + an on-brand "cooldown" note — the graph facts never depend on it.
    llm_token_budget: int = 0


settings = Settings()
