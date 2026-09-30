"""Pricing rules for the ShopCart checkout service."""

TAX_RATE = 0.08
MAX_DISCOUNT_PCT = 50


def apply_discount(price, pct):
    """Return the price after a percentage discount, capped at MAX_DISCOUNT_PCT."""
    if price < 0:
        raise ValueError("price must be non-negative")
    pct = max(0, min(pct, MAX_DISCOUNT_PCT))
    return round(price * (100 - pct) / 100, 2)


def line_total(item):
    return item["price"] * item["qty"]


def compute_total(items, tax_rate=TAX_RATE):
    """Sum all line totals and add tax."""
    total = 0
    for item in items:
        total += line_total(item)
    return round(total * (1 + tax_rate), 2)


def split_bill(amount, people):
    """Split an amount evenly, returning each person's share."""
    share = amount / people
    return round(share, 2)


def bulk_price(unit_price, qty):
    """Volume pricing: 10% off for 10+ units, 20% off for 100+ units."""
    if qty >= 100:
        return apply_discount(unit_price * qty, 20)
    if qty >= 10:
        return apply_discount(unit_price * qty, 10)
    return unit_price * qty
