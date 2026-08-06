# Domain Model

> The "domain" is small and lives entirely inside `main.py` as data plus a few
> pure functions. There are no domain *classes*; entities are rows in SQLite
> (schema in [`database.md`](database.md)). This file describes the conceptual
> model, invariants, and where to add new behaviour.

## Entities & value objects

### Hookah count (`hookah_stats` row) — aggregate root per `(chat_id, user_id)`
- **Represents**: a user's lifetime hookah count in one chat.
- **Lifecycle**: created on first `+1` (count=1); incremented on each new `+1`;
  never decremented (no `/undo`, no `/reset`).
- **Ownership**: repository function `add_hookah_and_get_score` is the **only**
  writer. Reads via `get_score`, `get_score_with_user_ids`.
- **Invariant**: `(chat_id, user_id)` is the primary key. A user changing their
  `@username` updates the stored `user_name` (via `ON CONFLICT ... DO UPDATE
  SET user_name = excluded.user_name`) but keeps the same numeric `user_id` and
  accumulated `count`.

### Hookah log entry (`hookah_log` row) — immutable event
- **Represents**: a single counted `+1` event, used for period scoring.
- **Lifecycle**: append-only; never updated or deleted in production (tests may
  `DELETE` to set up deterministic windows).
- **Ownership**: written only by `add_hookah_and_get_score`. Read by
  `get_period_score`, `determine_period_winner`.
- **Invariant**: each row corresponds 1:1 to a successfully deduped `+1`
  message. `created_at` is SQLite `CURRENT_TIMESTAMP` (**UTC** — see
  [Technical Debt](architecture.md#17-technical-debt)).

### Processed message (`processed_messages` row) — dedup token
- **Represents**: "we have already counted Telegram message `(chat_id,
  message_id)`".
- **Purpose**: idempotency. Re-sent / forwarded duplicates must not inflate
  counts.
- **Lifecycle**: `INSERT OR IGNORE` on every `+1` attempt; never deleted.
- **Invariant**: `PRIMARY KEY (chat_id, message_id)`. If the insert yields
  `rowcount == 0`, the message is a duplicate and the whole transaction skips
  the stats/log writes.

### Period winner (`period_winners` row) — award record
- **Represents**: "in chat `C`, for period `P` starting `S`, user `U` won with
  `count` hookahs".
- **Purpose**: drives the `🏆 Победы` block in `/stats` and prevents
  re-announcing a period.
- **Lifecycle**: written by `save_period_winner` (`INSERT OR IGNORE`) inside
  `_announce_period_winner` **after** sending the announcement message.
  Never updated/deleted.
- **Invariant**: `PRIMARY KEY (chat_id, period_type, period_start)`. Re-running
  the announcement job for the same period is a no-op.

## Value objects (pure)

- **`period_type`**: string literal `"week"` or `"month"`. Used as a discriminator
  in `period_winners` and to pick the start-boundary function
  (`_week_start` vs `_month_start`).
- **Username** (`user_name`): either `"@username"` when Telegram handle exists,
  else `"First Last"`, else `"user_<id>"`. Computed by `get_user_name(user)` —
  **callers must not** format names themselves.
- **Place icon**: `🥇/🥈/🥉` for ranks 0–2, else `"{n}."`. From `_place_icon`.

## Domain rules & invariants

1. **Anti-rating**: fewer hookahs = better rank. Every leaderboard query uses
   `ORDER BY count ASC, user_name ASC`. New code must keep this ordering.
2. **`+1` requires a photo**. Text-only `+1` is ignored. Photo without `+1` is
   ignored. The regex is `\+\s*1\b` (matches `+1`, `+ 1`, `+1️⃣`; rejects `+2`,
   `11`, "плюс один").
3. **Idempotent `+1`**: one Telegram message = at most one increment, enforced
   transactionally via `processed_messages`.
4. **Period boundaries**: week starts Monday (`_week_start = d -
   timedelta(days=d.weekday())`); month starts on the 1st
   (`_month_start = d.replace(day=1)`).
5. **Winner = fewest hookahs in the period**. Ties broken by `user_name ASC`.
6. **Announcements cover the *previous* period**, not the current one:
   `_prev_week_range` / `_prev_month_range`.
7. **Announcements are idempotent**: persisted in `period_winners`, so a job
   re-run (e.g. after a container restart) never double-posts.

## Extension points

- **New `/stats` period** (e.g. "year"): add the alias set to `stats()`, add a
  `_year_start()` core helper, and have `get_period_score` accept `"year"`. No
  schema change needed (it reads `hookah_log` with a start bound).
- **New tracked command** (e.g. `/reset`): add a `CommandHandler` in
  `build_application`, write a thin handler that calls repository functions — do
  not inline SQL in the handler.
- **New announcement cadence** (e.g. daily): add an `announce_daily_winner`
  coroutine mirroring `announce_weekly_winner`, and a guard `_is_daily` called
  from `check_period_jobs`. Use a distinct `period_type` literal so wins don't
  collide.
- **New storage backend**: replace the bodies of the repository functions
  (Section "Repository Layer" in [`database.md`](database.md)). Handlers and
  core need not change if signatures stay stable.

There is no plugin system and no abstract interfaces; "extension" means
following the existing patterns.
