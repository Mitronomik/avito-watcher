from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    api_key: str = ""
    # Secret key to protect POST /monitor/run. Set API_KEY env var.
    # If empty, the endpoint is unprotected (dev mode only).
    scrape_headless: bool = True
    scrape_timeout_ms: int = 45000
    scrape_concurrency: int = 2
    proxy_urls: str = ""
    # Comma-separated proxy URLs: http://user:pass@host:port,http://...
    # Set via PROXY_URLS env var. Used by AvitoParser via _build_parser().


settings = Settings()
