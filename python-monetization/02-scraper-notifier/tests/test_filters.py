from conftest import make_config
from filters import matches
from listing import Item


def item(title="Learn Python fast", price=20.0):
    return Item(source="Shop", url="http://shop.test/1", title=title, price=price, price_text=None if price is None else str(price))


def test_no_filters_matches_everything():
    assert matches(item(), make_config().sources[0])


def test_include_is_any_of_and_case_insensitive():
    source = make_config(include=["DJANGO", "python"]).sources[0]
    assert matches(item("Learn PYTHON fast"), source)
    assert matches(item("Django for beginners"), source)
    assert not matches(item("Cooking for dummies"), source)


def test_exclude_wins_over_include():
    source = make_config(include=["python"], exclude=["Senior"]).sources[0]
    assert matches(item("Python developer"), source)
    assert not matches(item("Senior Python developer"), source)


def test_price_bounds_are_inclusive():
    source = make_config(min_price=10, max_price=20).sources[0]
    assert matches(item(price=10.0), source)
    assert matches(item(price=20.0), source)
    assert not matches(item(price=9.99), source)
    assert not matches(item(price=20.01), source)


def test_unknown_price_does_not_pass_a_price_filter():
    assert not matches(item(price=None), make_config(max_price=100).sources[0])
    assert not matches(item(price=None), make_config(min_price=1).sources[0])
    assert matches(item(price=None), make_config().sources[0])  # без фильтра по цене — проходит
