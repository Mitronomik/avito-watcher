from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.search_job import SearchJob
from app.repositories.search_repository import SearchRepository

SessionFactory = Callable[[], Session]


HELP_TEXT = """Avito Watcher commands:
/start - show this help
/help - show this help
/add <url> [name] - add a search job
/list - list search jobs
/pause <search_id> - pause a search job
/resume <search_id> - resume a search job
/status - show watcher status"""


class TelegramSearchCommandHandlers:
    def __init__(self, session_factory: SessionFactory = SessionLocal) -> None:
        self.session_factory = session_factory

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await _reply(update, HELP_TEXT)

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await _reply(update, HELP_TEXT)

    async def add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = list(getattr(context, "args", []) or [])
        if not args:
            await _reply(update, _add_usage())
            return

        source_url = args[0].strip()
        if not _is_http_url(source_url):
            await _reply(update, _add_usage())
            return

        name = " ".join(args[1:]).strip() or "avito_search"

        with self.session_factory() as db:
            repo = SearchRepository(db)
            search = repo.create(
                name=name,
                source_url=source_url,
                poll_interval_sec=180,
            )
            search_id = search.id
            db.commit()

        await _reply(
            update,
            f"Search added: id={search_id}, name={name}. Baseline will initialize on the next worker run without sending alerts.",
        )

    async def list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        with self.session_factory() as db:
            repo = SearchRepository(db)
            searches = repo.list_all()
            lines = [_format_search_line(search) for search in searches]

        if not lines:
            await _reply(update, "No searches configured.")
            return
        await _reply(update, "Searches:\n" + "\n".join(lines))

    async def pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        search_id = _parse_search_id(context)
        if search_id is None:
            await _reply(update, "Usage: /pause <search_id>")
            return

        with self.session_factory() as db:
            repo = SearchRepository(db)
            search = repo.get(search_id)
            if search is None:
                await _reply(update, f"Search not found: {search_id}")
                return
            repo.pause(search)
            db.commit()

        await _reply(update, f"Search paused: {search_id}")

    async def resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        search_id = _parse_search_id(context)
        if search_id is None:
            await _reply(update, "Usage: /resume <search_id>")
            return

        with self.session_factory() as db:
            repo = SearchRepository(db)
            search = repo.get(search_id)
            if search is None:
                await _reply(update, f"Search not found: {search_id}")
                return
            repo.resume(search)
            db.commit()

        await _reply(update, f"Search resumed: {search_id}")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        now = datetime.now(UTC).replace(tzinfo=None)
        with self.session_factory() as db:
            repo = SearchRepository(db)
            searches = repo.list_all()
            active_count = sum(1 for search in searches if search.is_active)
            due_count = len(repo.list_due_active(now))
            errors = [search for search in searches if search.last_error]
            lines = [
                f"Searches: {len(searches)}",
                f"Active: {active_count}",
                f"Due now: {due_count}",
            ]
            if errors:
                lines.append("Last errors:")
                lines.extend(
                    f"- #{search.id} {search.name}: {search.last_error}" for search in errors
                )
            else:
                lines.append("Last errors: none")

        await _reply(update, "\n".join(lines))


def build_telegram_application(
    session_factory: SessionFactory = SessionLocal,
) -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    handlers = TelegramSearchCommandHandlers(session_factory=session_factory)
    application = Application.builder().token(settings.telegram_bot_token).build()
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help))
    application.add_handler(CommandHandler("add", handlers.add))
    application.add_handler(CommandHandler("list", handlers.list))
    application.add_handler(CommandHandler("pause", handlers.pause))
    application.add_handler(CommandHandler("resume", handlers.resume))
    application.add_handler(CommandHandler("status", handlers.status))
    return application


def _add_usage() -> str:
    return "Usage: /add <url> [name]"


def _is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _parse_search_id(context: Any) -> int | None:
    args = list(getattr(context, "args", []) or [])
    if len(args) != 1:
        return None
    try:
        return int(args[0])
    except ValueError:
        return None


async def _reply(update: Update, text: str) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(text)


def _format_search_line(search: SearchJob) -> str:
    active = "active" if search.is_active else "inactive"
    baseline = "baseline initialized" if search.baseline_initialized else "baseline pending"
    next_run_at = search.next_run_at.isoformat(sep=" ") if search.next_run_at else "not scheduled"
    return f"#{search.id} | {search.name} | {active} | {baseline} | next_run_at={next_run_at}"
