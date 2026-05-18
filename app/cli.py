import argparse
import asyncio
import json
import os
from app.bot.telegram_commands import build_telegram_application
from app.parsers.avito_parser import AvitoParser
from app.parsers.errors import ParserError
from app.parsers.proxy_manager import ProxyManager
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.repositories.search_repository import SearchRepository
from app.services.monitor_service import MonitorService


def _build_parser() -> "AvitoParser":
    """Build AvitoParser with ProxyManager from PROXY_URLS env var (mirrors monitor.py logic)."""
    raw = os.getenv("PROXY_URLS", "").strip()
    proxy_urls = [u.strip() for u in raw.split(",") if u.strip()]
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


def cmd_run_once(args) -> None:
    init_db()
    service = MonitorService(parser=_build_parser())
    if args.search_id is not None:
        result = service.run_once(args.search_id)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="avito-watcher")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed-search", help="Create a search job")
    seed.add_argument("--name", required=True)
    seed.add_argument("--url", required=True)
    seed.add_argument("--interval", type=int, default=180)
    seed.set_defaults(func=cmd_seed_search)

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

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
