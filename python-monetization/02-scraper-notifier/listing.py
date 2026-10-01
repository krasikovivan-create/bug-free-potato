"""Разбор HTML: из страницы получаем список объявлений (Item) с помощью CSS-селекторов из конфига."""
import re
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from config import Source


@dataclass(frozen=True)
class Item:
    source: str            # имя источника из конфига
    url: str               # абсолютная ссылка — она же уникальный «паспорт» объявления
    title: str
    price: float | None    # число для фильтров (None, если цены нет или не поняли)
    price_text: str | None  # как цена выглядит на сайте: «£51.77», «1 500 000 ₽»


# Число с пробелами/апострофами как разделителями тысяч («1 500 000,50», «1'299»)
# ИЛИ обычное число с точками/запятыми («51.77», «1,299.00», «12,5»).
_NUMBER = re.compile(r"\d{1,3}(?:[   ']\d{3})+(?:[.,]\d{1,2})?(?!\d)|\d+(?:[.,]\d+)*")


def parse_price(text: str | None) -> float | None:
    """'£51.77' -> 51.77; '1 500 000 ₽' -> 1500000.0; '$1,299.00' -> 1299.0; '12,50 €' -> 12.5.

    Берёт первое число в строке. Если чисел нет («Договорная») — возвращает None.
    """
    if not text:
        return None
    match = _NUMBER.search(text)
    if not match:
        return None
    raw = re.sub(r"[   ']", "", match.group())

    has_dot, has_comma = "." in raw, "," in raw
    if has_dot and has_comma:
        # «1,299.00» или «1.299,00»: десятичный разделитель — тот, что стоит последним.
        decimal = "." if raw.rfind(".") > raw.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        raw = raw.replace(thousands, "").replace(decimal, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = raw.split(sep)
        if len(parts) == 2 and len(parts[1]) != 3:
            raw = parts[0] + "." + parts[1]   # «12,50» / «51.77» — десятичная дробь
        else:
            raw = "".join(parts)              # «1,299» / «1.299.000» — разделители тысяч
    return float(raw)


def make_soup(content: bytes | str) -> BeautifulSoup:
    # Передаём байты: BeautifulSoup сам определит кодировку по <meta charset>. Так кириллица не ломается.
    return BeautifulSoup(content, "html.parser")


def _pick(element: Tag, selector: str) -> Tag | None:
    """Находит элемент внутри объявления. Селектор «.» означает «само объявление»."""
    return element if selector == "." else element.select_one(selector)


def _clean(text: str) -> str:
    return " ".join(text.split())


def parse_listing(soup: BeautifulSoup, page_url: str, source: Source) -> tuple[list[Item], list[str]]:
    """Возвращает (найденные объявления, список проблем).

    Объявление, где нет заголовка или ссылки, пропускается, а в проблемы пишется понятное сообщение —
    по нему видно, что селекторы пора обновить (сайт поменял вёрстку).
    """
    items: list[Item] = []
    problems: list[str] = []

    for number, element in enumerate(soup.select(source.item), start=1):
        title_el = _pick(element, source.title)
        title = ""
        if title_el is not None:
            raw_title = title_el.get(source.title_attr, "") if source.title_attr else title_el.get_text(" ")
            title = _clean(str(raw_title))
        if not title:
            problems.append(f"объявление №{number}: не найден заголовок (селектор {source.title!r})")
            continue

        link_el = _pick(element, source.link)
        href = str(link_el.get(source.link_attr, "")).strip() if link_el is not None else ""
        url = urldefrag(urljoin(page_url, href))[0] if href else ""
        if urlsplit(url).scheme not in ("http", "https"):  # пусто, javascript:, mailto: …
            problems.append(f"объявление №{number} «{title[:40]}»: нет рабочей ссылки (селектор {source.link!r})")
            continue

        price_text = price = None
        if source.price:
            price_el = _pick(element, source.price)
            if price_el is not None:
                price_text = _clean(price_el.get_text(" ")) or None
                price = parse_price(price_text)

        items.append(Item(source=source.name, url=url, title=title, price=price, price_text=price_text))

    return items, problems


def find_next_page(soup: BeautifulSoup, page_url: str, source: Source) -> str | None:
    """Ссылка на следующую страницу списка или None, если страниц больше нет."""
    if not source.next_page:
        return None
    link = soup.select_one(source.next_page)
    href = str(link.get("href", "")).strip() if link is not None else ""
    if not href:
        return None
    url = urldefrag(urljoin(page_url, href))[0]
    return url if urlsplit(url).scheme in ("http", "https") else None
