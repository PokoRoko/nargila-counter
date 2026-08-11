"""Tests for nargila-counter pure functions and DB layer."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(main, "DB_PATH", str(db_path))
    main.init_db()
    yield db_path


def _make_user(user_id: int, username: str | None = None,
               first_name: str = "", last_name: str = ""):
    return SimpleNamespace(
        id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
    )


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------
class TestWeekStart:
    def test_monday_returns_same_day(self):
        monday = date(2026, 6, 1)  # Monday
        assert main._week_start(monday) == monday

    def test_sunday_returns_previous_monday(self):
        sunday = date(2026, 5, 31)  # Sunday
        assert main._week_start(sunday) == date(2026, 5, 25)

    def test_wednesday(self):
        wed = date(2026, 6, 3)  # Wednesday
        assert main._week_start(wed) == date(2026, 6, 1)


class TestMonthStart:
    def test_mid_month(self):
        assert main._month_start(date(2026, 6, 15)) == date(2026, 6, 1)

    def test_first_day(self):
        assert main._month_start(date(2026, 1, 1)) == date(2026, 1, 1)


class TestPrevWeekRange:
    def test_range_is_7_days(self, monkeypatch):
        monkeypatch.setattr(main, "_week_start", lambda d=None: date(2026, 6, 9))
        start, end = main._prev_week_range()
        assert start == date(2026, 6, 2)
        assert end == date(2026, 6, 9)
        assert (end - start).days == 7


class TestPrevMonthRange:
    def test_january_wraps_to_december(self, monkeypatch):
        monkeypatch.setattr(main, "_month_start", lambda d=None: date(2026, 1, 1))
        start, end = main._prev_month_range()
        assert start == date(2025, 12, 1)
        assert end == date(2026, 1, 1)

    def test_march_to_february(self, monkeypatch):
        monkeypatch.setattr(main, "_month_start", lambda d=None: date(2026, 3, 1))
        start, end = main._prev_month_range()
        assert start == date(2026, 2, 1)
        assert end == date(2026, 3, 1)


class TestPlurals:
    @pytest.mark.parametrize(
        "n,expected",
        [
            (0, "кальянов"),
            (1, "кальян"),
            (2, "кальяна"),
            (3, "кальяна"),
            (4, "кальяна"),
            (5, "кальянов"),
            (11, "кальянов"),
            (12, "кальянов"),
            (21, "кальян"),
            (22, "кальяна"),
            (25, "кальянов"),
            (101, "кальян"),
            (111, "кальянов"),
        ],
    )
    def test_russian_plurals(self, n, expected):
        assert main._plurals_count(n, ("кальян", "кальяна", "кальянов")) == expected


class TestPlaceIcon:
    def test_medals(self):
        assert main._place_icon(0) == "🥇"
        assert main._place_icon(1) == "🥈"
        assert main._place_icon(2) == "🥉"

    def test_after_medals(self):
        assert main._place_icon(3) == "4."
        assert main._place_icon(9) == "10."


class TestFormatScore:
    def test_empty(self):
        assert main.format_score([]) == "Пока нет записей."

    def test_with_rows(self):
        rows = [("@alice", 1), ("@bob", 3)]
        out = main.format_score(rows)
        assert "🥇 @alice — 1" in out
        assert "🥈 @bob — 3" in out

    def test_default_title_has_no_anti_rating_clarifier(self):
        # "меньше — лучше" was removed by design; the default title is now
        # just the leaderboard name. Lock this in so the clarifier doesn't
        # silently creep back.
        out = main.format_score([("@x", 0)])
        assert "меньше" not in out.lower()
        assert out.startswith("📉 Общий счёт кальянов:")

    def test_custom_title_and_empty_msg(self):
        # Default empty message for the total score...
        assert main.format_score([]) == "Пока нет записей."
        # ...but callers (e.g. the photo handler) can override title + empty.
        out = main.format_score(
            [],
            title="📉 Эта неделя (4–10 авг):",
            empty_msg="На этой неделе пока нет записей.",
        )
        assert out == "На этой неделе пока нет записей."

    def test_custom_title_appears_in_output(self):
        out = main.format_score(
            [("@alice", 1)],
            title="📉 Эта неделя (4–10 авг):",
        )
        assert out.startswith("📉 Эта неделя (4–10 авг)")
        assert "🥇 @alice — 1" in out

    def test_highlight_user_wraps_only_that_user_in_bold(self):
        """highlight_user must wrap exactly that one row in <b>...</b>;
        everyone else stays plain (HTML output)."""
        out = main.format_score(
            [("@alice", 1), ("@bob", 3)],
            highlight_user="@alice",
        )
        assert "🥇 <b>@alice</b> — 1" in out
        # bob is not highlighted
        assert "🥈 @bob — 3" in out
        assert "<b>@bob</b>" not in out

    def test_highlight_user_none_means_no_bold(self):
        """Without highlight_user there must be no <b> tags at all."""
        out = main.format_score([("@alice", 1)])
        assert "<b>" not in out

    def test_deltas_append_plus_n_only_for_positive(self):
        """deltas={name: n} appends '(+n)' to that row; zero/negative are omitted."""
        out = main.format_score(
            [("@alice", 5), ("@bob", 1)],
            deltas={"@alice": 2, "@bob": 0},
        )
        assert "🥇 @alice — 5 (+2)" in out
        # bob's delta is 0 -> no suffix
        assert "🥈 @bob — 1" in out
        assert "(+0)" not in out

    def test_html_escaping_in_names(self):
        """Usernames containing HTML metacharacters must be escaped so HTML
        parse_mode doesn't break and the literal name is shown."""
        # A user named (hypothetically) "<x>" with no @ handle path
        out = main.format_score([("<script>", 1)])
        assert "<b>" not in out.split("\n", 1)[1]  # only our own tags allowed
        assert "&lt;script&gt;" in out


class TestFormatDateRange:
    """_format_date_range renders inclusive day ranges in Russian."""

    def test_same_month_uses_day_dash_day(self):
        aug4 = date(2026, 8, 4)   # Tuesday
        aug10 = date(2026, 8, 10)
        assert main._format_date_range(aug4, aug10) == "4–10 авг"

    def test_crosses_month_boundary(self):
        jul28 = date(2026, 7, 28)
        aug3 = date(2026, 8, 3)
        assert main._format_date_range(jul28, aug3) == "28 июл – 3 авг"

    def test_single_day_same_month(self):
        # When start == end (single-day range), the same-month branch still
        # produces a valid label like "1–1 авг".
        first = date(2026, 8, 1)
        assert main._format_date_range(first, first) == "1–1 авг"

    def test_month_name_picked_correctly_per_month(self):
        # Sanity: a January range and a December range pick the right label.
        assert main._format_date_range(date(2026, 1, 5), date(2026, 1, 11)) == "5–11 янв"
        assert main._format_date_range(date(2026, 12, 7), date(2026, 12, 13)) == "7–13 дек"


class TestPlusOneRegex:
    @pytest.mark.parametrize(
        "text,matches",
        [
            ("+1", True),
            ("+ 1", True),
            ("+1 сегодня", True),
            ("сегодня +1", True),
            ("+1️⃣", True),  # "+1" prefix is still present before the keycap
            ("плюс один", False),
            ("+2", False),
            ("11", False),
            ("", False),
        ],
    )
    def test_regex(self, text, matches):
        assert bool(main.PLUS_ONE_RE.search(text)) is matches


class TestGetUserName:
    def test_username_preferred(self):
        u = _make_user(1, username="alice", first_name="Алиса")
        assert main.get_user_name(u) == "@alice"

    def test_first_last_name(self):
        u = _make_user(2, first_name="Иван", last_name="Петров")
        assert main.get_user_name(u) == "Иван Петров"

    def test_fallback_to_id(self):
        u = _make_user(3)
        assert main.get_user_name(u) == "user_3"


# ---------------------------------------------------------------------------
# Database layer tests
# ---------------------------------------------------------------------------
class TestDatabase:
    def test_init_creates_tables(self, temp_db):
        with sqlite3.connect(temp_db) as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        for expected in {"hookah_stats", "processed_messages", "hookah_log",
                         "period_winners"}:
            assert expected in tables

    def test_add_hookah_increments(self, temp_db):
        user = _make_user(100, username="alice")
        rows, was_counted = main.add_hookah_and_get_score(1, 10, user)
        assert was_counted is True
        assert rows == [("@alice", 1)]

        rows, was_counted = main.add_hookah_and_get_score(1, 11, user)
        assert was_counted is True
        assert rows == [("@alice", 2)]

    def test_duplicate_message_ignored(self, temp_db):
        user = _make_user(100, username="alice")
        main.add_hookah_and_get_score(1, 10, user)
        # Same message_id again -> ignored
        rows, was_counted = main.add_hookah_and_get_score(1, 10, user)
        assert was_counted is False
        assert rows == [("@alice", 1)]

    def test_anti_rating_order(self, temp_db):
        """Fewer hookahs -> higher place (first row)."""
        alice = _make_user(1, username="alice")  # will have 3
        bob = _make_user(2, username="bob")      # will have 1
        for mid in (10, 11, 12):
            main.add_hookah_and_get_score(1, mid, alice)
        main.add_hookah_and_get_score(1, 20, bob)
        rows = main.get_score(1)
        # bob (1) should come before alice (3) — anti-rating
        assert rows[0] == ("@bob", 1)
        assert rows[1] == ("@alice", 3)

    def test_username_change_updates_stats(self, temp_db):
        """If a user changes their @username, the stored name should update."""
        u1 = _make_user(1, username="old_name")
        main.add_hookah_and_get_score(1, 10, u1)
        u2 = _make_user(1, username="new_name")  # same id, new username
        main.add_hookah_and_get_score(1, 11, u2)
        rows = main.get_score(1)
        assert rows == [("@new_name", 2)]

    def test_determine_period_winner_picks_minimum(self, temp_db):
        alice = _make_user(1, username="alice")
        bob = _make_user(2, username="bob")
        # alice: 2 hookahs, bob: 1 hookah in the same period
        for mid in (10, 11):
            main.add_hookah_and_get_score(1, mid, alice)
        main.add_hookah_and_get_score(1, 20, bob)

        start, end = main._prev_week_range()
        # Insert log entries within the requested range explicitly so the test
        # is deterministic regardless of "today".
        with sqlite3.connect(temp_db) as conn:
            conn.execute("DELETE FROM hookah_log")
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 1, '@alice', ?)",
                (start.isoformat(),),
            )
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 1, '@alice', ?)",
                (start.isoformat(),),
            )
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 2, '@bob', ?)",
                (start.isoformat(),),
            )
        winner = main.determine_period_winner(1, start, end)
        assert winner is not None
        name, uid, count = winner
        assert (name, uid, count) == ("@bob", 2, 1)

    def test_save_period_winner_idempotent(self, temp_db):
        main.save_period_winner(1, "week", "2026-05-25", 1, "@alice", 2)
        # Second insert with same key must be ignored, not raise.
        main.save_period_winner(1, "week", "2026-05-25", 2, "@bob", 5)
        wins = main.get_win_counts(1)
        # Only the first one should be recorded
        assert wins == {1: {"week": 1}}

    def test_get_period_score_week_excludes_old_entries(self, temp_db):
        """get_period_score('week') must only count the current week,
        not the all-time history. This is the guarantee the photo handler
        relies on when showing 'score for this week'."""
        alice = _make_user(1, username="alice")
        bob = _make_user(2, username="bob")
        # Two real +1's this week (logged under created_at=now by default)
        main.add_hookah_and_get_score(1, 10, alice)
        main.add_hookah_and_get_score(1, 11, alice)
        main.add_hookah_and_get_score(1, 20, bob)
        # Inject an OLD log entry (2 weeks ago) that must be ignored.
        week_start = main._week_start()
        two_weeks_ago = (week_start - timedelta(weeks=2)).isoformat()
        with sqlite3.connect(temp_db) as conn:
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 1, '@alice', ?)",
                (two_weeks_ago,),
            )
        rows = dict(main.get_period_score(1, "week"))
        # Alice: 2 this week (old one excluded), bob: 1 this week.
        assert rows == {"@alice": 2, "@bob": 1}

    def test_get_period_score_week_is_anti_rating_order(self, temp_db):
        alice = _make_user(1, username="alice")
        bob = _make_user(2, username="bob")
        for mid in (10, 11, 12):
            main.add_hookah_and_get_score(1, mid, alice)  # 3
        main.add_hookah_and_get_score(1, 20, bob)  # 1
        rows = main.get_period_score(1, "week")
        # bob (1) above alice (3) — fewer hookahs ranks first
        assert rows[0] == ("@bob", 1)
        assert rows[1] == ("@alice", 3)

    def test_wal_mode_enabled(self, temp_db):
        with sqlite3.connect(temp_db) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


# ---------------------------------------------------------------------------
# Timezone handling
# ---------------------------------------------------------------------------
class TestTimezone:
    def test_valid_timezone(self, monkeypatch):
        monkeypatch.setattr(main, "TIMEZONE_STR", "Europe/Belgrade")
        # build_application is heavy (creates Application); we only verify that
        # ZoneInfo accepts the configured value without an exception.
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(main.TIMEZONE_STR)
        assert str(tz) == "Europe/Belgrade"

    def test_invalid_timezone_falls_back(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        with pytest.raises(ZoneInfoNotFoundError):
            ZoneInfo("Not/A/Real/Zone")


# ---------------------------------------------------------------------------
# Handler-level integration tests
#
# These exercise the bot's user-facing behaviour (the 4 rules the owner
# documented) end to end against a temp DB: photo +1 replies with the weekly
# score, /stats shows the all-time total + win counts, and the weekly/monthly
# winner announcement picks whoever smoked the least in the previous period.
#
# Coroutine handlers are run synchronously via asyncio.run — no need for
# pytest-asyncio. Handlers only duck-type attributes on Update/Message/Chat,
# so SimpleNamespace doubles are enough.
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402


def _run(coro):
    """Run an async handler coroutine to completion in a fresh event loop."""
    return asyncio.run(coro)


def _make_msg(
    *,
    chat_id: int,
    message_id: int,
    user,
    text: str = "",
    caption: str = "",
    photo=None,
    document=None,
):
    """Build a minimal Update/Message/Chat triple that handlers duck-type.

    `photo=None` means 'no photo'. Setting photo=True makes `bool(photo)` truthy
    (handlers check `if message.photo:`). We don't need real PhotoSize objects.
    """
    photo_obj = None if photo is None else photo  # truthy sentinel is enough
    chat = SimpleNamespace(id=chat_id)
    replies: list[str] = []

    async def reply_text(text, **_kwargs):
        replies.append(text)
        return None

    message = SimpleNamespace(
        message_id=message_id,
        from_user=user,
        chat=chat,
        text=text,
        caption=caption,
        photo=photo_obj,
        document=document,
        reply_text=reply_text,
    )
    update = SimpleNamespace(effective_message=message, effective_chat=chat)
    return update, message, replies


def _make_context(*, args=None, bot=None):
    """Build a minimal ContextTypes.DEFAULT_TYPE double."""
    sent: list[tuple[int, str]] = []

    class _Bot:
        async def send_message(self, chat_id, text, **_kwargs):
            sent.append((chat_id, text))
            return None

    return SimpleNamespace(args=args or [], bot=bot or _Bot()), sent


class TestPhotoHandlerShowsWeeklyScore:
    """Rule 3: при отправке фото показывается статистика только за эту неделю."""

    def test_photo_reply_uses_weekly_title(self, temp_db):
        user = _make_user(100, username="alice")
        update, _msg, replies = _make_msg(
            chat_id=1, message_id=10, user=user, photo=True, caption="+1"
        )
        context, _ = _make_context()
        _run(main.on_hookah_message(update, context))

        assert len(replies) == 1
        reply = replies[0]
        # Weekly title, NOT the all-time "Общий счёт" one. New wording uses
        # nominative "Эта неделя" plus an inclusive date range in parentheses.
        assert "эта неделя" in reply.lower()
        assert "общий счёт" not in reply.lower()
        # Author is highlighted in HTML bold; everyone else stays plain.
        assert "🥇 <b>@alice</b> — 1" in reply

    def test_photo_reply_excludes_old_weeks(self, temp_db):
        """A +1 from 2 weeks ago must not appear in the weekly reply."""
        alice = _make_user(1, username="alice")
        bob = _make_user(2, username="bob")

        # Bob: 3 this week (current created_at)
        for mid in (10, 11, 12):
            update, _m, _r = _make_msg(
                chat_id=1, message_id=mid, user=bob, photo=True, caption="+1"
            )
            context, _ = _make_context()
            _run(main.on_hookah_message(update, context))

        # Alice: 1 this week
        update, _m, replies = _make_msg(
            chat_id=1, message_id=20, user=alice, photo=True, caption="+1"
        )
        context, _ = _make_context()
        _run(main.on_hookah_message(update, context))

        # Inject an OLD log entry (2 weeks ago) that must be ignored
        two_weeks_ago = (main._week_start() - timedelta(weeks=2)).isoformat()
        with sqlite3.connect(temp_db) as conn:
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 1, '@alice', ?)",
                (two_weeks_ago,),
            )

        # Trigger one more +1 for alice and inspect the weekly reply
        update, _m, replies = _make_msg(
            chat_id=1, message_id=21, user=alice, photo=True, caption="+1"
        )
        context, _ = _make_context()
        _run(main.on_hookah_message(update, context))

        reply = replies[0]
        # This week: alice 2 (old excluded), bob 3. Alice ranks first (anti-rating).
        # Alice is the highlighted author (last +1) -> bold; bob stays plain.
        assert "🥇 <b>@alice</b> — 2" in reply
        assert "🥈 @bob — 3" in reply
        # And it must NOT show the cumulative total (alice 2, not alice 3+)
        assert "🥉" not in reply  # only 2 users this week

    def test_photo_without_plus_one_is_ignored(self, temp_db):
        """A photo without +1 in caption/text must not trigger any reply."""
        user = _make_user(100, username="alice")
        update, _msg, replies = _make_msg(
            chat_id=1, message_id=10, user=user, photo=True, caption="nice photo"
        )
        context, _ = _make_context()
        _run(main.on_hookah_message(update, context))
        assert replies == []

    def test_text_without_photo_is_ignored(self, temp_db):
        """A plain +1 text without a photo must not trigger any reply."""
        user = _make_user(100, username="alice")
        update, _msg, replies = _make_msg(
            chat_id=1, message_id=10, user=user, photo=None, text="+1"
        )
        context, _ = _make_context()
        _run(main.on_hookah_message(update, context))
        assert replies == []

    def test_duplicate_plus_one_does_not_reply_again(self, temp_db):
        """Re-sending the same +1 photo (same message_id) is ignored."""
        user = _make_user(100, username="alice")
        first = _make_msg(
            chat_id=1, message_id=10, user=user, photo=True, caption="+1"
        )
        second = _make_msg(
            chat_id=1, message_id=10, user=user, photo=True, caption="+1"
        )
        context, _ = _make_context()
        _run(main.on_hookah_message(first[0], context))   # 1 reply
        _run(main.on_hookah_message(second[0], context))  # no extra reply
        assert len(first[2]) == 1
        assert second[2] == []

    def test_photo_reply_has_date_range_in_title(self, temp_db):
        """The weekly title carries an inclusive date range so chatters know
        exactly which week the score covers."""
        user = _make_user(100, username="alice")
        update, _msg, replies = _make_msg(
            chat_id=1, message_id=10, user=user, photo=True, caption="+1"
        )
        context, _ = _make_context()
        _run(main.on_hookah_message(update, context))

        reply = replies[0]
        week_start = main._week_start()
        week_end = week_start + timedelta(days=6)
        expected_range = main._format_date_range(week_start, week_end)
        assert f"({expected_range})" in reply

    def test_photo_reply_is_html_with_bold_author(self, temp_db):
        """The reply must be valid HTML (so parse_mode=HTML is set on the
        reply_text call) and wrap the +1 author in <b>...</b>."""
        user = _make_user(100, username="alice")
        # Capture kwargs passed to reply_text to assert parse_mode=HTML.
        captured: dict = {}

        async def _spy_reply(text, **kwargs):
            captured["text"] = text
            captured["kwargs"] = kwargs
            return None

        update = SimpleNamespace(
            effective_message=SimpleNamespace(
                message_id=10, from_user=user, chat=SimpleNamespace(id=1),
                text="", caption="+1", photo=["x"], document=None,
                reply_text=_spy_reply,
            ),
            effective_chat=SimpleNamespace(id=1),
        )
        context, _ = _make_context()
        _run(main.on_hookah_message(update, context))

        assert captured["kwargs"].get("parse_mode") == "HTML"
        assert "<b>@alice</b>" in captured["text"]


class TestStatsCommand:
    """Rule 4: /stats -> общий счёт + победы по каждому пользователю."""

    def test_stats_shows_all_time_total(self, temp_db):
        alice = _make_user(1, username="alice")
        bob = _make_user(2, username="bob")
        for mid in (10, 11, 12):
            main.add_hookah_and_get_score(1, mid, alice)  # 3
        main.add_hookah_and_get_score(1, 20, bob)        # 1

        update, _m, replies = _make_msg(chat_id=1, message_id=99, user=alice)
        context, _ = _make_context(args=[])
        _run(main.stats(update, context))

        assert len(replies) == 1
        out = replies[0]
        # All-time title and both users
        assert "общий счёт" in out.lower()
        assert "🥇 @bob — 1" in out   # anti-rating: bob (1) above alice (3)
        assert "🥈 @alice — 3" in out

    def test_stats_with_no_history_says_nothing(self, temp_db):
        user = _make_user(1, username="alice")
        update, _m, replies = _make_msg(chat_id=1, message_id=1, user=user)
        context, _ = _make_context(args=[])
        _run(main.stats(update, context))
        assert replies == ["Пока нет записей."]

    def test_stats_shows_win_counts_section_when_present(self, temp_db):
        alice = _make_user(1, username="alice")
        bob = _make_user(2, username="bob")
        main.add_hookah_and_get_score(1, 10, alice)  # 1
        main.add_hookah_and_get_score(1, 20, bob)    # 1

        # Seed a recorded weekly win for alice
        main.save_period_winner(1, "week", main._week_start().isoformat(), 1, "@alice", 1)
        # And a monthly win for bob
        main.save_period_winner(1, "month", main._month_start().isoformat(), 2, "@bob", 1)

        update, _m, replies = _make_msg(chat_id=1, message_id=99, user=alice)
        context, _ = _make_context(args=[])
        _run(main.stats(update, context))

        out = replies[0]
        assert "Победы" in out
        # Compact wins format: one entry per winning user joined by ' | ',
        # showing both week and month counts separated by '·' (zeros included),
        # sorted by total wins desc. e.g. "@alice: 1 нед · 0 мес | @bob: 0 нед · 1 мес".
        assert "@alice: 1 нед · 0 мес" in out
        assert "@bob: 0 нед · 1 мес" in out
        # Both winners fit on one line, separated by a pipe.
        assert " | " in out.split("Победы")[1]

    def test_stats_wins_block_sorted_by_total_desc(self, temp_db):
        """The compact wins block lists users by total wins (week + month)
        descending, so the most-decorated user appears first."""
        alice = _make_user(1, username="alice")
        bob = _make_user(2, username="bob")

        main.add_hookah_and_get_score(1, 10, alice)
        main.add_hookah_and_get_score(1, 20, bob)

        # Alice: 3 weekly wins + 1 monthly. Bob: 1 monthly (different period_start
        # so it doesn't collide with alice's row on the period_winners PK).
        for start in ("2026-01-05", "2026-01-12", "2026-01-19"):
            main.save_period_winner(1, "week", start, 1, "@alice", 1)
        main.save_period_winner(1, "month", "2026-01-01", 1, "@alice", 1)
        main.save_period_winner(1, "month", "2026-02-01", 2, "@bob", 1)

        update, _m, replies = _make_msg(chat_id=1, message_id=99, user=alice)
        context, _ = _make_context(args=[])
        _run(main.stats(update, context))

        wins_line = replies[0].split("Победы")[1].strip()
        # Alice (4 total) must come before bob (1 total).
        assert wins_line.index("@alice") < wins_line.index("@bob")
        # And the compact format is used.
        assert "3 нед · 1 мес" in wins_line
        assert "0 нед · 1 мес" in wins_line

    def test_stats_omits_win_section_when_no_winners(self, temp_db):
        """No period_winners rows -> no 'Победы' section at all."""
        alice = _make_user(1, username="alice")
        main.add_hookah_and_get_score(1, 10, alice)

        update, _m, replies = _make_msg(chat_id=1, message_id=99, user=alice)
        context, _ = _make_context(args=[])
        _run(main.stats(update, context))

        assert "Победы" not in replies[0]

    def test_stats_week_arg_shows_weekly(self, temp_db):
        alice = _make_user(1, username="alice")
        main.add_hookah_and_get_score(1, 10, alice)  # 1 this week

        update, _m, replies = _make_msg(chat_id=1, message_id=99, user=alice)
        context, _ = _make_context(args=["week"])
        _run(main.stats(update, context))

        # New wording uses nominative "текущая неделя" + an inclusive date range.
        assert "текущая неделя" in replies[0].lower()
        assert "🥇 @alice — 1" in replies[0]

    def test_stats_month_arg_shows_monthly(self, temp_db):
        alice = _make_user(1, username="alice")
        main.add_hookah_and_get_score(1, 10, alice)

        update, _m, replies = _make_msg(chat_id=1, message_id=99, user=alice)
        context, _ = _make_context(args=["month"])
        _run(main.stats(update, context))

        assert "текущий месяц" in replies[0].lower()

    @pytest.mark.parametrize("alias", ["w", "week", "неделя", "неделю", "нед"])
    def test_week_aliases_accepted(self, temp_db, alias):
        alice = _make_user(1, username="alice")
        main.add_hookah_and_get_score(1, 10, alice)  # seed a record this week
        update, _m, replies = _make_msg(chat_id=1, message_id=99, user=alice)
        context, _ = _make_context(args=[alias])
        _run(main.stats(update, context))
        assert "текущая неделя" in replies[0].lower()

    @pytest.mark.parametrize("alias", ["m", "month", "месяц", "мес"])
    def test_month_aliases_accepted(self, temp_db, alias):
        alice = _make_user(1, username="alice")
        main.add_hookah_and_get_score(1, 10, alice)  # seed a record this month
        update, _m, replies = _make_msg(chat_id=1, message_id=99, user=alice)
        context, _ = _make_context(args=[alias])
        _run(main.stats(update, context))
        assert "текущий месяц" in replies[0].lower()


class TestWeeklyAndMonthlyPeriodStart:
    """Rule 1: отсчёт идёт с понедельника и первого числа месяца."""

    @pytest.mark.parametrize(
        "today,expected_monday",
        [
            (date(2026, 8, 3), date(2026, 8, 3)),   # Monday -> same
            (date(2026, 8, 4), date(2026, 8, 3)),   # Tuesday
            (date(2026, 8, 6), date(2026, 8, 3)),   # Thursday (today)
            (date(2026, 8, 9), date(2026, 8, 3)),   # Sunday -> back to Mon
            (date(2026, 8, 10), date(2026, 8, 10)), # next Monday
        ],
    )
    def test_week_start_is_monday(self, today, expected_monday):
        assert main._week_start(today) == expected_monday
        assert expected_monday.weekday() == 0  # always Monday

    @pytest.mark.parametrize(
        "today,expected_first",
        [
            (date(2026, 1, 1), date(2026, 1, 1)),
            (date(2026, 1, 15), date(2026, 1, 1)),
            (date(2026, 1, 31), date(2026, 1, 1)),
            (date(2026, 2, 28), date(2026, 2, 1)),
            (date(2026, 12, 31), date(2026, 12, 1)),
        ],
    )
    def test_month_start_is_first(self, today, expected_first):
        assert main._month_start(today) == expected_first
        assert expected_first.day == 1

    def test_get_period_score_week_uses_monday_boundary(self, temp_db):
        """A log entry on Sunday just before this Monday must be EXCLUDED."""
        alice = _make_user(1, username="alice")
        main.add_hookah_and_get_score(1, 10, alice)  # this week

        # Last Sunday (just before this week's Monday)
        this_monday = main._week_start()
        last_sunday = this_monday - timedelta(days=1)
        with sqlite3.connect(temp_db) as conn:
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 2, '@bob', ?)",
                (last_sunday.isoformat(),),
            )

        rows = dict(main.get_period_score(1, "week"))
        # Bob's Sunday entry is in the previous week, must not count here.
        assert rows == {"@alice": 1}

    def test_get_period_score_month_uses_first_of_month_boundary(self, temp_db):
        """A log entry on the last day of the previous month is excluded."""
        alice = _make_user(1, username="alice")
        main.add_hookah_and_get_score(1, 10, alice)  # this month

        # Last day of previous month
        first_of_this_month = main._month_start()
        last_day_prev = first_of_this_month - timedelta(days=1)
        with sqlite3.connect(temp_db) as conn:
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 2, '@bob', ?)",
                (last_day_prev.isoformat(),),
            )

        rows = dict(main.get_period_score(1, "month"))
        assert rows == {"@alice": 1}


class TestPeriodWinnerAnnouncement:
    """Rule 2: в конце каждой недели и месяца отчёт с победителем (меньше всех)."""

    def test_determine_winner_picks_fewest(self, temp_db):
        alice = _make_user(1, username="alice")
        bob = _make_user(2, username="bob")
        carol = _make_user(3, username="carol")
        # alice 3, bob 1, carol 2 -> bob wins (fewest)
        for mid in (10, 11, 12):
            main.add_hookah_and_get_score(1, mid, alice)
        main.add_hookah_and_get_score(1, 20, bob)
        for mid in (30, 31):
            main.add_hookah_and_get_score(1, mid, carol)

        start, end = main._prev_week_range()
        # Make sure all entries land inside the previous week window deterministically
        with sqlite3.connect(temp_db) as conn:
            conn.execute("DELETE FROM hookah_log")
            for uid, name, cnt in [(1, "@alice", 3), (2, "@bob", 1), (3, "@carol", 2)]:
                for _ in range(cnt):
                    conn.execute(
                        "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                        "VALUES (1, ?, ?, ?)",
                        (uid, name, start.isoformat()),
                    )

        name, uid, count = main.determine_period_winner(1, start, end)
        assert (name, uid, count) == ("@bob", 2, 1)

    def test_announce_weekly_sends_message_and_records_winner(self, temp_db):
        """announce_weekly_winner messages the chat and persists the winner."""
        alice = _make_user(1, username="alice")
        bob = _make_user(2, username="bob")
        main.add_hookah_and_get_score(1, 10, alice)  # 1
        main.add_hookah_and_get_score(1, 20, bob)
        main.add_hookah_and_get_score(1, 21, bob)    # 2

        # Place the entries in the previous week so the job picks them up.
        start, end = main._prev_week_range()
        with sqlite3.connect(temp_db) as conn:
            conn.execute("DELETE FROM hookah_log")
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 1, '@alice', ?)", (start.isoformat(),)
            )
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 2, '@bob', ?)", (start.isoformat(),)
            )
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 2, '@bob', ?)", (start.isoformat(),)
            )

        context, sent = _make_context()
        _run(main.announce_weekly_winner(context))

        # One message to the chat
        assert len(sent) == 1
        chat_id, text = sent[0]
        assert chat_id == 1
        assert "прошлой недели" in text.lower()
        assert "@alice" in text  # alice (1) is the winner — fewer hookahs
        assert "меньше" in text.lower() or "так держать" in text.lower()

        # Winner persisted for the period start -> idempotent re-run sends nothing
        wins = main.get_win_counts(1)
        assert wins == {1: {"week": 1}}

        # Idempotent: running again must NOT send a second message
        context2, sent2 = _make_context()
        _run(main.announce_weekly_winner(context2))
        assert sent2 == []

    def test_announce_monthly_sends_message_and_records_winner(self, temp_db):
        alice = _make_user(1, username="alice")
        bob = _make_user(2, username="bob")
        main.add_hookah_and_get_score(1, 10, alice)
        main.add_hookah_and_get_score(1, 20, bob)
        main.add_hookah_and_get_score(1, 21, bob)  # bob 2, alice 1

        start, end = main._prev_month_range()
        with sqlite3.connect(temp_db) as conn:
            conn.execute("DELETE FROM hookah_log")
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 1, '@alice', ?)", (start.isoformat(),)
            )
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 2, '@bob', ?)", (start.isoformat(),)
            )
            conn.execute(
                "INSERT INTO hookah_log (chat_id, user_id, user_name, created_at) "
                "VALUES (1, 2, '@bob', ?)", (start.isoformat(),)
            )

        context, sent = _make_context()
        _run(main.announce_monthly_winner(context))

        assert len(sent) == 1
        _chat_id, text = sent[0]
        assert "прошлого месяца" in text.lower()
        assert "@alice" in text
        assert main.get_win_counts(1) == {1: {"month": 1}}

    def test_announce_skips_chats_with_no_activity(self, temp_db):
        """A chat registered in hookah_stats but with no logs this period
        receives no winner announcement."""
        # chat 1 has stats but the previous-period window is empty for it
        alice = _make_user(1, username="alice")
        main.add_hookah_and_get_score(1, 10, alice)  # this week, not prev

        context, sent = _make_context()
        _run(main.announce_weekly_winner(context))
        # No previous-week entries -> determine_period_winner returns None -> skip
        assert sent == []


class TestSchedulerGuards:
    """The daily job only fires weekly on Mondays, monthly on the 1st."""

    def test_is_monday_true_on_monday(self, monkeypatch):
        monkeypatch.setattr(main, "date", _FakeDate(date(2026, 8, 3)))  # Mon
        assert main._is_monday() is True
        assert main._is_first_of_month() is False

    def test_is_first_of_month_true_on_first(self, monkeypatch):
        monkeypatch.setattr(main, "date", _FakeDate(date(2026, 8, 1)))
        assert main._is_first_of_month() is True
        assert main._is_monday() is False

    def test_neither_on_mid_month_weekday(self, monkeypatch):
        monkeypatch.setattr(main, "date", _FakeDate(date(2026, 8, 6)))  # Thu
        assert main._is_monday() is False
        assert main._is_first_of_month() is False


class _FakeDate:
    """Minimal date stand-in so _is_monday/_is_first_of_month read a fixed today.

    We only implement what `date.today()` and the two guards touch.
    """

    def __init__(self, today):
        self._today = today

    def today(self):
        return self._today
