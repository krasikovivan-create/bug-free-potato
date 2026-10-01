"""Парсер сайтов с уведомлениями в Telegram.

Команды (подробности — в README.md):
    python scraper.py check      показать, что находится на сайте (без базы и без Telegram) — для настройки селекторов
    python scraper.py once       один проход: найти новое и отправить в Telegram (для cron)
    python scraper.py run        то же самое, но по кругу каждые interval_minutes минут
    python scraper.py chat-id    узнать свой chat id (сначала напиши боту /start)

Как это работает за один проход:
    скачать страницы -> достать объявления (CSS-селекторы) -> оставить подходящие под фильтры ->
    выкинуть те, что уже видели (SQLite) -> отправить новые в Telegram -> запомнить отправленные.
"""
import argparse
import logging
import os
import sys
import time
from html import escape
from itertools import islice

from dotenv import load_dotenv

from config import Config, ConfigError, Source, load_config
from fetcher import FetchError, Fetcher
from filters import matches
from listing import Item, find_next_page, make_soup, parse_listing
from notifier import DEFAULT_API_BASE, NotifyError, TelegramNotifier, format_items
from storage import Storage

log = logging.getLogger("scraper")

MAX_ITEMS_PER_MESSAGE = 10   # новые объявления группируем по 10 в одно сообщение — не засыпаем чат
FAIL_ALERT_AFTER = 3         # столько сбоев подряд — и бот пишет в Telegram, что источник сломался


class ScrapeError(Exception):
    """Страница скачалась, но объявлений на ней не нашлось — вероятно, сайт изменил вёрстку."""


def chunked(items: list, size: int):
    iterator = iter(items)
    while batch := list(islice(iterator, size)):
        yield batch


# ---------------------------------------------------------------- сбор объявлений

def scrape_source(source: Source, fetcher: Fetcher) -> list[Item]:
    """Обходит страницы источника (до max_pages) и возвращает все найденные объявления."""
    items: list[Item] = []
    seen_urls: set[str] = set()
    visited: set[str] = set()
    url: str | None = source.url

    # for + range: цикл не может стать бесконечным, даже если сайт зациклит ссылки «следующая страница».
    for page_number in range(source.max_pages):
        if not url or url in visited:
            break
        visited.add(url)
        soup = make_soup(fetcher.get(url))
        page_items, problems = parse_listing(soup, url, source)
        for problem in problems:
            log.warning("[%s] %s", source.name, problem)

        if not page_items and page_number == 0 and not source.allow_empty:
            raise ScrapeError(
                f"на странице {url} не найдено ни одного объявления по селектору {source.item!r}: "
                "сайт мог изменить вёрстку (или список сейчас пуст — тогда поставь allow_empty = true)"
            )
        for item in page_items:
            if item.url not in seen_urls:  # одно объявление может попасться на двух страницах
                seen_urls.add(item.url)
                items.append(item)
        url = find_next_page(soup, url, source)
    return items


def process_source(source: Source, config: Config, fetcher: Fetcher, db: Storage, notifier: TelegramNotifier) -> int:
    """Один источник за один проход. Возвращает, сколько объявлений отправлено."""
    items = scrape_source(source, fetcher)

    # Страница получена и разобрана — источник «здоров». Если до этого он был сломан, сообщаем, что починился.
    previous_failures = db.record_success(source.name)
    if previous_failures >= FAIL_ALERT_AFTER:
        _alert(notifier, f"✅ <b>{escape(source.name)}</b>: снова работает.")

    suitable = [item for item in items if matches(item, source)]
    new = db.filter_new(source.name, suitable)
    log.info("[%s] найдено %d, подходит %d, новых %d", source.name, len(items), len(suitable), len(new))

    if not db.is_baselined(source.name):
        db.set_baselined(source.name)
        if not config.settings.notify_on_first_run:
            # Первый проход: на сайте уже висят десятки объявлений. Присылать их все — значит завалить клиента.
            # Просто запоминаем их, а писать будем только про те, что появятся позже.
            db.mark_seen(source.name, new)
            log.info("[%s] первый проход: запомнил объявлений: %d, уведомления начнутся со следующего", source.name, len(new))
            return 0

    sent = 0
    for batch in chunked(new, MAX_ITEMS_PER_MESSAGE):
        notifier.send(format_items(source.name, batch))
        db.mark_seen(source.name, batch)  # запоминаем только после успешной отправки: не дошло — отправим в следующий раз
        sent += len(batch)
    return sent


def _alert(notifier: TelegramNotifier, text: str) -> None:
    try:
        notifier.send(text)
    except NotifyError as exc:
        log.error("не удалось отправить служебное сообщение: %s", exc)


def run_once(config: Config, db: Storage, notifier: TelegramNotifier, fetcher: Fetcher | None = None) -> dict[str, int]:
    """Один проход по всем источникам. Сбой одного источника не мешает остальным."""
    fetcher = fetcher or Fetcher(config.settings)  # новый на каждый проход: robots.txt перечитывается
    result: dict[str, int] = {}
    for source in config.sources:
        try:
            result[source.name] = process_source(source, config, fetcher, db, notifier)
        except (FetchError, ScrapeError) as exc:
            failures = db.record_failure(source.name)
            log.warning("[%s] сбой %d подряд: %s", source.name, failures, exc)
            if failures == FAIL_ALERT_AFTER:  # ровно один раз, а не на каждом проходе
                _alert(notifier, f"⚠️ <b>{escape(source.name)}</b>: {failures} неудачных проверок подряд.\n{escape(str(exc))}")
        except NotifyError as exc:
            log.error("[%s] не удалось отправить в Telegram: %s", source.name, exc)
    return result


# ---------------------------------------------------------------- команды

def cmd_check(config: Config) -> int:
    fetcher = Fetcher(config.settings)
    failed = False
    for source in config.sources:
        print(f"\n=== {source.name} ({source.url})")
        try:
            items = scrape_source(source, fetcher)
        except (FetchError, ScrapeError) as exc:
            print(f"  ОШИБКА: {exc}")
            failed = True
            continue
        suitable = sum(matches(item, source) for item in items)
        print(f"  найдено {len(items)}, подходит под фильтры {suitable}")
        for item in items[:10]:
            mark = "✓" if matches(item, source) else "✗"
            price = item.price_text or "—"
            print(f"  {mark} {item.title[:60]:<60} {price:>12}  {item.url}")
        if len(items) > 10:
            print(f"  … и ещё {len(items) - 10}")
    return 1 if failed else 0


def cmd_chat_id(notifier: TelegramNotifier) -> int:
    chats = notifier.find_chats()
    if not chats:
        print("Бот пока никто не писал. Открой его в Telegram, нажми Start (или отправь /start) и запусти команду ещё раз.")
        return 1
    for chat_id, name in chats:
        print(f"TELEGRAM_CHAT_ID={chat_id}   ({name})")
    return 0


def run_forever(config: Config, db: Storage, notifier: TelegramNotifier) -> None:
    while True:
        try:
            run_once(config, db, notifier)
        except Exception:  # noqa: BLE001 — демон не должен падать из-за одной неожиданной ошибки
            log.exception("проход завершился ошибкой, попробую в следующий раз")
        log.info("следующая проверка через %d мин.", config.settings.interval_minutes)
        time.sleep(config.settings.interval_minutes * 60)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # читает .env рядом со скриптом (токен и chat id)
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)

    parser = argparse.ArgumentParser(description="Парсер сайтов с уведомлениями в Telegram")
    parser.add_argument("command", choices=["check", "once", "run", "chat-id"])
    parser.add_argument("--config", default="config.toml", help="путь к конфигу (по умолчанию config.toml)")
    args = parser.parse_args(argv)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    api_base = os.getenv("TELEGRAM_API_BASE", DEFAULT_API_BASE)  # менять нужно только в тестах

    try:
        if args.command == "chat-id":
            if not token:
                return _fail("Не задан TELEGRAM_BOT_TOKEN (см. .env.example).")
            return cmd_chat_id(TelegramNotifier(token, "", api_base))

        config = load_config(args.config)
        if args.command == "check":
            return cmd_check(config)

        if not token or not chat_id:
            return _fail("Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID (см. .env.example; chat id покажет `python scraper.py chat-id`).")
        db = Storage(os.getenv("DB_PATH") or config.settings.db_path)
        notifier = TelegramNotifier(token, chat_id, api_base)
        if args.command == "once":
            run_once(config, db, notifier)
        else:
            run_forever(config, db, notifier)
    except ConfigError as exc:
        return _fail(f"Ошибка в конфиге: {exc}")
    except NotifyError as exc:
        return _fail(f"Telegram: {exc}")
    except KeyboardInterrupt:
        print("\nОстановлено.")
    return 0


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
