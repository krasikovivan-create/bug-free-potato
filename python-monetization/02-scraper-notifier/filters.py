"""Фильтры: подходит ли объявление под критерии клиента."""
from config import Source
from listing import Item


def matches(item: Item, source: Source) -> bool:
    """True, если объявление проходит все фильтры источника.

    Правила:
      * include — в заголовке есть хотя бы одно из слов (регистр не важен); пустой список = без ограничений;
      * exclude — в заголовке нет ни одного из слов;
      * min_price / max_price — цена в границах. Если цену на странице найти не удалось,
        объявление НЕ проходит (лучше пропустить, чем прислать клиенту то, что может не подойти).
    """
    title = item.title.lower()
    if source.include and not any(word.lower() in title for word in source.include):
        return False
    if any(word.lower() in title for word in source.exclude):
        return False
    if source.min_price is not None or source.max_price is not None:
        if item.price is None:
            return False
        if source.min_price is not None and item.price < source.min_price:
            return False
        if source.max_price is not None and item.price > source.max_price:
            return False
    return True
