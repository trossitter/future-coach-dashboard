from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "futurepassword"

    # Data lives at the repo root /data, mounted into the container at /app/data.
    data_dir: str = "data"

    anthropic_api_key: str = ""


settings = Settings()
