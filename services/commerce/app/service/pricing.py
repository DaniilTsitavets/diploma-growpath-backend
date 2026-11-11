def first_purchase_discount(list_price_cents: int, discount_pct: int) -> int:
    """Discount in cents; integer division, so tiny prices may round down to zero."""
    return list_price_cents * discount_pct // 100
