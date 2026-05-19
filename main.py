import logging
import os
import re
import sqlite3

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

DB_PATH = os.getenv("DB_PATH", "hookah.db")
PLUS_ONE_RE = re.compile(r"\+1\b")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("nargila-counter")


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
    logger.info("SQLite initialized at path=%s", DB_PATH)


def get_user_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    return name or f"user_{user.id}"


def add_hookah_and_get_score(chat_id: int, user) -> list[tuple[str, int]]:
    user_name = get_user_name(user)
    logger.info("Count +1 request: chat_id=%s user_id=%s user=%s", chat_id, user.id, user_name)
    with sqlite3.connect(DB_PATH) as conn:
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
    logger.info("Score table updated: chat_id=%s users=%s", chat_id, len(rows))
    return rows


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


async def on_photo_message(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    logger.info(
        "Incoming photo message: has_message=%s has_chat=%s has_user=%s",
        bool(message),
        bool(chat),
        bool(user),
    )
    if not message or not message.photo or not chat or not user:
        logger.info("Skip message: missing message/photo/chat/user")
        return

    text = (message.caption or "") + " " + (message.text or "")
    if not PLUS_ONE_RE.search(text):
        logger.info("Skip message: no '+1' marker in caption/text")
        return

    rows = add_hookah_and_get_score(chat.id, user)
    logger.info("Replying with updated score: chat_id=%s", chat.id)
    _ = await message.reply_text(format_score(rows))


async def stats(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message:
        logger.info("Skip /stats: no chat or message context")
        return
    logger.info("Handling /stats command: chat_id=%s", chat.id)
    rows = get_score(chat.id)
    _ = await message.reply_text(format_score(rows))


async def on_error(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled update error: %s", context.error)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable")

    logger.info("Starting bot process")
    init_db()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo_message))
    app.add_error_handler(on_error)
    logger.info("Bot polling started")
    app.run_polling()


if __name__ == "__main__":
    main()
    
"""
docker build \
  --build-arg GITHUB_REPO=https://github.com/PokoRoko/nargila-counter.git \
  --build-arg GITHUB_REF=main \
  -t nargila-counter-bot .
  
  docker run -d \
  --name nargila-counter-bot \
  -e TELEGRAM_BOT_TOKEN=8898548415:AAHqNwWn4ywZ0dwzfPT-j40zbCiOX_PQQbk \
  -v nargila-counter-data:/data \
  nargila-counter-bot
"""