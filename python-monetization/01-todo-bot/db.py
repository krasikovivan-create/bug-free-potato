"""База данных (SQLite). Всё общение с файлом tasks.db — только здесь.

SQLite — это обычный файл, отдельный сервер не нужен: для бота на сотни пользователей этого хватает.
(Когда проект вырастет — тот же интерфейс можно переписать под PostgreSQL, а bot.py не менять.)

Каждый пользователь видит СВОЮ нумерацию задач (1, 2, 3 …), а внутри базы у задачи ещё есть общий id.
Номера не переиспользуются: удалил #5 — следующая задача будет #6, поэтому старые кнопки в чате
никогда не попадут по чужой задаче.
"""
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from timeutil import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id  INTEGER PRIMARY KEY,
    tz       TEXT,                       -- имя часового пояса, напр. 'Europe/Berlin' (NULL = по умолчанию)
    last_num INTEGER NOT NULL DEFAULT 0  -- последний выданный номер задачи
);
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    num        INTEGER NOT NULL,         -- номер, который видит пользователь
    text       TEXT NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,            -- время хранится строкой ISO в UTC
    done_at    TEXT,
    remind_at  TEXT,
    reminded   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, num)
);
CREATE INDEX IF NOT EXISTS idx_tasks_remind ON tasks (remind_at);
"""


@dataclass
class Task:
    id: int
    user_id: int
    num: int
    text: str
    done: bool
    created_at: datetime
    done_at: datetime | None
    remind_at: datetime | None
    reminded: bool


def _iso(dt: datetime) -> str:
    # Всегда UTC и один и тот же формат — тогда строки можно сравнивать как время (remind_at <= ?).
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        user_id=row["user_id"],
        num=row["num"],
        text=row["text"],
        done=bool(row["done"]),
        created_at=_from_iso(row["created_at"]),
        done_at=_from_iso(row["done_at"]),
        remind_at=_from_iso(row["remind_at"]),
        reminded=bool(row["reminded"]),
    )


class Database:
    def __init__(self, path: str, default_tz: str = "UTC"):
        self.path = path
        self.default_zone = ZoneInfo(default_tz)  # упадёт сразу при старте, если имя неверное
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        """Открывает соединение; при успехе — commit, при ошибке — rollback; в конце — закрывает."""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row  # строки можно читать по имени: row["text"]
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ---------- пользователи ----------

    def get_zone(self, user_id: int) -> ZoneInfo:
        with self._connect() as conn:
            row = conn.execute("SELECT tz FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return ZoneInfo(row["tz"]) if row and row["tz"] else self.default_zone

    def set_tz(self, user_id: int, tz_name: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            conn.execute("UPDATE users SET tz = ? WHERE user_id = ?", (tz_name, user_id))

    # ---------- задачи ----------

    def add_task(self, user_id: int, text: str, now: datetime | None = None) -> int:
        """Сохраняет задачу и возвращает её номер (для этого пользователя)."""
        now = now or utcnow()
        # Все четыре запроса идут в одной транзакции: номер не может выдаться дважды.
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            conn.execute("UPDATE users SET last_num = last_num + 1 WHERE user_id = ?", (user_id,))
            num = conn.execute("SELECT last_num FROM users WHERE user_id = ?", (user_id,)).fetchone()[0]
            conn.execute(
                "INSERT INTO tasks (user_id, num, text, created_at) VALUES (?, ?, ?, ?)",
                (user_id, num, text, _iso(now)),
            )
        return num

    def get_task(self, user_id: int, num: int) -> Task | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE user_id = ? AND num = ?", (user_id, num)
            ).fetchone()
        return _to_task(row) if row else None

    def list_tasks(self, user_id: int, include_done: bool = False) -> list[Task]:
        """Задачи пользователя: сначала открытые, потом (если просили) выполненные."""
        query = "SELECT * FROM tasks WHERE user_id = ?"
        if not include_done:
            query += " AND done = 0"
        with self._connect() as conn:
            rows = conn.execute(query + " ORDER BY done, num", (user_id,)).fetchall()
        return [_to_task(r) for r in rows]

    def mark_done(self, user_id: int, num: int, now: datetime | None = None) -> bool:
        """True — если задача нашлась и ещё не была выполнена."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE tasks SET done = 1, done_at = ? WHERE user_id = ? AND num = ? AND done = 0",
                (_iso(now or utcnow()), user_id, num),
            )
            return cur.rowcount > 0

    def delete_task(self, user_id: int, num: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE user_id = ? AND num = ?", (user_id, num))
            return cur.rowcount > 0

    # ---------- напоминания ----------

    def set_reminder(self, user_id: int, num: int, remind_at: datetime | None) -> bool:
        """Ставит напоминание (None — снимает). Работает только для невыполненных задач."""
        value = _iso(remind_at) if remind_at else None
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE tasks SET remind_at = ?, reminded = 0 WHERE user_id = ? AND num = ? AND done = 0",
                (value, user_id, num),
            )
            return cur.rowcount > 0

    def due_reminders(self, now: datetime, limit: int = 100) -> list[Task]:
        """Напоминания, время которых пришло, а отправить мы ещё не успели."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM tasks
                   WHERE done = 0 AND reminded = 0 AND remind_at IS NOT NULL AND remind_at <= ?
                   ORDER BY remind_at LIMIT ?""",
                (_iso(now), limit),
            ).fetchall()
        return [_to_task(r) for r in rows]

    def mark_reminded(self, task: Task) -> None:
        """Помечает напоминание отправленным.

        Проверяем и remind_at: если пользователь успел перенести напоминание, пока мы отправляли
        старое, новое не должно считаться отправленным.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET reminded = 1 WHERE id = ? AND remind_at = ?",
                (task.id, _iso(task.remind_at)),
            )
