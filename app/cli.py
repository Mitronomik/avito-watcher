import argparse
import asyncio
import json
import os
import time
import re
import tomllib
import uvicorn
from pathlib import Path
from urllib.parse import urlparse
from app.bot.telegram_commands import build_telegram_application
from app.parsers.avito_parser import AvitoParser
from app.parsers.errors import ParserError
from app.parsers.proxy_manager import ProxyManager
from app.parsers.proxy_url import validate_proxy_urls
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.repositories.search_repository import SearchRepository
from app.services.monitor_service import MonitorService, runtime_diagnostics


def _parser_stats_snapshot(parser_instance: "AvitoParser") -> dict:
    cycle_stats_fn = getattr(parser_instance, "cycle_stats", None)
    if not callable(cycle_stats_fn):
        return {}
    stats = cycle_stats_fn()
    if not isinstance(stats, dict):
        return {}
    return stats


def _build_parser() -> "AvitoParser":
    """Build AvitoParser with ProxyManager from PROXY_URLS env var (mirrors monitor.py logic)."""
    raw = os.getenv("PROXY_URLS", "").strip()
    proxy_urls = [u.strip() for u in raw.split(",") if u.strip()]
    proxy_urls = validate_proxy_urls(proxy_urls) if proxy_urls else []
    manager = ProxyManager(proxy_urls) if proxy_urls else None
    return AvitoParser(proxy_manager=manager)


def _card_to_dry_run_json(card) -> dict:
    return {
        "external_id": card.external_id,
        "title": card.title,
        "price": card.price,
        "url": card.url,
        "published_label": card.published_label,
        "published_at": card.published_at.isoformat() if card.published_at else None,
    }


async def _dry_run_search(url: str) -> dict:
    try:
        cards = await _build_parser().fetch_search_cards(url)
    except ParserError as exc:
        return {
            "ok": False,
            "total_cards": 0,
            "cards": [],
            "error_type": exc.error_type.value,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "ok": False,
            "total_cards": 0,
            "cards": [],
            "error_type": "unexpected_error",
            "error": str(exc),
        }

    return {
        "ok": True,
        "total_cards": len(cards),
        "cards": [_card_to_dry_run_json(card) for card in cards[:5]],
    }


def cmd_dry_run_search(args) -> None:
    result = asyncio.run(_dry_run_search(args.url))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_seed_search(args) -> None:
    init_db()
    with SessionLocal() as db:
        repo = SearchRepository(db)
        item = repo.create(
            name=args.name,
            source_url=args.url,
            filters_json={"seeded": True, "label": args.name},
            poll_interval_sec=args.interval,
        )
        db.commit()
        print(json.dumps({"id": item.id, "name": item.name, "url": item.source_url}, ensure_ascii=False))



PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,120}$")


def _validation_error(message: str) -> dict:
    return {"ok": False, "error_type": "validation_error", "error": message}


def _is_avito_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname == "avito.ru" or hostname.endswith(".avito.ru")


def _load_search_profile(path: str) -> dict:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read file: {exc}") from exc

    try:
        profile = tomllib.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"file must be valid UTF-8: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid TOML: {exc}") from exc

    if not isinstance(profile, dict):
        raise ValueError("profile must be a TOML table")

    name = profile.get("name")
    url = profile.get("url")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required and must be a non-empty string")
    if not PROFILE_NAME_RE.fullmatch(name.strip()):
        raise ValueError("name must match ^[a-z0-9][a-z0-9_-]{2,120}$")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url is required and must be a non-empty string")
    if not _is_avito_url(url.strip()):
        raise ValueError("url must be a valid avito.ru URL")

    poll_interval_sec = profile.get("poll_interval_sec")
    if poll_interval_sec is not None and (not isinstance(poll_interval_sec, int) or isinstance(poll_interval_sec, bool) or poll_interval_sec <= 0):
        raise ValueError("poll_interval_sec must be a positive integer")

    is_active = profile.get("is_active")
    if is_active is not None and not isinstance(is_active, bool):
        raise ValueError("is_active must be a boolean")

    filters = profile.get("filters", {})
    if not isinstance(filters, dict):
        raise ValueError("filters must be a table/object")

    filters_json = dict(filters)
    title = profile.get("title")
    if title is not None:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string when provided")
        filters_json["human_title"] = title

    return {
        "name": name.strip(),
        "url": url.strip(),
        "poll_interval_sec": poll_interval_sec,
        "is_active": is_active,
        "filters_json": filters_json,
    }


def cmd_upsert_search_profile(args) -> None:
    if args.activate and args.deactivate:
        print(json.dumps(_validation_error("--activate and --deactivate cannot be used together"), ensure_ascii=False, indent=2))
        return

    try:
        profile = _load_search_profile(args.file)
    except ValueError as exc:
        print(json.dumps(_validation_error(str(exc)), ensure_ascii=False, indent=2))
        return

    init_db()
    with SessionLocal() as db:
        repo = SearchRepository(db)
        existing = repo.get_by_name(profile["name"])

        target_is_active = profile["is_active"] if profile["is_active"] is not None else True
        if args.activate:
            target_is_active = True
        if args.deactivate:
            target_is_active = False

        if existing is None:
            action = "created"
            created = repo.create(
                name=profile["name"],
                source_url=profile["url"],
                filters_json=profile["filters_json"],
                poll_interval_sec=profile["poll_interval_sec"] or 180,
            )
            created.is_active = target_is_active
            if args.reset_baseline:
                created.baseline_initialized = False
                created.baseline_initialized_at = None
                created.next_run_at = None
            target = created
        else:
            action = "updated"
            existing.source_url = profile["url"]
            existing.filters_json = profile["filters_json"]
            if profile["poll_interval_sec"] is not None:
                existing.poll_interval_sec = profile["poll_interval_sec"]
            existing.is_active = target_is_active
            if args.reset_baseline:
                existing.baseline_initialized = False
                existing.baseline_initialized_at = None
                existing.next_run_at = None
            target = existing

        response = {
            "ok": True,
            "action": "dry_run" if args.dry_run else action,
            "id": target.id,
            "name": target.name,
            "is_active": target.is_active,
            "baseline_initialized": target.baseline_initialized,
            "poll_interval_sec": target.poll_interval_sec,
            "filters_json": target.filters_json,
            "source_url_preview": target.source_url[:180],
        }

        if args.dry_run:
            db.rollback()
        else:
            db.commit()

        print(json.dumps(response, ensure_ascii=False, indent=2))


def cmd_run_once(args) -> None:
    init_db()
    parser_instance = _build_parser()
    service = MonitorService(parser=parser_instance)
    if args.search_id is not None:
        started_at = time.perf_counter()
        try:
            result = service.run_once(args.search_id)
        except ParserError as exc:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            result = {
                "ok": False,
                "search_id": args.search_id,
                "error_type": exc.error_type.value,
                "error": str(exc),
                "elapsed_ms": elapsed_ms,
                "parser_stats": _parser_stats_snapshot(parser_instance),
                "runtime": runtime_diagnostics(),
            }
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            result = {
                "ok": False,
                "search_id": args.search_id,
                "error_type": exc.__class__.__name__,
                "error": str(exc),
                "elapsed_ms": elapsed_ms,
                "parser_stats": _parser_stats_snapshot(parser_instance),
                "runtime": runtime_diagnostics(),
            }
    else:
        result = service.run_all_searches()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_run_all(args) -> None:
    init_db()
    service = MonitorService(parser=_build_parser())
    result = service.run_all_searches()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_run_telegram_bot(args) -> None:
    del args
    init_db()
    application = build_telegram_application()
    application.run_polling()




def cmd_admin_server(args) -> None:
    from app.main import create_app

    app = create_app(admin_ui_enabled=True)
    uvicorn.run(app, host=args.host, port=args.port)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="avito-watcher")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed-search", help="Create a search job")
    seed.add_argument("--name", required=True)
    seed.add_argument("--url", required=True)
    seed.add_argument("--interval", type=int, default=180)
    seed.set_defaults(func=cmd_seed_search)

    upsert_profile = sub.add_parser("upsert-search-profile", help="Upsert a search job from TOML profile (technical import/export for DevOps)")
    upsert_profile.add_argument("--file", required=True)
    upsert_profile.add_argument("--reset-baseline", action="store_true")
    upsert_profile.add_argument("--activate", action="store_true")
    upsert_profile.add_argument("--deactivate", action="store_true")
    upsert_profile.add_argument("--dry-run", action="store_true")
    upsert_profile.set_defaults(func=cmd_upsert_search_profile)

    run_once = sub.add_parser("run-once", help="Run one monitoring cycle")
    run_once.add_argument("--search-id", type=int, required=False)
    run_once.set_defaults(func=cmd_run_once)

    run_all = sub.add_parser("run-all", help="Run all configured searches")
    run_all.set_defaults(func=cmd_run_all)

    dry_run = sub.add_parser("dry-run-search", help="Fetch and print Avito search parser diagnostics without side effects")
    dry_run.add_argument("--url", required=True)
    dry_run.set_defaults(func=cmd_dry_run_search)

    telegram_bot = sub.add_parser("telegram-bot", help="Run Telegram command bot")
    telegram_bot.set_defaults(func=cmd_run_telegram_bot)

    admin_server = sub.add_parser("admin-server", help="Run local admin UI server")
    admin_server.add_argument("--host", default="127.0.0.1")
    admin_server.add_argument("--port", type=int, default=8000)
    admin_server.set_defaults(func=cmd_admin_server)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
