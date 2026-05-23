import json
import re
from datetime import UTC, date, datetime
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
/status - show watcher status
/showfilters <search_id> - show search filters
/setfilters <search_id> key=value key=value ... - merge search filters
/clearfilters <search_id> - clear search filters"""


def _is_authorized(update: Update) -> bool:
    """Return True if the message comes from the configured owner chat.

    If TELEGRAM_CHAT_ID is not set, allows all (dev mode).
    Compares as strings to handle both int and str chat IDs from env.
    """
    if not settings.telegram_chat_id:
        return True  # unconfigured → dev mode, allow all
    chat = getattr(update, "effective_chat", None)
    if chat is None:
        return not hasattr(update, "effective_chat")
    return str(chat.id) == str(settings.telegram_chat_id)


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
        if not _is_authorized(update):
            await _reply(update, "Unauthorized.")
            return
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
        if not _is_authorized(update):
            await _reply(update, "Unauthorized.")
            return
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
        if not _is_authorized(update):
            await _reply(update, "Unauthorized.")
            return
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

    async def showfilters(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        search_id = _parse_search_id(context)
        if search_id is None:
            await _reply(update, "Usage: /showfilters <search_id>")
            return

        with self.session_factory() as db:
            repo = SearchRepository(db)
            search = repo.get(search_id)
            if search is None:
                await _reply(update, f"Search not found: {search_id}")
                return
            filters_json = dict(search.filters_json or {})

        if not filters_json:
            await _reply(update, f"Filters for search {search_id} are empty.")
            return

        await _reply(
            update,
            f"Filters for search {search_id}:\n"
            f"{json.dumps(filters_json, ensure_ascii=False, indent=2, sort_keys=True)}",
        )

    async def setfilters(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = list(getattr(context, "args", []) or [])
        if not _is_authorized(update):
            await _reply(update, "Unauthorized.")
            return
        search_id = _parse_search_id_from_args(args)
        if search_id is None or len(args) < 2:
            await _reply(update, "Usage: /setfilters <search_id> key=value key=value ...")
            return

        parsed_filters, error = _parse_filter_assignments(args[1:])
        if error is not None:
            await _reply(update, error)
            return

        with self.session_factory() as db:
            repo = SearchRepository(db)
            search = repo.get(search_id)
            if search is None:
                await _reply(update, f"Search not found: {search_id}")
                return

            merged_filters = dict(search.filters_json or {})
            merged_filters.update(parsed_filters)
            repo.update_filters(search, merged_filters)
            db.commit()

        changed_keys = ", ".join(sorted(parsed_filters))
        await _reply(update, f"Filters updated for search {search_id}: {changed_keys}")

    async def clearfilters(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        search_id = _parse_search_id(context)
        if not _is_authorized(update):
            await _reply(update, "Unauthorized.")
            return
        if search_id is None:
            await _reply(update, "Usage: /clearfilters <search_id>")
            return

        with self.session_factory() as db:
            repo = SearchRepository(db)
            search = repo.get(search_id)
            if search is None:
                await _reply(update, f"Search not found: {search_id}")
                return
            repo.update_filters(search, {})
            db.commit()

        await _reply(update, f"Filters cleared for search {search_id}.")


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
    application.add_handler(CommandHandler("showfilters", handlers.showfilters))
    application.add_handler(CommandHandler("setfilters", handlers.setfilters))
    application.add_handler(CommandHandler("clearfilters", handlers.clearfilters))
    return application


NUMERIC_FILTER_KEYS = {"min_price", "max_price", "min_area", "max_area", "max_age_hours"}
KEYWORD_FILTER_KEYS = {"include_keywords", "exclude_keywords", "location_keywords"}
DATE_FILTER_KEYS = {"published_after", "published_on_date"}
BOOL_FILTER_KEYS = {"require_published_at"}
STRING_FILTER_KEYS = {"missing_published_at_policy", "source_sort"}
SUPPORTED_FILTER_KEYS = (
    NUMERIC_FILTER_KEYS
    | KEYWORD_FILTER_KEYS
    | DATE_FILTER_KEYS
    | BOOL_FILTER_KEYS
    | STRING_FILTER_KEYS
)
BOOL_VALUES = {
    "true": True,
    "yes": True,
    "1": True,
    "false": False,
    "no": False,
    "0": False,
}
PUBLISHED_ON_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _add_usage() -> str:
    return "Usage: /add <url> [name]"


def _is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _parse_search_id(context: Any) -> int | None:
    args = list(getattr(context, "args", []) or [])
    if len(args) != 1:
        return None
    return _parse_search_id_from_args(args)


def _parse_search_id_from_args(args: list[str]) -> int | None:
    if not args:
        return None
    try:
        return int(args[0])
    except ValueError:
        return None


def _parse_filter_assignments(args: list[str]) -> tuple[dict[str, Any], str | None]:
    parsed: dict[str, Any] = {}
    for arg in args:
        if "=" not in arg:
            return {}, f"Invalid filter assignment: {arg}. Expected key=value."

        key, raw_value = arg.split("=", 1)
        key = key.strip()
        if key not in SUPPORTED_FILTER_KEYS:
            supported_keys = ", ".join(sorted(SUPPORTED_FILTER_KEYS))
            return {}, f"Unknown filter key: {key}. Supported keys: {supported_keys}"

        value, error = _parse_filter_value(key, raw_value)
        if error is not None:
            return {}, error
        parsed[key] = value

    return parsed, None


def _parse_filter_value(key: str, raw_value: str) -> tuple[Any, str | None]:
    if key in NUMERIC_FILTER_KEYS:
        try:
            return float(raw_value), None
        except ValueError:
            return None, f"Invalid numeric value for {key}: {raw_value}"

    if key in KEYWORD_FILTER_KEYS:
        return _parse_keyword_filter(raw_value), None

    if key == "published_on_date":
        if not PUBLISHED_ON_DATE_RE.fullmatch(raw_value):
            return None, f"Invalid date for {key}: {raw_value}. Use YYYY-MM-DD."
        try:
            date.fromisoformat(raw_value)
        except ValueError:
            return None, f"Invalid date for {key}: {raw_value}. Use YYYY-MM-DD."
        return raw_value, None

    if key == "missing_published_at_policy":
        if raw_value not in {"reject", "allow", "allow_when_date_sorted"}:
            return None, (
                "Invalid value for missing_published_at_policy: "
                f"{raw_value}. Use reject|allow|allow_when_date_sorted."
            )
        return raw_value, None

    if key == "source_sort":
        return raw_value, None

    if key == "published_after":
        try:
            datetime.fromisoformat(_normalize_iso_datetime(raw_value))
        except ValueError:
            return None, f"Invalid ISO datetime for {key}: {raw_value}"
        return raw_value, None

    if key in BOOL_FILTER_KEYS:
        bool_value = BOOL_VALUES.get(raw_value.strip().lower())
        if bool_value is None:
            return None, f"Invalid boolean value for {key}: {raw_value}"
        return bool_value, None

    return None, f"Unsupported filter key: {key}"


def _parse_keyword_filter(raw_value: str) -> list[str]:
    stripped = raw_value.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            decoded = stripped
        if isinstance(decoded, list):
            return [item.strip() for item in map(str, decoded) if item.strip()]

    return [item.strip() for item in stripped.split(",") if item.strip()]


def _normalize_iso_datetime(value: str) -> str:
    if value.endswith("Z"):
        return value[:-1] + "+00:00"
    return value


async def _reply(update: Update, text: str) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(text)


def _format_search_line(search: SearchJob) -> str:
    active = "active" if search.is_active else "inactive"
    baseline = "baseline initialized" if search.baseline_initialized else "baseline pending"
    next_run_at = search.next_run_at.isoformat(sep=" ") if search.next_run_at else "not scheduled"
    return f"#{search.id} | {search.name} | {active} | {baseline} | next_run_at={next_run_at}"
