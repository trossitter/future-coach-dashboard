from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "futurepassword"

    # Data lives at the repo root /data, mounted into the container at /app/data.
    data_dir: str = "data"

    anthropic_api_key: str = ""
    # The graph owns the reasoning/safety, so the LLM only does light structuring
    # + phrasing — Haiku is the right fit for the ~5s target and token efficiency.
    # Override with CLAUDE_MODEL=claude-opus-4-8 (or sonnet-4-6) for more polish.
    claude_model: str = "claude-haiku-4-5"


settings = Settings()
