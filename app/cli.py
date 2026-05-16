import argparse
import json
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.repositories.search_repository import SearchRepository
from app.services.monitor_service import MonitorService


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
    service = MonitorService()
    result = service.run_once(args.url) if args.url else service.run_all_searches()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_run_all(args) -> None:
    init_db()
    service = MonitorService()
    result = service.run_all_searches()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="avito-watcher")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed-search", help="Create a search job")
    seed.add_argument("--name", required=True)
    seed.add_argument("--url", required=True)
    seed.add_argument("--interval", type=int, default=180)
    seed.set_defaults(func=cmd_seed_search)

    run_once = sub.add_parser("run-once", help="Run one monitoring cycle")
    run_once.add_argument("--url", required=False)
    run_once.set_defaults(func=cmd_run_once)

    run_all = sub.add_parser("run-all", help="Run all configured searches")
    run_all.set_defaults(func=cmd_run_all)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
