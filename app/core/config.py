from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    scoring_enabled: bool = True
    deterministic_analysis_on_monitor: bool = False
    llm_provider: Literal["off", "ollama", "openai_compatible"] = "off"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_sec: int = 60
    llm_max_retries: int = 2
    llm_retry_delay_sec: float = 1.0
    llm_shadow_mode: bool = True
    llm_prompt_version: str = "listing-summary-v1"
    llm_review_copilot_enabled: bool = False
    llm_review_copilot_provider: Literal["openai_compatible"] = "openai_compatible"
    llm_review_copilot_model: str = ""
    llm_review_copilot_prompt_version: str = "review-copilot-v1"
    llm_review_copilot_timeout_sec: int = 60
    llm_review_copilot_max_retries: int = 2
    llm_review_copilot_rag_enabled: bool = False
    llm_review_copilot_rag_limit: int = 5
    llm_review_copilot_rag_max_chars: int = 4000
    llm_review_copilot_rag_query_max_chars: int = 1000
    llm_review_copilot_rag_note_types: str = "rulebook,false_positive,domain_note"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    api_key: str = ""
    admin_ui_enabled: bool = False
    # Secret key to protect POST /monitor/run. Set API_KEY env var.
    # If empty, the endpoint is unprotected (dev mode only).
    scrape_headless: bool = True
    scrape_humanize: bool = False
    scrape_timeout_ms: int = 45000
    scrape_concurrency: int = 2
    scrape_max_pages: int = 1
    scrape_cards_per_page_limit: int = 30
    scrape_stop_on_duplicate_page: bool = True
    scrape_page_delay_ms: int = 0
    scrape_page_jitter_ms: int = 0
    scrape_enrich_missing_published_at: bool = False
    scrape_enrich_item_page_details: bool = False
    scrape_item_page_delay_ms: int = 0
    scrape_item_page_jitter_ms: int = 0
    scrape_item_page_limit_per_run: int = 10
    scrape_debug_dump_html: bool = False
    scrape_debug_dump_dir: str = "./data/debug_html"
    scrape_debug_dump_max_bytes: int = 2_000_000
    scrape_preferred_engine: Literal["auto", "nodriver", "camoufox"] = "auto"
    scrape_allowed_engines: Literal["both", "nodriver", "camoufox"] = "both"
    scrape_timeout_retry_once: bool = False
    scrape_timeout_retry_delay_ms: int = 300
    scrape_camoufox_retry_on_driver_crash: bool = True
    scrape_nodriver_browser_executable_path: str = ""
    proxy_urls: str = ""
    proxy_quarantine_seconds: int = 7200
    alert_channels: str = "jsonl,telegram"
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""
    google_sheets_webhook_enabled: bool = False
    google_sheets_webhook_url: str = ""
    google_sheets_webhook_secret: str = ""
    google_sheets_webhook_timeout_sec: int = 15
    jsonl_outbox_enabled: bool = True
    jsonl_outbox_path: str = "./data/alerts.jsonl"
    alert_delivery_bulk_guard_enabled: bool = True
    alert_delivery_max_new_per_cycle: int = 50
    monitor_worker_lock_path: str = "./data/monitor_worker.lock"
    monitor_worker_status_path: str = "./data/worker_status.json"
    monitor_worker_stale_after_seconds: int = 180
    # Comma-separated proxy URLs: http://user:pass@host:port,http://...
    # Set via PROXY_URLS env var. Used by AvitoParser via _build_parser().


settings = Settings()
