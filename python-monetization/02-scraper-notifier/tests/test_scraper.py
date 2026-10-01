"""Логика прохода без сети: фейковый загрузчик страниц и фейковый Telegram."""
import pytest

import scraper
from config import parse_config
from conftest import listing_html, make_config
from fetcher import FetchError
from notifier import NotifyError
from scraper import FAIL_ALERT_AFTER, MAX_ITEMS_PER_MESSAGE, ScrapeError, run_once, scrape_source
from storage import Storage


class FakeFetcher:
    """pages: url -> bytes | str | Exception. Любой другой адрес — 404."""

    def __init__(self, pages=None):
        self.pages = pages or {}
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        page = self.pages.get(url, FetchError(f"{url}: HTTP 404"))
        if isinstance(page, Exception):
            raise page
        return page.encode() if isinstance(page, str) else page


class FakeNotifier:
    def __init__(self):
        self.sent = []
        self.attempts = 0
        self.fail_attempts = set()   # номера попыток отправки (с 1), которые должны завершиться ошибкой

    def send(self, text):
        self.attempts += 1
        if self.attempts in self.fail_attempts:
            raise NotifyError("Telegram недоступен")
        self.sent.append(text)


@pytest.fixture
def db(tmp_path):
    return Storage(str(tmp_path / "t.db"))


@pytest.fixture
def notifier():
    return FakeNotifier()


def books(*titles, price="£10.00"):
    return listing_html([(t, f"/book/{t.replace(' ', '-')}", price) for t in titles])


URL = "http://shop.test/"


def test_first_run_only_remembers_then_new_items_are_sent_once(db, notifier):
    config = make_config()
    fetcher = FakeFetcher({URL: books("Old one", "Old two")})

    assert run_once(config, db, notifier, fetcher) == {"Shop": 0}
    assert notifier.sent == []                       # на первом проходе тишина

    fetcher.pages[URL] = books("Brand new", "Old one", "Old two")
    assert run_once(config, db, notifier, fetcher) == {"Shop": 1}
    assert len(notifier.sent) == 1
    assert "Brand new" in notifier.sent[0] and "Old one" not in notifier.sent[0]

    assert run_once(config, db, notifier, fetcher) == {"Shop": 0}   # то же самое повторно — тишина
    assert len(notifier.sent) == 1


def test_notify_on_first_run_sends_everything_in_batches(db, notifier):
    config = make_config(notify_on_first_run=True)
    titles = [f"Book {n:02d}" for n in range(25)]
    run_once(config, db, notifier, FakeFetcher({URL: books(*titles)}))

    assert len(notifier.sent) == 3
    assert [msg.count("<a href") for msg in notifier.sent] == [MAX_ITEMS_PER_MESSAGE, MAX_ITEMS_PER_MESSAGE, 5]


def test_only_matching_items_are_sent(db, notifier):
    config = make_config(include=["python"], max_price=50)
    fetcher = FakeFetcher({URL: books("Seed")})
    run_once(config, db, notifier, fetcher)           # первый проход

    fetcher.pages[URL] = listing_html([
        ("Python tricks", "/1", "£20"),
        ("Python for rich people", "/2", "£500"),     # слишком дорого
        ("Cooking", "/3", "£5"),                      # нет слова
    ])
    run_once(config, db, notifier, fetcher)
    assert len(notifier.sent) == 1
    assert "Python tricks" in notifier.sent[0] and "rich" not in notifier.sent[0]


def test_items_skipped_by_filters_appear_after_filters_are_relaxed(db, notifier):
    strict = make_config(max_price=5)
    fetcher = FakeFetcher({URL: books("First")})
    run_once(strict, db, notifier, fetcher)           # первый проход (First не подошёл по цене, не запомнен)

    fetcher.pages[URL] = books("First", "Second")     # оба по £10 — не проходят max_price=5
    run_once(strict, db, notifier, fetcher)
    assert notifier.sent == []

    relaxed = make_config(max_price=50)
    run_once(relaxed, db, notifier, fetcher)
    assert len(notifier.sent) == 1                    # теперь подошли — клиент их не потерял
    assert "First" in notifier.sent[0] and "Second" in notifier.sent[0]


def test_failed_telegram_send_is_retried_next_run(db, notifier):
    config = make_config()
    fetcher = FakeFetcher({URL: books("Seed")})
    run_once(config, db, notifier, fetcher)

    fetcher.pages[URL] = books("Seed", "News")
    notifier.fail_attempts = {1}
    assert run_once(config, db, notifier, fetcher) == {}     # источник завершился ошибкой отправки
    assert notifier.sent == []

    run_once(config, db, notifier, fetcher)                  # Telegram снова работает — доставили
    assert len(notifier.sent) == 1 and "News" in notifier.sent[0]


def test_partial_delivery_does_not_resend_what_already_arrived(db, notifier):
    config = make_config(notify_on_first_run=True)
    titles = [f"Book {n:02d}" for n in range(15)]
    fetcher = FakeFetcher({URL: books(*titles)})

    notifier.fail_attempts = {2}                      # первая пачка уйдёт, вторая — нет
    run_once(config, db, notifier, fetcher)
    assert len(notifier.sent) == 1                    # доставлены первые 10

    run_once(config, db, notifier, fetcher)
    assert len(notifier.sent) == 2
    assert notifier.sent[1].count("<a href") == 5     # доехали только недостающие 5, а не все 15


def test_alert_after_three_failures_exactly_once_and_recovery_message(db, notifier):
    config = make_config()
    fetcher = FakeFetcher({URL: FetchError("http://shop.test/: HTTP 500")})

    for _ in range(FAIL_ALERT_AFTER - 1):
        run_once(config, db, notifier, fetcher)
    assert notifier.sent == []                        # два сбоя — ещё не тревожим

    run_once(config, db, notifier, fetcher)
    assert len(notifier.sent) == 1
    assert "3 неудачных" in notifier.sent[0] and "HTTP 500" in notifier.sent[0]

    run_once(config, db, notifier, fetcher)
    run_once(config, db, notifier, fetcher)
    assert len(notifier.sent) == 1                    # дальше не спамим

    fetcher.pages[URL] = books("Alive")
    run_once(config, db, notifier, fetcher)
    assert len(notifier.sent) == 2 and "снова работает" in notifier.sent[1]


def test_short_outage_is_forgotten_and_does_not_alert(db, notifier):
    config = make_config()
    fetcher = FakeFetcher({URL: FetchError("down")})
    run_once(config, db, notifier, fetcher)
    run_once(config, db, notifier, fetcher)
    fetcher.pages[URL] = books("Alive")
    run_once(config, db, notifier, fetcher)
    run_once(config, db, notifier, FakeFetcher({URL: FetchError("down")}))
    run_once(config, db, notifier, FakeFetcher({URL: FetchError("down")}))
    assert notifier.sent == []                        # 2 + 2 сбоя с успехом между ними — это не «3 подряд»


def test_page_without_items_counts_as_failure_unless_allowed(db, notifier):
    empty = "<html><body><p>Nothing here</p></body></html>"
    with pytest.raises(ScrapeError, match="вёрстку"):
        scrape_source(make_config().sources[0], FakeFetcher({URL: empty}))
    assert scrape_source(make_config(allow_empty=True).sources[0], FakeFetcher({URL: empty})) == []

    for _ in range(FAIL_ALERT_AFTER):
        run_once(make_config(), db, notifier, FakeFetcher({URL: empty}))
    assert len(notifier.sent) == 1
    assert "3 неудачных" in notifier.sent[0] and "вёрстку" in notifier.sent[0]
    run_once(make_config(allow_empty=True), db, notifier, FakeFetcher({URL: empty}))   # это уже «здоров»
    assert "снова работает" in notifier.sent[-1]


def test_one_broken_source_does_not_block_the_others(db, notifier):
    base = {"item": "article.product_pod", "title": "h3 a", "title_attr": "title", "link": "h3 a"}
    config = parse_config({"request_delay": 0, "sources": [
        {"name": "Broken", "url": "http://a.test/", **base},
        {"name": "Working", "url": "http://b.test/", **base},
    ]})
    fetcher = FakeFetcher({"http://a.test/": FetchError("down"), "http://b.test/": books("Seed")})
    run_once(config, db, notifier, fetcher)

    fetcher.pages["http://b.test/"] = books("Seed", "Fresh")
    result = run_once(config, db, notifier, fetcher)
    assert result == {"Working": 1}
    assert "Fresh" in notifier.sent[0]


def test_pagination_follows_next_links_up_to_max_pages_and_dedupes(db):
    source = make_config(next_page="li.next a", max_pages=3).sources[0]
    fetcher = FakeFetcher({
        URL: listing_html([("A", "/a", "£1"), ("B", "/b", "£1")], next_href="p2.html"),
        URL + "p2.html": listing_html([("B", "/b", "£1"), ("C", "/c", "£1")], next_href="p3.html"),
        URL + "p3.html": listing_html([("D", "/d", "£1")], next_href="p4.html"),
        URL + "p4.html": listing_html([("E", "/e", "£1")]),
    })
    items = scrape_source(source, fetcher)
    assert [i.title for i in items] == ["A", "B", "C", "D"]          # B не задвоился, до p4 не дошли
    assert fetcher.requested == [URL, URL + "p2.html", URL + "p3.html"]


def test_pagination_stops_on_a_loop(db):
    source = make_config(next_page="li.next a", max_pages=10).sources[0]
    fetcher = FakeFetcher({URL: listing_html([("A", "/a", "£1")], next_href=URL)})   # «следующая» — сама же страница
    assert len(scrape_source(source, fetcher)) == 1
    assert fetcher.requested == [URL]


def test_empty_second_page_is_just_the_end(db):
    source = make_config(next_page="li.next a", max_pages=3).sources[0]
    fetcher = FakeFetcher({
        URL: listing_html([("A", "/a", "£1")], next_href="p2.html"),
        URL + "p2.html": "<html><body>no more</body></html>",
    })
    assert [i.title for i in scrape_source(source, fetcher)] == ["A"]


def test_duplicate_alert_text_is_html_safe(db, notifier):
    config = make_config(name="<Shop & Co>")
    run_once(config, db, notifier, FakeFetcher({URL: FetchError("bad <thing> & more")}))
    run_once(config, db, notifier, FakeFetcher({URL: FetchError("bad <thing> & more")}))
    run_once(config, db, notifier, FakeFetcher({URL: FetchError("bad <thing> & more")}))
    assert "<Shop" not in notifier.sent[0] and "&lt;Shop &amp; Co&gt;" in notifier.sent[0]
    assert "<thing>" not in notifier.sent[0]
