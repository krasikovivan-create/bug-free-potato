from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import texts
from timeutil import format_dt, parse_when

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")
# 1 октября 2026, 12:00 UTC == 14:00 в Берлине (летнее время, UTC+2)
NOW = datetime(2026, 10, 1, 12, 0, 30, tzinfo=UTC)


@pytest.mark.parametrize("text, delta", [
    ("30m", timedelta(minutes=30)),
    ("2h", timedelta(hours=2)),
    ("1d", timedelta(days=1)),
    ("1h30m", timedelta(hours=1, minutes=30)),
    ("1h 30m", timedelta(hours=1, minutes=30)),
    ("2д", timedelta(days=2)),
    ("45М", timedelta(minutes=45)),
    ("  15m  ", timedelta(minutes=15)),
])
def test_relative(text, delta):
    assert parse_when(text, NOW, BERLIN) == NOW + delta


def test_clock_time_later_today():
    # сейчас 14:00:30 по Берлину -> 18:00 сегодня == 16:00 UTC
    assert parse_when("18:00", NOW, BERLIN) == datetime(2026, 10, 1, 16, 0, tzinfo=UTC)


def test_clock_time_already_passed_means_tomorrow():
    assert parse_when("09:00", NOW, BERLIN) == datetime(2026, 10, 2, 7, 0, tzinfo=UTC)


def test_clock_time_equal_to_now_minute_means_tomorrow():
    assert parse_when("14:00", NOW, BERLIN) == datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("word", ["tomorrow", "завтра", "Tomorrow"])
def test_tomorrow(word):
    assert parse_when(f"{word} 09:00", NOW, BERLIN) == datetime(2026, 10, 2, 7, 0, tzinfo=UTC)


def test_iso_date():
    assert parse_when("2026-12-31 20:00", NOW, BERLIN) == datetime(2026, 12, 31, 19, 0, tzinfo=UTC)  # зима, UTC+1


def test_dotted_date_with_and_without_year():
    assert parse_when("31.12 20:00", NOW, BERLIN) == datetime(2026, 12, 31, 19, 0, tzinfo=UTC)
    assert parse_when("31.12.2027 20:00", NOW, BERLIN) == datetime(2027, 12, 31, 19, 0, tzinfo=UTC)


def test_dotted_date_without_year_rolls_to_next_year_when_past():
    assert parse_when("01.01 10:00", NOW, BERLIN) == datetime(2027, 1, 1, 9, 0, tzinfo=UTC)
    assert parse_when("01.10 10:00", NOW, BERLIN) == datetime(2027, 10, 1, 8, 0, tzinfo=UTC)


def test_other_time_zone_changes_result():
    tokyo = ZoneInfo("Asia/Tokyo")  # UTC+9, сейчас 21:00:30
    assert parse_when("22:00", NOW, tokyo) == datetime(2026, 10, 1, 13, 0, tzinfo=UTC)


@pytest.mark.parametrize("text", [
    "", "   ", "soon", "abc", "30", "m", "25:00", "12:99", "2026-02-30 10:00",
    "31.02 10:00", "99999999999m", "-5m", "30 minutes", "tomorrow", "1h 1h",
])
def test_garbage_is_rejected_with_friendly_message(text):
    with pytest.raises(ValueError) as exc:
        parse_when(text, NOW, BERLIN)
    assert str(exc.value) == texts.BAD_TIME


@pytest.mark.parametrize("text", ["0m", "2026-10-01 10:00", "2025-01-01 10:00"])
def test_past_or_now_is_rejected(text):
    with pytest.raises(ValueError) as exc:
        parse_when(text, NOW, BERLIN)
    assert str(exc.value) == texts.TIME_IN_PAST


def test_far_future_overflow_is_handled():
    with pytest.raises(ValueError):
        parse_when("9999-12-31 23:59", NOW, ZoneInfo("America/Los_Angeles"))


def test_format_dt_uses_user_zone():
    assert format_dt(NOW, BERLIN) == "2026-10-01 14:00"
    assert format_dt(NOW, UTC) == "2026-10-01 12:00"
