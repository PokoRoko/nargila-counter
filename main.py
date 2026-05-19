import os
import re
import sqlite3

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

DB_PATH = os.getenv("DB_PATH", "hookah.db")
PLUS_ONE_RE = re.compile(r"\+1\b")


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
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


def get_user_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    return name or f"user_{user.id}"


def add_hookah_and_get_score(chat_id: int, user) -> list[tuple[str, int]]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO hookah_stats (chat_id, user_id, user_name, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                count = count + 1,
                user_name = excluded.user_name
            """,
            (chat_id, user.id, get_user_name(user)),
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


async def on_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not message.photo or not chat or not user:
        return

    text = (message.caption or "") + " " + (message.text or "")
    if not PLUS_ONE_RE.search(text):
        return

    rows = add_hookah_and_get_score(chat.id, user)
    await message.reply_text(format_score(rows))


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message:
        return
    rows = get_score(chat.id)
    await message.reply_text(format_score(rows))


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable")

    init_db()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo_message))
    app.run_polling()


if __name__ == "__main__":
    main()
