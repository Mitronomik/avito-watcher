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
    llm_listing_detail_extraction_enabled: bool = False
    llm_listing_detail_extraction_max_input_chars: int = 12000
    llm_listing_detail_extraction_prompt_version: str = "listing-detail-extraction-v1"
    llm_listing_detail_extraction_schema_version: str = (
        "listing-detail-extraction-schema-v1"
    )
    llm_data_quality_agent_enabled: bool = False
    llm_data_quality_agent_prompt_version: str = "data-quality-agent-v1"
    llm_data_quality_agent_schema_version: str = "data-quality-assessment-schema-v1"
    llm_data_quality_agent_max_input_chars: int = 14000
    llm_data_quality_agent_rag_enabled: bool = False
    llm_data_quality_agent_rag_limit: int = 5
    llm_data_quality_agent_rag_max_chars: int = 4000
    llm_data_quality_agent_rag_query_max_chars: int = 1000
    llm_data_quality_agent_rag_note_types: str = "rulebook,false_positive,domain_note"
    research_agent_enabled: bool = False
    research_agent_provider: str = "off"
    research_agent_model: str = ""
    research_agent_base_url: str = ""
    research_agent_api_key: str = ""
    research_agent_timeout_sec: int = 60
    research_agent_max_retries: int = 1
    research_agent_max_queries: int = 3
    research_agent_max_input_chars: int = 12000
    research_agent_max_output_chars: int = 12000
    research_agent_prompt_version: str = "research-agent-v1"
    research_agent_schema_version: str = "research-agent-result-v1"
    market_evidence_default_ttl_days: int = 30
    market_evidence_min_confidence_for_reuse: float = 0.5
    market_evidence_max_retrieval_items: int = 10
    weekly_strategy_agent_enabled: bool = False
    weekly_strategy_agent_provider: Literal["off", "openai_compatible"] = "off"
    weekly_strategy_agent_model: str = ""
    weekly_strategy_agent_base_url: str = ""
    weekly_strategy_agent_api_key: str = ""
    weekly_strategy_agent_timeout_sec: int = 60
    weekly_strategy_agent_max_retries: int = 1
    weekly_strategy_agent_max_input_chars: int = 16000
    weekly_strategy_agent_max_output_chars: int = 12000
    weekly_strategy_agent_prompt_version: str = "weekly-strategy-agent-v1"
    weekly_strategy_agent_schema_version: str = "weekly-strategy-agent-result-v1"
    agent_orchestration_enabled: bool = False
    agent_orchestration_allow_monitor_trigger: bool = False
    agent_orchestration_max_chain_depth: int = 4
    agent_orchestration_max_tasks_per_listing: int = 10
    agent_orchestration_default_timeout_sec: int = 120
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    api_key: str = ""
    admin_ui_enabled: bool = False
    admin_ui_mode: str = "operator"
    admin_ui_language: str = "ru"
    admin_ui_allow_query_api_key: bool = False
    admin_ui_technical_ops_enabled: bool = False
    admin_ui_read_key: str = ""
    admin_ui_write_key: str = ""
    admin_ui_technical_write_key: str = ""
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
