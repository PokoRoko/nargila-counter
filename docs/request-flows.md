# Request Flows

> Step-by-step scenarios. Cross-link: [`api.md`](api.md) for trigger contracts,
> [`database.md`](database.md) for the SQL, [`workers.md`](workers.md) for the
> scheduler.

## A. Photo `+1` ingestion (the main flow)

```mermaid
sequenceDiagram
    participant U as Telegram user
    participant TG as Telegram / PTB
    participant H as on_hookah_message
    participant R as add_hookah_and_get_score
    participant DB as SQLite (WAL)

    U->>TG: photo with caption "+1"
    TG->>H: Update(effective_message, effective_chat)
    H->>H: guard: has_image? +1 regex? from_user?
    H->>R: (chat_id, message_id, user)
    R->>DB: BEGIN
    R->>DB: INSERT OR IGNORE processed_messages(chat_id, message_id)
    alt duplicate (rowcount == 0)
        R->>DB: COMMIT
        R-->>H: (score_rows, False)
        H-->>U: (no reply)
    else new message
        R->>DB: UPSERT hookah_stats (count = count + 1)
        R->>DB: INSERT hookah_log
        R->>DB: SELECT current score
        R->>DB: COMMIT
        R-->>H: (score_rows, True)
        H->>R: get_period_score(chat_id, "week")
        R->>DB: SELECT COUNT(*) FROM hookah_log WHERE created_at >= week_start
        R-->>H: week_rows
        H->>TG: reply_text(format_score(week_rows, title="...эту неделю..."))
        TG-->>U: weekly leaderboard
    end
```

Key points:
- The dedup gate is **inside** the same transaction as the count increment —
  a crash mid-way cannot double-count.
- The **reply shows the current week**, not the all-time total (the all-time
  view is reserved for `/stats`). See commit `0e8098c`.
- If the photo has no `+1`, or no photo, or no `from_user`, the handler returns
  silently — no error, no reply.

## B. `/stats` (all-time + win counts)

```mermaid
sequenceDiagram
    participant U as Telegram user
    participant TG as Telegram / PTB
    participant H as stats
    participant DB as SQLite

    U->>TG: "/stats"
    TG->>H: Update + context.args=[]
    H->>DB: get_score(chat_id)  // lifetime leaderboard
    alt empty
        H-->>U: "Пока нет записей."
    else has rows
        H->>DB: get_win_counts(chat_id)
        H->>DB: get_score_with_user_ids(chat_id)  // to map uid -> name
        H->>H: assemble lines: format_score(total) + optional Победы block
        H-->>U: combined message
    end
```

The `🏆 Победы` block is appended **only** when `get_win_counts` returns
non-empty. Each winner line shows both week and month counts
(`@name: недель — N, месяцев — M`); users with zero wins of both types are
omitted.

## C. `/stats week` / `/stats month`

1. `stats` lowercases and strips `context.args[0]`.
2. Matches against the alias set (`w/week/неделя/неделю/нед` or
   `m/month/месяц/мес`).
3. `get_period_score(chat_id, period_type)` → `SELECT user_name, COUNT(*) FROM
   hookah_log WHERE chat_id=? AND created_at >= <period_start> GROUP BY user_id
   ORDER BY cnt ASC, user_name ASC`.
4. If empty → `За этот период записей пока нет.`
5. Else render rows with `_place_icon` under the period-specific title.

## D. Scheduled weekly/monthly winner announcement

```mermaid
sequenceDiagram
    participant S as APScheduler
    participant J as check_period_jobs
    participant A as announce_weekly_winner / _announce_period_winner
    participant DB as SQLite
    participant TG as context.bot

    Note over S: daily at 00:00 (TZ)
    S->>J: run_daily fires
    alt _is_monday()
        J->>A: announce_weekly_winner(context)
    end
    alt _is_first_of_month()
        J->>A: announce_monthly_winner(context)
    end

    loop for each chat_id in get_all_chat_ids()
        A->>DB: SELECT 1 FROM period_winners WHERE (chat, type, start)
        alt already awarded
            Note over A: skip (idempotent)
        else
            A->>DB: determine_period_winner(chat, prev_start, prev_end)
            alt winner is None (no activity)
                Note over A: skip silently
            else
                A->>DB: save_period_winner(...)  // INSERT OR IGNORE
                A->>TG: send_message(chat_id, "🏆 ...")
                alt send_message fails
                    Note over A: log, keep going (winner already saved)
                end
            end
        end
    end
```

Important: `save_period_winner` runs **before** `send_message`. If the message
fails (bot kicked, chat deleted), the winner is still recorded — so we never
re-announce the same period on the next run.

## E. Container startup

1. `main()` → `setup_logging()` (console + rotating file).
2. `init_db()` — `CREATE TABLE IF NOT EXISTS` for all 4 tables + the log index.
   Safe on existing DB; no data loss on redeploy.
3. `build_application()`:
   - reads `TELEGRAM_BOT_TOKEN` (raises if missing);
   - registers `start`, `help`, `stats`, photo message handler, error handler;
   - registers `run_daily(check_period_jobs, 00:00 TZ)` if `job_queue` exists
     (logs a warning if not — e.g. missing `[job-queue]` extra).
4. `app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)`.
5. The container restart policy is `unless-stopped`, so a crash (e.g. transient
   token error) self-heals.
