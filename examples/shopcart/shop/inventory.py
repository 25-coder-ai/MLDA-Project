"""Stock keeping."""


def parse_quantity(raw):
    """Parse a user-supplied quantity into a positive int."""
    try:
        qty = int(str(raw).strip())
    except ValueError:
        raise ValueError(f"invalid quantity: {raw!r}")
    if qty <= 0:
        raise ValueError("quantity must be positive")
    return qty


def restock(stock, sku, qty):
    stock[sku] = stock.get(sku, 0) + parse_quantity(qty)
    return stock


def low_stock(stock, threshold=5):
    return sorted(sku for sku, count in stock.items() if count < threshold)


def reserve(stock, sku, qty):
    """Reserve qty units of sku and return the remaining count."""
    available = stock.get(sku, 0)
    if qty > available:
        raise ValueError("insufficient stock")
    stock[sku] = available - qty
    return stock[sku]
