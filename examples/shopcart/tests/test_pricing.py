import pytest

from shop.pricing import apply_discount, bulk_price, compute_total, split_bill


def test_apply_discount_basic():
    assert apply_discount(100, 20) == 80


def test_apply_discount_is_capped():
    assert apply_discount(100, 80) == 50


def test_apply_discount_rejects_negative_price():
    with pytest.raises(ValueError):
        apply_discount(-1, 10)


def test_compute_total_adds_tax():
    assert compute_total([{"price": 10, "qty": 2}], tax_rate=0.1) == 22.0


def test_compute_total_empty_cart():
    assert compute_total([]) == 0


def test_split_bill_even():
    assert split_bill(100, 4) == 25


def test_split_bill_rounding():
    assert split_bill(10, 3) == 3.33


def test_split_bill_zero_people_raises():
    with pytest.raises(ValueError):
        split_bill(100, 0)


def test_bulk_price_small_order():
    assert bulk_price(2, 5) == 10


def test_bulk_price_ten_units():
    assert bulk_price(10, 10) == 90
