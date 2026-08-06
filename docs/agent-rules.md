# Rules for AI Agents

> **Read this file before any code change in this repo.** It encodes the
> conventions the codebase actually follows. Project docs override global/user
> rules for architecture, domain logic, and module boundaries (see the Cursor
> rule `.cursor/rules/consult-project-docs.mdc`).

## Layer rules

1. **No SQL outside the repository functions.** Handlers, the scheduler, and the
   entrypoint must call the functions listed in
   [`database.md`](database.md) §Repository Layer (`add_hookah_and_get_score`,
   `get_*`, `save_period_winner`, `determine_period_winner`, `init_db`). Do not
   `conn.execute(...)` from a handler.
2. **The functional core stays pure.** Functions in the "Formatting helpers" /
   period-arithmetic slots (`_week_start`, `_month_start`, `_prev_week_range`,
   `_prev_month_range`, `_plurals_count`, `_place_icon`, `format_score`,
   `has_image`, `PLUS_ONE_RE`) must not import `sqlite3` or `telegram` and must
   not perform I/O. Add new pure logic here.
3. **Telegram SDK types belong only in handlers and `build_application`.**
   Repository functions take/return primitives (`int`, `str`, `tuple`,
   `SimpleNamespace` in tests), never `Update`/`Message`.
4. **The scheduler never writes `hookah_stats`.** It reads `hookah_log` and
   writes only `period_winners`. Do not "fix" an announcement by editing
   someone's lifetime count.
5. **`add_hookah_and_get_score` is the sole writer to `hookah_stats` and
   `hookah_log`.** Keep the dedup+insert+log+read sequence inside a single
   `BEGIN/COMMIT` with `ROLLBACK` on error.

## Behaviour rules (do not regress)

6. **Anti-rating ordering is mandatory.** Every leaderboard query must be
   `ORDER BY count ASC, user_name ASC`. The 🥇 goes to the lightest smoker.
7. **Photo `+1` reply = current week.** After a successful `+1`, reply with
   `get_period_score(chat_id, "week")` under the "Счёт за эту неделю" title —
   not the all-time total. (The all-time total belongs to `/stats` only.)
8. **`/stats` (no args) = all-time + win counts.** The `🏆 Победы` block is
   omitted when there are no winners; otherwise each line shows both week and
   month counts in the format `@name: недель — N, месяцев — M`.
9. **Period boundaries are Monday and the 1st.** Use `_week_start()` /
   `_month_start()`; do not recompute "start of week" inline. Announcements
   always cover the **previous** period (`_prev_week_range` / `_prev_month_range`).
10. **Dedup is by `(chat_id, message_id)`.** Never key dedup on timestamp or
    user id.
11. **Idempotency of announcements.** Always `save_period_winner` before
    `send_message`, and always check `period_winners` first. A re-run of the job
    must never double-post.
12. **Username changes are not a new user.** `user_id` is the identity;
    `user_name` is cosmetic and may be overwritten via the `ON CONFLICT ...
    DO UPDATE SET user_name = excluded.user_name` clause.

## Schema rules

13. **`init_db()` is idempotent and non-destructive.** Always use
    `CREATE TABLE IF NOT EXISTS`. Never `DROP` or `ALTER` in `init_db()` — ship
    a separate one-off migration script for destructive changes.
14. **No foreign keys.** Relations are enforced by application code; don't add
    `REFERENCES` constraints piecemeal without enabling `PRAGMA foreign_keys`
    everywhere.
15. **`created_at` is UTC** (SQLite `CURRENT_TIMESTAMP`). Any new time-bound
    query must compare against UTC, or explicitly convert. (See technical debt
    note in [`architecture.md`](architecture.md).)

## Deploy & ops rules

16. **The Docker data volume is named `nargila-counter-data`** — hardcoded in
    `deploy.sh` as `DATA_VOLUME`. Do **not** derive it from `CONTAINER_NAME`
    (commit `8a35291` fixed a score-reset regression caused by exactly that).
17. **Secrets never go in git.** The bot token lives only in
    `~/nargila-counter/.env` on the server, read via `docker run --env-file`.
    `.env` is gitignored; `.env.example` documents required vars.
18. **The server pulls from `origin/master`.** `deploy.sh` does
    `git fetch + reset --hard origin/master` on the server. Always push before
    deploying; local-only commits will not ship.

## Testing rules

19. **Pure functions get unit tests; handlers get async tests via
    `asyncio.run()`.** Do not add `pytest-asyncio` as a dependency — the
    existing `_run(coro)` helper is sufficient.
20. **Duck-type Telegram objects in tests** with `SimpleNamespace`; do not
    instantiate real `Update`/`Message` graphs.
21. **DB tests use the `temp_db` fixture** (per-test tmp file with `init_db()`).
    Don't share state across tests.
22. **Any new user-facing behaviour must come with a test** that encodes the
    rule from this file. Tests are the regression net for rules 6–12.

## When unsure

- If the docs and the code disagree, **trust the code** for the immediate fix
  and open a follow-up to fix the docs (or vice-versa, but pick one and say so).
- State assumptions explicitly in the PR/commit message. The repo is small
  enough that over-engineering (preemptive splits, abstract base classes,
  config layers) is a bigger risk than duplication.
