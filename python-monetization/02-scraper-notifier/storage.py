"""SQLite: что мы уже видели и в каком состоянии каждый источник.

Две таблицы:
  seen          — объявления, о которых уже сообщили (или запомнили при первом проходе);
  source_state  — по одной строке на источник: прошёл ли первый проход и сколько сбоев подряд.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from listing import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    source     TEXT NOT NULL,
    item_url   TEXT NOT NULL,
    title      TEXT NOT NULL,
    price      REAL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (source, item_url)
);
CREATE TABLE IF NOT EXISTS source_state (
    source    TEXT PRIMARY KEY,
    baselined INTEGER NOT NULL DEFAULT 0,  -- 1: первый проход уже был
    failures  INTEGER NOT NULL DEFAULT 0   -- сбоев подряд
);
"""


class Storage:
    def __init__(self, path: str):
        self.path = path
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        try:
            with conn:  # успех — commit, ошибка — rollback
                yield conn
        finally:
            conn.close()

    # ---------- объявления ----------

    def filter_new(self, source: str, items: list[Item]) -> list[Item]:
        """Оставляет только те объявления, которых мы ещё не видели."""
        if not items:
            return []
        with self._connect() as conn:
            rows = conn.execute("SELECT item_url FROM seen WHERE source = ?", (source,)).fetchall()
        known = {row[0] for row in rows}
        return [item for item in items if item.url not in known]

    def mark_seen(self, source: str, items: list[Item]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen (source, item_url, title, price, first_seen) VALUES (?, ?, ?, ?, ?)",
                [(source, i.url, i.title, i.price, now) for i in items],
            )

    # ---------- состояние источника ----------

    def _ensure(self, conn, source: str) -> None:
        conn.execute("INSERT OR IGNORE INTO source_state (source) VALUES (?)", (source,))

    def is_baselined(self, source: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT baselined FROM source_state WHERE source = ?", (source,)).fetchone()
        return bool(row and row[0])

    def set_baselined(self, source: str) -> None:
        with self._connect() as conn:
            self._ensure(conn, source)
            conn.execute("UPDATE source_state SET baselined = 1 WHERE source = ?", (source,))

    def record_failure(self, source: str) -> int:
        """Увеличивает счётчик сбоев подряд и возвращает его новое значение."""
        with self._connect() as conn:
            self._ensure(conn, source)
            conn.execute("UPDATE source_state SET failures = failures + 1 WHERE source = ?", (source,))
            return conn.execute("SELECT failures FROM source_state WHERE source = ?", (source,)).fetchone()[0]

    def record_success(self, source: str) -> int:
        """Сбрасывает счётчик сбоев и возвращает, сколько их было подряд до этого."""
        with self._connect() as conn:
            self._ensure(conn, source)
            previous = conn.execute("SELECT failures FROM source_state WHERE source = ?", (source,)).fetchone()[0]
            conn.execute("UPDATE source_state SET failures = 0 WHERE source = ?", (source,))
            return previous
