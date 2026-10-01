import pytest

from listing import Item
from storage import Storage


@pytest.fixture
def db(tmp_path):
    return Storage(str(tmp_path / "t.db"))


def item(n, source="S"):
    return Item(source=source, url=f"http://x.test/{n}", title=f"Item {n}", price=float(n), price_text=str(n))


def test_filter_new_and_mark_seen(db):
    items = [item(1), item(2), item(3)]
    assert db.filter_new("S", items) == items
    db.mark_seen("S", items[:2])
    assert db.filter_new("S", items) == [items[2]]
    db.mark_seen("S", items[:2])  # повторная запись не падает (INSERT OR IGNORE)
    assert db.filter_new("S", []) == []


def test_sources_are_independent(db):
    db.mark_seen("A", [item(1, "A")])
    assert db.filter_new("B", [item(1, "B")]) == [item(1, "B")]
    assert db.filter_new("A", [item(1, "A")]) == []


def test_same_url_in_two_sources_is_not_mixed_up(db):
    shared = item(1)
    db.mark_seen("A", [shared])
    assert db.filter_new("B", [shared]) == [shared]


def test_baseline_flag(db):
    assert not db.is_baselined("S")
    db.set_baselined("S")
    assert db.is_baselined("S")
    assert not db.is_baselined("other")


def test_failure_counter(db):
    assert db.record_failure("S") == 1
    assert db.record_failure("S") == 2
    assert db.record_failure("other") == 1
    assert db.record_success("S") == 2   # сколько сбоев было подряд
    assert db.record_success("S") == 0
    assert db.record_failure("S") == 1   # счёт пошёл заново


def test_success_on_unknown_source_is_fine(db):
    assert db.record_success("never-seen") == 0


def test_data_survives_reopening(tmp_path):
    path = str(tmp_path / "persist.db")
    first = Storage(path)
    first.mark_seen("S", [item(1)])
    first.set_baselined("S")
    second = Storage(path)
    assert second.filter_new("S", [item(1)]) == []
    assert second.is_baselined("S")


def test_titles_with_quotes_and_unicode_are_stored_safely(db):
    weird = Item("S", "http://x.test/q", "O'Reilly \"Книга\"; DROP TABLE seen; --", 1.0, "1")
    db.mark_seen("S", [weird])
    assert db.filter_new("S", [weird]) == []
