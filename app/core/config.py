from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str
    redis_url: str
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    scrape_headless: bool = True
    scrape_timeout_ms: int = 45000
    scrape_concurrency: int = 2
    proxy_urls: str = ""
    # Comma-separated mobile proxy URLs, mirrors PROXY_URLS env var.
    # app/workers/monitor.py reads os.getenv("PROXY_URLS") directly,
    # but this field documents the variable and enables future config-driven injection.


settings = Settings()
