"""Время: разбор того, что пишет пользователь («30m», «18:00», «tomorrow 09:00»), и вывод даты.

Главное правило: ВНУТРИ (в базе, в расчётах) всё хранится в UTC,
а пользователю показывается в его часовом поясе. Так не бывает путаницы при переводе часов
и пользователи из разных стран не мешают друг другу.
"""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import texts

# «30m», «2h», «1d», «1h30m», «1h 30m». Русские буквы (д/ч/м) тоже работают.
_RELATIVE = re.compile(r"(?:(\d{1,4})\s*[dд])?\s*(?:(\d{1,4})\s*[hч])?\s*(?:(\d{1,4})\s*[mм])?")
# «18:00» или «9:30»
_TIME_ONLY = re.compile(r"(\d{1,2}):(\d{2})")
# «tomorrow 09:00» / «завтра 09:00»
_TOMORROW = re.compile(r"(?:tomorrow|завтра)\s+(\d{1,2}):(\d{2})")
# «2026-12-31 20:00»
_ISO_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})")
# «31.12 20:00» (год можно не указывать) или «31.12.2026 20:00»
_DOTTED_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s+(\d{1,2}):(\d{2})")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def format_dt(dt: datetime, zone: ZoneInfo) -> str:
    """Дата для показа пользователю: '2026-10-01 18:30' в его часовом поясе."""
    return dt.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def parse_when(text: str, now: datetime, zone: ZoneInfo) -> datetime:
    """Превращает текст пользователя в момент времени (UTC).

    now — текущий момент (UTC), zone — часовой пояс пользователя.
    Если не поняли или момент уже прошёл — бросает ValueError с текстом для пользователя.
    """
    try:
        when = _parse(text.strip().lower(), now, zone)
    except (ValueError, OverflowError):  # напр. «25:99» или 31 февраля
        raise ValueError(texts.BAD_TIME) from None
    if when is None:
        raise ValueError(texts.BAD_TIME)
    if when <= now:
        raise ValueError(texts.TIME_IN_PAST)
    return when


def _parse(s: str, now: datetime, zone: ZoneInfo) -> datetime | None:
    local_now = now.astimezone(zone)

    m = _RELATIVE.fullmatch(s)
    if m and any(m.groups()):
        days, hours, minutes = (int(g or 0) for g in m.groups())
        return now + timedelta(days=days, hours=hours, minutes=minutes)

    m = _TIME_ONLY.fullmatch(s)
    if m:
        hour, minute = int(m[1]), int(m[2])
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:  # сегодня это время уже прошло — значит, завтра
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    m = _TOMORROW.fullmatch(s)
    if m:
        hour, minute = int(m[1]), int(m[2])
        tomorrow = local_now + timedelta(days=1)
        candidate = tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate.astimezone(timezone.utc)

    m = _ISO_DATE.fullmatch(s)
    if m:
        year, month, day, hour, minute = (int(g) for g in m.groups())
        return _local(zone, year, month, day, hour, minute)

    m = _DOTTED_DATE.fullmatch(s)
    if m:
        day, month, year, hour, minute = m.groups()
        day, month, hour, minute = int(day), int(month), int(hour), int(minute)
        if year:
            return _local(zone, int(year), month, day, hour, minute)
        # Год не указан: берём текущий, а если дата уже прошла — следующий.
        candidate = _local(zone, local_now.year, month, day, hour, minute)
        if candidate <= now:
            candidate = _local(zone, local_now.year + 1, month, day, hour, minute)
        return candidate

    return None


def _local(zone: ZoneInfo, year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """Дата-время «по часам» пользователя -> UTC."""
    return datetime(year, month, day, hour, minute, tzinfo=zone).astimezone(timezone.utc)
