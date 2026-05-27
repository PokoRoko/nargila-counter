import logging
import os
import re
import sqlite3
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

DB_PATH = os.getenv("DB_PATH", "hookah.db")
PLUS_ONE_RE = re.compile(r"\+\s*1\b")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_PATH = os.getenv("LOG_PATH", "logs/bot.log")
LOG_BACKUP_WEEKS = int(os.getenv("LOG_BACKUP_WEEKS", "4"))

logger = logging.getLogger("nargila-counter")


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


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
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
    logger.info("SQLite initialized at path=%s", DB_PATH)


def get_user_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    return name or f"user_{user.id}"


def add_hookah_and_get_score(chat_id: int, message_id: int, user) -> tuple[list[tuple[str, int]], bool]:
    user_name = get_user_name(user)
    logger.info(
        "Count +1 request: chat_id=%s message_id=%s user_id=%s user=%s",
        chat_id,
        message_id,
        user.id,
        user_name,
    )
    with sqlite3.connect(DB_PATH) as conn:
        # Idempotency on restarts/retries: one message can be counted only once.
        dedup_result = conn.execute(
            """
            INSERT OR IGNORE INTO processed_messages (chat_id, message_id)
            VALUES (?, ?)
            """,
            (chat_id, message_id),
        )
        is_new_message = dedup_result.rowcount == 1
        if not is_new_message:
            logger.info("Duplicate message ignored: chat_id=%s message_id=%s", chat_id, message_id)
            rows = conn.execute(
                """
                SELECT user_name, count
                FROM hookah_stats
                WHERE chat_id = ?
                ORDER BY count DESC, user_name ASC
                """,
                (chat_id,),
            ).fetchall()
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
        rows = conn.execute(
            """
            SELECT user_name, count
            FROM hookah_stats
            WHERE chat_id = ?
            ORDER BY count DESC, user_name ASC
            """,
            (chat_id,),
        ).fetchall()
    logger.info("Score updated: chat_id=%s users=%s", chat_id, len(rows))
    return rows, True


def get_score(chat_id: int) -> list[tuple[str, int]]:
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            """
            SELECT user_name, count
            FROM hookah_stats
            WHERE chat_id = ?
            ORDER BY count DESC, user_name ASC
            """,
            (chat_id,),
        ).fetchall()


def format_score(rows: list[tuple[str, int]]) -> str:
    if not rows:
        return "Пока нет записей."
    return "\n".join(
        ["Общий счет кальянов:"]
        + [f"{i}. {name} — {count}" for i, (name, count) in enumerate(rows, start=1)]
    )


def has_image(message) -> bool:
    if message.photo:
        return True
    if message.document and message.document.mime_type:
        return message.document.mime_type.startswith("image/")
    return False


async def on_hookah_message(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat:
        return
    user = message.from_user
    if not user:
        logger.warning("Skip message without from_user: chat_id=%s message_id=%s", chat.id, message.message_id)
        return

    text = (message.caption or "") + " " + (message.text or "")
    if not has_image(message):
        return

    if not PLUS_ONE_RE.search(text):
        return

    rows, was_counted = add_hookah_and_get_score(chat.id, message.message_id, user)
    if was_counted:
        _ = await message.reply_text(format_score(rows))


async def stats(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message:
        return
    logger.info("Command /stats: chat_id=%s", chat.id)
    rows = get_score(chat.id)
    _ = await message.reply_text(format_score(rows))


async def on_error(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled update error: %s", context.error)


def main() -> None:
    setup_logging()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable")

    logger.info("Starting bot process")
    init_db()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_hookah_message))
    app.add_error_handler(on_error)
    logger.warning(
        "For group chats, disable BotFather privacy mode (/setprivacy -> Disable) or bot will not receive most non-command messages."
    )
    logger.info("Bot polling started")
    app.run_polling()


if __name__ == "__main__":
    main()