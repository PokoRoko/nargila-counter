"""Nargila Counter — Telegram bot that counts hookahs per user in a chat.

This is an *anti-rating*: users who smoke less are ranked higher (🥇).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import date, time, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

if TYPE_CHECKING:
    from telegram import Message, User

DB_PATH = os.getenv("DB_PATH", "hookah.db")
PLUS_ONE_RE = re.compile(r"\+\s*1\b")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_PATH = os.getenv("LOG_PATH", "logs/bot.log")
LOG_BACKUP_WEEKS = int(os.getenv("LOG_BACKUP_WEEKS", "4"))
TIMEZONE_STR = os.getenv("TIMEZONE", "Europe/Belgrade")

logger = logging.getLogger("nargila-counter")

MEDALS = ["🥇", "🥈", "🥉"]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging() -> None:
    log_level = getattr(logging, LOG_LEVEL, logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    log_file = Path(LOG_PATH)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    rotating_handler = TimedRotatingFileHandler(
        filename=log_file,
        when="W0",
        interval=1,
        backupCount=max(LOG_BACKUP_WEEKS, 1),
        encoding="utf-8",
    )
    rotating_handler.setLevel(log_level)
    rotating_handler.setFormatter(formatter)
    root_logger.addHandler(rotating_handler)

    # Reduce noisy library logs, keep only warnings/errors.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and a sane busy timeout.

    WAL enables concurrent readers while a write is in flight, and the busy
    timeout lets writers wait briefly instead of failing with
    "database is locked".
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)
    # `isolation_level=None` enables autocommit; explicit transactions are
    # used where atomicity matters (see `_add_hookah`).
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db() -> None:
    with _connect() as conn:
        _ = conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hookah_stats (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        _ = conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, message_id)
            )
            """
        )
        _ = conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hookah_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _ = conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_hookah_log_chat_created
                ON hookah_log (chat_id, created_at)
            """
        )
        _ = conn.execute(
            """
            CREATE TABLE IF NOT EXISTS period_winners (
                chat_id INTEGER NOT NULL,
                period_type TEXT NOT NULL,
                period_start TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                count INTEGER NOT NULL,
                PRIMARY KEY (chat_id, period_type, period_start)
            )
            """
        )
    logger.info("SQLite initialized at path=%s", DB_PATH)


def get_user_name(user: User) -> str:
    if user.username:
        return f"@{user.username}"
    name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    return name or f"user_{user.id}"


def _score_rows_asc(conn: sqlite3.Connection, chat_id: int) -> list[tuple[str, int]]:
    """Return (user_name, count) rows for a chat, ascending (anti-rating)."""
    return conn.execute(
        """
        SELECT user_name, count
        FROM hookah_stats
        WHERE chat_id = ?
        ORDER BY count ASC, user_name ASC
        """,
        (chat_id,),
    ).fetchall()


def add_hookah_and_get_score(
    chat_id: int, message_id: int, user: User
) -> tuple[list[tuple[str, int]], bool]:
    user_name = get_user_name(user)
    logger.info(
        "Count +1 request: chat_id=%s message_id=%s user_id=%s user=%s",
        chat_id,
        message_id,
        user.id,
        user_name,
    )
    with _connect() as conn:
        # Atomic: dedup + insert + log + read, all in one transaction.
        conn.execute("BEGIN")
        try:
            dedup_result = conn.execute(
                """
                INSERT OR IGNORE INTO processed_messages (chat_id, message_id)
                VALUES (?, ?)
                """,
                (chat_id, message_id),
            )
            is_new_message = dedup_result.rowcount == 1

            if not is_new_message:
                conn.execute("COMMIT")
                logger.info(
                    "Duplicate message ignored: chat_id=%s message_id=%s",
                    chat_id,
                    message_id,
                )
                rows = _score_rows_asc(conn, chat_id)
                return rows, False

            _ = conn.execute(
                """
                INSERT INTO hookah_stats (chat_id, user_id, user_name, count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    count = count + 1,
                    user_name = excluded.user_name
                """,
                (chat_id, user.id, user_name),
            )
            _ = conn.execute(
                """
                INSERT INTO hookah_log (chat_id, user_id, user_name)
                VALUES (?, ?, ?)
                """,
                (chat_id, user.id, user_name),
            )
            rows = _score_rows_asc(conn, chat_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    logger.info("Score updated: chat_id=%s users=%s", chat_id, len(rows))
    return rows, True


def get_score(chat_id: int) -> list[tuple[str, int]]:
    with _connect() as conn:
        return _score_rows_asc(conn, chat_id)


def get_score_with_user_ids(chat_id: int) -> list[tuple[int, str, int]]:
    """Return (user_id, user_name, count) rows for a chat, ascending."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT user_id, user_name, count
            FROM hookah_stats
            WHERE chat_id = ?
            ORDER BY count ASC, user_name ASC
            """,
            (chat_id,),
        ).fetchall()


def get_period_score(chat_id: int, period_type: str) -> list[tuple[str, int]]:
    if period_type == "week":
        start = _week_start().isoformat()
    else:
        start = _month_start().isoformat()
    with _connect() as conn:
        return conn.execute(
            """
            SELECT user_name, COUNT(*) as cnt
            FROM hookah_log
            WHERE chat_id = ? AND created_at >= ?
            GROUP BY user_id
            ORDER BY cnt ASC, user_name ASC
            """,
            (chat_id, start),
        ).fetchall()


def get_win_counts(chat_id: int) -> dict[int, dict[str, int]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT user_id, period_type, COUNT(*) as wins
            FROM period_winners
            WHERE chat_id = ?
            GROUP BY user_id, period_type
            """,
            (chat_id,),
        ).fetchall()
    result: dict[int, dict[str, int]] = {}
    for user_id, period_type, wins in rows:
        result.setdefault(user_id, {})[period_type] = wins
    return result


def get_all_chat_ids() -> list[int]:
    with _connect() as conn:
        rows = conn.execute("SELECT DISTINCT chat_id FROM hookah_stats").fetchall()
    return [r[0] for r in rows]


def save_period_winner(
    chat_id: int,
    period_type: str,
    period_start: str,
    user_id: int,
    user_name: str,
    count: int,
) -> None:
    with _connect() as conn:
        _ = conn.execute(
            """
            INSERT OR IGNORE INTO period_winners
                (chat_id, period_type, period_start, user_id, user_name, count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, period_type, period_start, user_id, user_name, count),
        )


def determine_period_winner(
    chat_id: int, start: date, end: date
) -> tuple[str, int, int] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT user_name, user_id, COUNT(*) as cnt
            FROM hookah_log
            WHERE chat_id = ? AND created_at >= ? AND created_at < ?
            GROUP BY user_id
            ORDER BY cnt ASC, user_name ASC
            LIMIT 1
            """,
            (chat_id, start.isoformat(), end.isoformat()),
        ).fetchone()
    return row  # (user_name, user_id, count) or None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _place_icon(index: int) -> str:
    if index < len(MEDALS):
        return MEDALS[index]
    return f"{index + 1}."


def format_score(
    rows: list[tuple[str, int]],
    *,
    title: str = "📉 Общий счёт кальянов (меньше — лучше):",
    empty_msg: str = "Пока нет записей.",
) -> str:
    """Render a ranked score list (anti-rating: fewer = higher place).

    ``title`` / ``empty_msg`` let callers reuse this for period stats
    (week/month) without duplicating the formatting loop.
    """
    if not rows:
        return empty_msg
    lines = [title]
    for i, (name, count) in enumerate(rows):
        icon = _place_icon(i)
        lines.append(f"{icon} {name} — {count}")
    return "\n".join(lines)


def _week_start(d: date | None = None) -> date:
    d = d or date.today()
    return d - timedelta(days=d.weekday())


def _month_start(d: date | None = None) -> date:
    d = d or date.today()
    return d.replace(day=1)


def _prev_week_range() -> tuple[date, date]:
    start = _week_start() - timedelta(weeks=1)
    end = start + timedelta(weeks=1)
    return start, end


def _prev_month_range() -> tuple[date, date]:
    first_of_this_month = _month_start()
    end = first_of_this_month
    first_of_prev = (end - timedelta(days=1)).replace(day=1)
    return first_of_prev, end


def _plurals_count(n: int, forms: tuple[str, str, str]) -> str:
    """Pick correct Russian plural form for a count.

    forms = (one, few, many), e.g. ("кальян", "кальяна", "кальянов").
    """
    n1 = abs(n) % 100
    n2 = n1 % 10
    if 10 < n1 < 20:
        return forms[2]
    if 1 < n2 < 5:
        return forms[1]
    if n2 == 1:
        return forms[0]
    return forms[2]


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------
def has_image(message: Message) -> bool:
    if message.photo:
        return True
    if message.document and message.document.mime_type:
        return message.document.mime_type.startswith("image/")
    return False


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def on_hookah_message(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat:
        return
    user = message.from_user
    if not user:
        logger.warning(
            "Skip message without from_user: chat_id=%s message_id=%s",
            chat.id,
            message.message_id,
        )
        return

    text = (message.caption or "") + " " + (message.text or "")
    if not has_image(message):
        return

    if not PLUS_ONE_RE.search(text):
        return

    rows, was_counted = add_hookah_and_get_score(chat.id, message.message_id, user)
    if was_counted:
        # After a +1, reply with the CURRENT WEEK score (not the all-time
        # total — that's what /stats is for). Slicing to the week keeps the
        # per-message feedback focused on the active period.
        week_rows = get_period_score(chat.id, "week")
        _ = await message.reply_text(
            format_score(
                week_rows,
                title="📉 Счёт за эту неделю (меньше — лучше):",
                empty_msg="На этой неделе пока нет записей.",
            )
        )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message:
        return
    logger.info("Command /stats: chat_id=%s args=%s", chat.id, context.args)

    period = (context.args[0].lower() if context.args else "").strip()
    if period in {"w", "week", "неделя", "неделю", "нед"}:
        rows = get_period_score(chat.id, "week")
        title = "📉 Счёт за текущую неделю (меньше — лучше):"
    elif period in {"m", "month", "месяц", "мес"}:
        rows = get_period_score(chat.id, "month")
        title = "📉 Счёт за текущий месяц (меньше — лучше):"
    else:
        total_rows = get_score(chat.id)
        if not total_rows:
            _ = await message.reply_text("Пока нет записей.")
            return

        win_counts = get_win_counts(chat.id)
        lines = [format_score(total_rows)]

        if win_counts:
            lines.append("")
            lines.append("🏆 Победы (анти-рейтинг — кто выкурил меньше всех):")
            for uid, name, _total in get_score_with_user_ids(chat.id):
                w = win_counts.get(uid, {})
                week_wins = w.get("week", 0)
                month_wins = w.get("month", 0)
                if week_wins or month_wins:
                    lines.append(
                        f"  • {name}: недель — {week_wins}, месяцев — {month_wins}"
                    )

        _ = await message.reply_text("\n".join(lines))
        return

    if not rows:
        _ = await message.reply_text("За этот период записей пока нет.")
        return
    lines = [title]
    for i, (name, count) in enumerate(rows):
        icon = _place_icon(i)
        lines.append(f"{icon} {name} — {count}")
    _ = await message.reply_text("\n".join(lines))


HELP_TEXT = (
    "🪔 *Nargila Counter* — анти-рейтинг кальянов в чате.\n\n"
    "Чем *меньше* ты куришь, тем выше ты в рейтинге 🥇.\n\n"
    "*Команды:*\n"
    "/stats — общий счёт и победители за неделю/месяц\n"
    "/stats week — счёт за текущую неделю\n"
    "/stats month — счёт за текущий месяц\n"
    "/help — эта справка\n\n"
    "*Как засчитать кальян:*\n"
    "Пришли фото с подписью `+1` — бот добавит +1 в твой счёт."
)


async def start(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        _ = await update.effective_message.reply_text(
            HELP_TEXT, parse_mode=ParseMode.MARKDOWN
        )


async def help_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        _ = await update.effective_message.reply_text(
            HELP_TEXT, parse_mode=ParseMode.MARKDOWN
        )


async def on_error(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled update error: %s", context.error)


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------
async def _announce_period_winner(
    context: ContextTypes.DEFAULT_TYPE, period_type: str
) -> None:
    if period_type == "week":
        start, end = _prev_week_range()
        intro = "🏆 Итоги прошлой недели!"
    else:
        start, end = _prev_month_range()
        intro = "🏆 Итоги прошлого месяца!"

    period_start = start.isoformat()

    for chat_id in get_all_chat_ids():
        with _connect() as conn:
            existing = conn.execute(
                """
                SELECT 1 FROM period_winners
                WHERE chat_id = ? AND period_type = ? AND period_start = ?
                """,
                (chat_id, period_type, period_start),
            ).fetchone()
        if existing:
            continue

        winner = determine_period_winner(chat_id, start, end)
        if winner is None:
            continue

        user_name, user_id, count = winner
        save_period_winner(chat_id, period_type, period_start, user_id, user_name, count)

        word = _plurals_count(count, ("кальян", "кальяна", "кальянов"))
        text = (
            f"{intro}\n"
            f"Победитель: {user_name} — выкурил меньше всех, всего {count} {word}.\n"
            f"Так держать! 🌿"
        )
        try:
            _ = await context.bot.send_message(chat_id=chat_id, text=text)
            logger.info(
                "Announced %s winner for chat_id=%s: %s",
                period_type,
                chat_id,
                user_name,
            )
        except Exception:
            logger.exception("Failed to send winner message to chat_id=%s", chat_id)


async def announce_weekly_winner(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _announce_period_winner(context, "week")


async def announce_monthly_winner(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _announce_period_winner(context, "month")


def _is_monday() -> bool:
    return date.today().weekday() == 0


def _is_first_of_month() -> bool:
    return date.today().day == 1


async def check_period_jobs(context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_monday():
        await announce_weekly_winner(context)
    if _is_first_of_month():
        await announce_monthly_winner(context)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def build_application() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_hookah_message)
    )
    app.add_error_handler(on_error)

    # Daily check at 00:00 in the configured timezone: announces weekly winner
    # on Mondays, monthly on the 1st.
    jq = app.job_queue
    if jq is not None:
        try:
            tz = ZoneInfo(TIMEZONE_STR)
        except Exception:
            logger.warning(
                "Unknown TIMEZONE=%s, falling back to UTC", TIMEZONE_STR
            )
            tz = ZoneInfo("UTC")
        _ = jq.run_daily(check_period_jobs, time=time(0, 0, tzinfo=tz))
    else:
        logger.warning(
            "JobQueue not available (no uvloop?); period winner checks disabled"
        )

    return app


def main() -> None:
    setup_logging()
    logger.info("Starting bot process")
    init_db()
    app = build_application()

    logger.warning(
        "For group chats, disable BotFather privacy mode "
        "(/setprivacy -> Disable) or the bot will not receive most non-command messages."
    )
    logger.info("Bot polling started")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
