# Workers, Scheduler & Events

> There is one process (`python main.py` → long-polling loop) and one scheduled
> job. There is no Celery, no Redis, no message broker. See
> [`architecture.md`](architecture.md) §Dependency Graph.

## Scheduler

Backed by the **`python-telegram-bot` job queue** (APScheduler under the hood,
pulled in via the `[job-queue]` extra). Registered in `build_application`:

```python
jq.run_daily(check_period_jobs, time=time(0, 0, tzinfo=tz))
```

- **Trigger**: once per day at **00:00 local time** (`ZoneInfo(TIMEZONE_STR)`,
  default `Europe/Belgrade`; falls back to UTC if the TZ string is invalid).
- **Idempotency guard**: the job's body decides *whether* to announce based on
  the current date — see below.

### `check_period_jobs` (daily router)

```python
if _is_monday():       # date.today().weekday() == 0
    await announce_weekly_winner(context)
if _is_first_of_month():  # date.today().day == 1
    await announce_monthly_winner(context)
```

On the 1st of a month that is also a Monday, **both** fire (in that order).
Both are safe to call repeatedly.

### `announce_weekly_winner` / `announce_monthly_winner`

Thin wrappers that call `_announce_period_winner(context, period_type)` with
the right period title and range:

| period_type | range helper | range meaning | intro text |
|---|---|---|---|
| `"week"` | `_prev_week_range()` | last Monday 00:00 → this Monday 00:00 | `🏆 Итоги прошлой недели!` |
| `"month"` | `_prev_month_range()` | 1st of last month → 1st of this month | `🏆 Итоги прошлого месяца!` |

### `_announce_period_winner` (the actual job body)

For **every chat id** returned by `get_all_chat_ids()`:

1. Skip if a `period_winners` row already exists for
   `(chat_id, period_type, period_start)` — this makes the job **idempotent**
   across restarts/re-runs.
2. Otherwise call `determine_period_winner(chat_id, start, end)`:
   - returns `(user_name, user_id, count)` for the **fewest** hookahs in range,
     tie-broken by `user_name ASC`; or
   - `None` if there were zero log rows in the period → skip this chat silently.
3. `save_period_winner(...)` to persist the award (idempotent `INSERT OR IGNORE`).
4. `context.bot.send_message(chat_id, text)` with a localized message:

   ```
   🏆 Итоги прошлой недели!
   Победитель: @bob — выкурил меньше всех, всего 1 кальян.
   Так держать! 🌿
   ```

   The noun is pluralized via `_plurals_count(count, ("кальян", "кальяна", "кальянов"))`.
5. Exceptions from `send_message` (e.g. bot kicked from the chat) are caught
   and logged — **but the winner is still persisted in step 3**, so we don't
   retry-spam on the next run.

## Events / messaging

There is **no internal event bus**. The closest thing to "events" are:

| "Event" | Producer | Consumer | Payload | Meaning |
|---|---|---|---|---|
| `+1` photo message | Telegram user | `on_hookah_message` | `Update` | "count one hookah for me" |
| Period winner decided | `_announce_period_winner` | the same chat (via `send_message`) + `period_winners` table | `(period_type, user_name, count)` | announcement + durable record |

## Retries

- The scheduler relies on APScheduler's internal misfire handling; no custom
  retry policy. A missed 00:00 run (e.g. downtime) is simply skipped — but
  because winners are computed from `hookah_log` and persisted in
  `period_winners`, a Monday that the bot was offline means that week's winner
  will be announced on the **next** Monday the bot is up (since
  `_prev_week_range` always points to "the week before the current one", not
  "the week we missed"). **Assumption / known limitation**: if the bot is down
  across a month boundary, the missed month may never be announced.
- Handler errors do not crash the loop: `on_error` logs them and the update is
  dropped (no dead-letter queue).
