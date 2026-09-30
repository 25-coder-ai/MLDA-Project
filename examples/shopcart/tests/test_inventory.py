import pytest

from shop.inventory import low_stock, parse_quantity, restock


def test_parse_quantity_strips_whitespace():
    assert parse_quantity(" 7 ") == 7


def test_parse_quantity_rejects_zero():
    with pytest.raises(ValueError):
        parse_quantity("0")


def test_parse_quantity_rejects_garbage():
    with pytest.raises(ValueError):
        parse_quantity("lots")


def test_restock_adds_units():
    assert restock({"A": 1}, "A", "4") == {"A": 5}


def test_low_stock_lists_skus_below_threshold():
    assert low_stock({"A": 1, "B": 9, "C": 4}) == ["A", "C"]
