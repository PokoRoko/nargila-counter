"""Tests for nargila-counter pure functions and DB layer."""

from __future__ import annotations

import sqlite3
from datetime import date
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

    def test_uses_anti_rating_wording(self):
        out = main.format_score([("@x", 0)])
        assert "меньше" in out.lower()


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
