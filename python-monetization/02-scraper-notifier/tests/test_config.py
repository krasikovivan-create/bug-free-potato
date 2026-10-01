from pathlib import Path

import pytest

from config import ConfigError, load_config, parse_config

ROOT = Path(__file__).resolve().parent.parent

GOOD_SOURCE = {"name": "S", "url": "https://example.com/", "item": "li", "title": "a", "link": "a"}


def src(**changes):
    source = {**GOOD_SOURCE, **changes}
    return {"sources": [{k: v for k, v in source.items() if v is not None}]}


def test_example_config_is_valid():
    config = load_config(str(ROOT / "config.example.toml"))
    assert config.sources[0].name == "Books demo"
    assert config.sources[0].max_pages == 3
    assert config.sources[0].max_price == 30.0
    assert config.settings.interval_minutes == 60


def test_defaults():
    config = parse_config(src())
    assert config.settings.respect_robots is True
    assert config.settings.notify_on_first_run is False
    source = config.sources[0]
    assert (source.link_attr, source.max_pages, source.include, source.min_price) == ("href", 1, (), None)


def test_numbers_become_floats_and_words_are_stripped():
    source = parse_config(src(price="b", min_price=5, max_price=10.5, include=[" Python "])).sources[0]
    assert (source.min_price, source.max_price, source.include) == (5.0, 10.5, ("Python",))


def test_missing_file_gives_a_hint(tmp_path):
    with pytest.raises(ConfigError, match="config.example.toml"):
        load_config(str(tmp_path / "nope.toml"))


def test_toml_syntax_error(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("name = = 1")
    with pytest.raises(ConfigError, match="синтаксическая ошибка"):
        load_config(str(path))


@pytest.mark.parametrize("raw, message", [
    ({}, "хотя бы один блок"),
    ({"sources": []}, "хотя бы один блок"),
    ({"sources": "oops"}, "хотя бы один блок"),
    ({**src(), "interval_minutes_typo": 5}, "Неизвестные настройки"),
    ({**src(), "interval_minutes": 0}, "не меньше 1"),
    ({**src(), "interval_minutes": "60"}, "целым числом"),
    ({**src(), "interval_minutes": True}, "целым числом"),
    ({**src(), "request_delay": -1}, "отрицательным"),
    ({**src(), "timeout": 0}, "больше 0"),
    ({**src(), "respect_robots": "yes"}, "true или false"),
    (src(name=None), "не хватает ключа «name»"),
    (src(link=None), "не хватает ключа «link»"),
    (src(name="  "), "не может быть пустым"),
    (src(incldue=["x"]), "неизвестные ключи incldue"),
    (src(url="example.com"), "http:// или https://"),
    (src(url="ftp://example.com/"), "http:// или https://"),
    (src(item="li[["), "неверный CSS-селектор"),
    (src(title=""), "не может быть пустым"),
    (src(title=5), "CSS-селектором"),
    (src(max_pages=0), "от 1 до 50"),
    (src(max_pages=51, next_page="a.next"), "от 1 до 50"),
    (src(max_pages=3), "не задан «next_page»"),
    (src(include="python"), "списком слов"),
    (src(include=["ok", 5]), "списком непустых строк"),
    (src(exclude=[""]), "списком непустых строк"),
    (src(max_price="cheap", price="b"), "должно быть числом"),
    (src(max_price=True, price="b"), "должно быть числом"),
    (src(max_price=10), "не указан селектор «price»"),
    (src(min_price=20, max_price=10, price="b"), "больше «max_price»"),
    (src(allow_empty="no"), "true или false"),
])
def test_invalid_configs_are_rejected_with_a_helpful_message(raw, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(raw)


def test_error_message_names_the_source():
    with pytest.raises(ConfigError, match="источник «S»"):
        parse_config(src(url="nope"))


def test_duplicate_source_names_are_rejected():
    raw = {"sources": [GOOD_SOURCE, dict(GOOD_SOURCE)]}
    with pytest.raises(ConfigError, match="Два источника"):
        parse_config(raw)
