from datetime import datetime, timedelta, timezone

import pytest

from db import Database

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_numbers_are_per_user(db):
    assert db.add_task(1, "a") == 1
    assert db.add_task(1, "b") == 2
    assert db.add_task(2, "c") == 1  # у второго пользователя своя нумерация


def test_numbers_are_never_reused(db):
    db.add_task(1, "a")
    db.add_task(1, "b")
    assert db.delete_task(1, 2)
    assert db.add_task(1, "c") == 3


def test_users_cannot_touch_each_others_tasks(db):
    db.add_task(1, "secret")
    assert db.get_task(2, 1) is None
    assert not db.mark_done(2, 1)
    assert not db.delete_task(2, 1)
    assert not db.set_reminder(2, 1, NOW)
    assert db.list_tasks(2) == []
    assert db.get_task(1, 1).text == "secret"


def test_done_and_listing(db):
    db.add_task(1, "a")
    db.add_task(1, "b")
    assert db.mark_done(1, 1, now=NOW)
    assert not db.mark_done(1, 1)  # повторно нельзя
    assert [t.num for t in db.list_tasks(1)] == [2]
    assert [t.num for t in db.list_tasks(1, include_done=True)] == [2, 1]  # открытые первыми
    assert db.get_task(1, 1).done_at == NOW


def test_unicode_and_quotes_are_stored_as_is(db):
    text = "Купить молоко; DROP TABLE tasks; -- 'x' \"y\" 🥛"
    num = db.add_task(1, text)
    assert db.get_task(1, num).text == text


def test_timezone_default_and_override(db):
    assert db.get_zone(1).key == "UTC"
    db.set_tz(1, "Europe/Berlin")
    assert db.get_zone(1).key == "Europe/Berlin"
    assert db.get_zone(2).key == "UTC"


def test_due_reminders_only_returns_due_open_unsent(db):
    for text in "abcd":
        db.add_task(1, text)
    db.set_reminder(1, 1, NOW - timedelta(minutes=1))   # пора
    db.set_reminder(1, 2, NOW + timedelta(minutes=1))   # ещё нет
    db.set_reminder(1, 3, NOW - timedelta(minutes=5))   # пора, но задача выполнена
    db.mark_done(1, 3)
    # задача 4 — без напоминания
    assert [t.num for t in db.due_reminders(NOW)] == [1]


def test_reminder_compares_correctly_across_time_zones(db):
    db.add_task(1, "a")
    plus3 = timezone(timedelta(hours=3))
    # 14:00+03:00 == 11:00 UTC, то есть раньше NOW (12:00 UTC)
    db.set_reminder(1, 1, datetime(2026, 10, 1, 14, 0, tzinfo=plus3))
    assert len(db.due_reminders(NOW)) == 1


def test_mark_reminded_stops_repeats(db):
    db.add_task(1, "a")
    db.set_reminder(1, 1, NOW - timedelta(minutes=1))
    (task,) = db.due_reminders(NOW)
    db.mark_reminded(task)
    assert db.due_reminders(NOW) == []


def test_mark_reminded_ignores_rescheduled_reminder(db):
    db.add_task(1, "a")
    db.set_reminder(1, 1, NOW - timedelta(minutes=1))
    (task,) = db.due_reminders(NOW)
    db.set_reminder(1, 1, NOW - timedelta(seconds=30))  # пользователь перенёс, пока мы отправляли
    db.mark_reminded(task)
    assert len(db.due_reminders(NOW)) == 1


def test_set_reminder_resets_sent_flag_and_can_clear(db):
    db.add_task(1, "a")
    db.set_reminder(1, 1, NOW - timedelta(minutes=1))
    db.mark_reminded(db.due_reminders(NOW)[0])
    db.set_reminder(1, 1, NOW - timedelta(seconds=10))
    assert len(db.due_reminders(NOW)) == 1
    assert db.set_reminder(1, 1, None)
    assert db.due_reminders(NOW) == []


def test_data_survives_reopening_the_file(tmp_path):
    path = str(tmp_path / "persist.db")
    Database(path).add_task(1, "keep me")
    assert Database(path).get_task(1, 1).text == "keep me"
