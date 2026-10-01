"""Чтение и проверка config.toml.

Весь «клиентский» код проекта — это конфиг: какой сайт смотреть, какие у него CSS-селекторы,
что считать подходящим объявлением. Конфиг правит человек, поэтому каждую ошибку (опечатка в ключе,
кривой селектор, текст вместо числа) мы ловим сразу при старте и объясняем по-человечески,
а не падаем через час с непонятным traceback.
"""
import tomllib
from dataclasses import dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup


class ConfigError(Exception):
    """Ошибка в config.toml. Текст рассчитан на человека, который будет править файл."""


@dataclass(frozen=True)
class Settings:
    interval_minutes: int = 60       # как часто проверять сайты в режиме `run`
    request_delay: float = 2.0       # пауза (сек) между запросами к одному сайту — не нагружаем чужой сервер
    timeout: float = 20.0            # сколько ждём ответ сайта
    user_agent: str = "ScraperNotifier/1.0"
    respect_robots: bool = True      # соблюдать robots.txt (отключай только для СВОИХ сайтов)
    notify_on_first_run: bool = False  # False: первый проход только запоминает объявления, без рассылки
    db_path: str = "scraper.db"


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    item: str                        # CSS-селектор одного объявления на странице
    title: str                       # селекторы ниже — внутри объявления; "." = само объявление
    link: str
    title_attr: str | None = None    # взять заголовок из атрибута (напр. "title"), а не из текста
    link_attr: str = "href"
    price: str | None = None
    next_page: str | None = None     # селектор ссылки «следующая страница»
    max_pages: int = 1
    allow_empty: bool = False        # True: «на странице 0 объявлений» — нормально, не считать сбоем
    include: tuple[str, ...] = ()    # хотя бы одно из слов должно быть в заголовке
    exclude: tuple[str, ...] = ()    # ни одного из этих слов в заголовке быть не должно
    min_price: float | None = None
    max_price: float | None = None


@dataclass(frozen=True)
class Config:
    settings: Settings
    sources: tuple[Source, ...]


_SETTINGS_KEYS = {
    "interval_minutes", "request_delay", "timeout", "user_agent",
    "respect_robots", "notify_on_first_run", "db_path",
}
_SOURCE_REQUIRED = {"name", "url", "item", "title", "link"}
_SOURCE_OPTIONAL = {
    "title_attr", "link_attr", "price", "next_page", "max_pages", "allow_empty",
    "include", "exclude", "min_price", "max_price",
}
_MISSING = object()
_EMPTY_SOUP = BeautifulSoup("", "html.parser")


def load_config(path: str) -> Config:
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(
            f"Файл {path} не найден. Скопируй config.example.toml в config.toml и поправь под свой сайт."
        ) from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: синтаксическая ошибка TOML — {exc}") from None
    return parse_config(raw)


def parse_config(raw: dict) -> Config:
    unknown = set(raw) - _SETTINGS_KEYS - {"sources"}
    if unknown:
        raise ConfigError(f"Неизвестные настройки: {_names(unknown)}. Допустимые: {_names(_SETTINGS_KEYS)}.")

    settings = _parse_settings(raw)

    raw_sources = raw.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError("Нужен хотя бы один блок [[sources]] — см. config.example.toml.")

    sources, names = [], set()
    for index, table in enumerate(raw_sources, start=1):
        source = _parse_source(table, index)
        if source.name in names:
            raise ConfigError(f"Два источника с именем «{source.name}» — имена должны быть разными.")
        names.add(source.name)
        sources.append(source)
    return Config(settings=settings, sources=tuple(sources))


# ---------------------------------------------------------------- внутренности

def _names(keys) -> str:
    return ", ".join(sorted(keys))


def _get(table: dict, key: str, types: tuple, where: str, default=_MISSING, kind: str = ""):
    """Достаёт значение и проверяет тип. bool в TOML — отдельный тип, на числа не похож."""
    if key not in table:
        if default is _MISSING:
            raise ConfigError(f"{where}: не хватает ключа «{key}».")
        return default
    value = table[key]
    if not isinstance(value, types) or (isinstance(value, bool) and bool not in types):
        raise ConfigError(f"{where}: «{key}» должно быть {kind}, а сейчас {value!r}.")
    return value


def _parse_settings(raw: dict) -> Settings:
    d = Settings()
    where = "настройки"
    interval = _get(raw, "interval_minutes", (int,), where, d.interval_minutes, "целым числом минут")
    delay = _get(raw, "request_delay", (int, float), where, d.request_delay, "числом секунд")
    timeout = _get(raw, "timeout", (int, float), where, d.timeout, "числом секунд")
    if interval < 1:
        raise ConfigError(f"{where}: «interval_minutes» должно быть не меньше 1.")
    if delay < 0:
        raise ConfigError(f"{where}: «request_delay» не может быть отрицательным.")
    if timeout <= 0:
        raise ConfigError(f"{where}: «timeout» должно быть больше 0.")
    return Settings(
        interval_minutes=interval,
        request_delay=float(delay),
        timeout=float(timeout),
        user_agent=_get(raw, "user_agent", (str,), where, d.user_agent, "строкой"),
        respect_robots=_get(raw, "respect_robots", (bool,), where, d.respect_robots, "true или false"),
        notify_on_first_run=_get(raw, "notify_on_first_run", (bool,), where, d.notify_on_first_run, "true или false"),
        db_path=_get(raw, "db_path", (str,), where, d.db_path, "строкой"),
    )


def _check_selector(selector: str, key: str, where: str) -> str:
    if not selector.strip():
        raise ConfigError(f"{where}: «{key}» не может быть пустым.")
    if selector != ".":  # "." — особый случай: «само объявление»
        try:
            _EMPTY_SOUP.select(selector)
        except Exception as exc:  # soupsieve бросает SelectorSyntaxError и ValueError
            reason = str(exc).splitlines()[0]
            raise ConfigError(f"{where}: «{key}» — неверный CSS-селектор {selector!r} ({reason}).") from None
    return selector


def _words(table: dict, key: str, where: str) -> tuple[str, ...]:
    value = _get(table, key, (list,), where, [], "списком слов, например [\"python\", \"django\"]")
    if not all(isinstance(w, str) and w.strip() for w in value):
        raise ConfigError(f"{where}: «{key}» должно быть списком непустых строк, а сейчас {value!r}.")
    return tuple(w.strip() for w in value)


def _parse_source(table: object, index: int) -> Source:
    if not isinstance(table, dict):
        raise ConfigError(f"Источник №{index}: ожидался блок [[sources]].")
    label = table.get("name") if isinstance(table.get("name"), str) and table["name"].strip() else None
    where = f"источник «{label}»" if label else f"источник №{index}"

    unknown = set(table) - _SOURCE_REQUIRED - _SOURCE_OPTIONAL
    if unknown:
        raise ConfigError(f"{where}: неизвестные ключи {_names(unknown)} — опечатка? Допустимые: {_names(_SOURCE_REQUIRED | _SOURCE_OPTIONAL)}.")

    name = _get(table, "name", (str,), where, kind="строкой").strip()
    if not name:
        raise ConfigError(f"{where}: «name» не может быть пустым.")

    url = _get(table, "url", (str,), where, kind="строкой").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError(f"{where}: «url» должен начинаться с http:// или https:// — сейчас {url!r}.")

    selectors = {
        key: _check_selector(_get(table, key, (str,), where, kind="CSS-селектором (строкой)"), key, where)
        for key in ("item", "title", "link")
    }
    for key in ("price", "next_page"):
        if key in table:
            selectors[key] = _check_selector(_get(table, key, (str,), where, kind="CSS-селектором (строкой)"), key, where)

    max_pages = _get(table, "max_pages", (int,), where, 1, "целым числом")
    if not 1 <= max_pages <= 50:
        raise ConfigError(f"{where}: «max_pages» должно быть от 1 до 50.")
    if max_pages > 1 and "next_page" not in selectors:
        raise ConfigError(f"{where}: «max_pages» больше 1, но не задан «next_page» — как бот найдёт следующую страницу?")

    min_price = _get(table, "min_price", (int, float), where, None, "числом")
    max_price = _get(table, "max_price", (int, float), where, None, "числом")
    if (min_price is not None or max_price is not None) and "price" not in selectors:
        raise ConfigError(f"{where}: фильтр по цене задан, но не указан селектор «price».")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise ConfigError(f"{where}: «min_price» ({min_price}) больше «max_price» ({max_price}).")

    return Source(
        name=name,
        url=url,
        item=selectors["item"],
        title=selectors["title"],
        link=selectors["link"],
        title_attr=_get(table, "title_attr", (str,), where, None, "строкой"),
        link_attr=_get(table, "link_attr", (str,), where, "href", "строкой"),
        price=selectors.get("price"),
        next_page=selectors.get("next_page"),
        max_pages=max_pages,
        allow_empty=_get(table, "allow_empty", (bool,), where, False, "true или false"),
        include=_words(table, "include", where),
        exclude=_words(table, "exclude", where),
        min_price=None if min_price is None else float(min_price),
        max_price=None if max_price is None else float(max_price),
    )
