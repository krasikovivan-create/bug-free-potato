import pytest

from conftest import listing_html, make_config
from listing import find_next_page, make_soup, parse_listing, parse_price


@pytest.mark.parametrize("text, expected", [
    ("£51.77", 51.77),
    ("$1,299.00", 1299.0),
    ("1.299,00 €", 1299.0),
    ("12,50 €", 12.5),
    ("12,5", 12.5),
    ("1 500 000 ₽", 1500000.0),
    ("1 500 000 ₽", 1500000.0),       # неразрывные пробелы — так пишут сайты
    ("1 299,50 руб.", 1299.5),
    ("1'299", 1299.0),
    ("1,299", 1299.0),                           # запятая + ровно 3 цифры = разделитель тысяч
    ("1.299.000", 1299000.0),
    ("0.5", 0.5),
    ("от 100 до 200", 100.0),
    ("Цена: 50 20 штук", 50.0),                  # «50 20» — не 5020
    ("999", 999.0),
    ("USD 1,000", 1000.0),
])
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize("text", [None, "", "   ", "Договорная", "free", "—"])
def test_parse_price_without_number(text):
    assert parse_price(text) is None


def test_parse_listing_basic():
    source = make_config().sources[0]
    html = listing_html([
        ("A Light in the Attic", "catalogue/a-light_1000/index.html", "£51.77"),
        ("Tipping the Velvet", "catalogue/tipping_999/index.html", "£53.74"),
    ])
    items, problems = parse_listing(make_soup(html), "http://shop.test/", source)

    assert problems == []
    assert [i.title for i in items] == ["A Light in the Attic", "Tipping the Velvet"]  # из атрибута, а не обрезанный текст
    assert items[0].url == "http://shop.test/catalogue/a-light_1000/index.html"
    assert items[0].price == 51.77 and items[0].price_text == "£51.77"
    assert items[0].source == "Shop"


def test_relative_links_resolve_against_the_page_url():
    source = make_config().sources[0]
    html = listing_html([("Book", "../other/book.html#reviews", "£1.00"), ("Abs", "/root/abs.html", "£2.00")])
    items, _ = parse_listing(make_soup(html), "http://shop.test/catalogue/page-2.html", source)
    assert items[0].url == "http://shop.test/other/book.html"   # ../ разобрано, #якорь убран
    assert items[1].url == "http://shop.test/root/abs.html"


def test_broken_items_are_skipped_with_a_readable_problem():
    source = make_config().sources[0]
    html = """<html><body>
      <article class="product_pod"><h3><a href="/ok" title="Good">x</a></h3></article>
      <article class="product_pod"><h3><a href="/no-title">x</a></h3></article>
      <article class="product_pod"><h3><a href="javascript:void(0)" title="JS link">x</a></h3></article>
      <article class="product_pod"><h3><a href="mailto:a@b.c" title="Mail link">x</a></h3></article>
      <article class="product_pod"><h3><a title="No href">x</a></h3></article>
    </body></html>"""
    items, problems = parse_listing(make_soup(html), "http://shop.test/", source)

    assert [i.title for i in items] == ["Good"]
    assert len(problems) == 4
    assert "не найден заголовок" in problems[0]
    assert all("нет рабочей ссылки" in p for p in problems[1:])


def test_missing_price_gives_none_not_an_error():
    source = make_config().sources[0]
    html = '<article class="product_pod"><h3><a href="/x" title="No price">x</a></h3></article>'
    (item,), problems = parse_listing(make_soup(html), "http://shop.test/", source)
    assert item.price is None and item.price_text is None and problems == []


def test_dot_selector_means_the_item_itself():
    source = make_config(item="a.job", title=".", link=".", title_attr=None, price=None).sources[0]
    html = '<a class="job" href="/jobs/1">  Python   developer \n</a><a class="job" href="/jobs/2">Go dev</a>'
    items, problems = parse_listing(make_soup(html), "http://jobs.test/", source)
    assert [(i.title, i.url) for i in items] == [
        ("Python developer", "http://jobs.test/jobs/1"),   # лишние пробелы и переводы строк схлопнуты
        ("Go dev", "http://jobs.test/jobs/2"),
    ]
    assert problems == []


def test_cyrillic_page_in_windows_1251_is_decoded_by_meta_charset():
    source = make_config().sources[0]
    html = listing_html([("Продам велосипед", "/v1", "15 000 ₽".replace("₽", "руб"))], charset="windows-1251")
    items, _ = parse_listing(make_soup(html.encode("cp1251")), "http://shop.test/", source)
    assert items[0].title == "Продам велосипед"
    assert items[0].price == 15000.0


def test_find_next_page():
    source = make_config(next_page="li.next a", max_pages=2).sources[0]
    page = make_soup(listing_html([("A", "/a", "£1")], next_href="page-2.html"))
    assert find_next_page(page, "http://shop.test/catalogue/page-1.html", source) == "http://shop.test/catalogue/page-2.html"

    last = make_soup(listing_html([("A", "/a", "£1")]))
    assert find_next_page(last, "http://shop.test/", source) is None
    assert find_next_page(page, "http://shop.test/", make_config().sources[0]) is None  # next_page не задан
