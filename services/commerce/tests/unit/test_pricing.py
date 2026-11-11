import pytest

from app.service.pricing import first_purchase_discount


@pytest.mark.parametrize(
    ("price_cents", "pct", "expected"),
    [(2500, 20, 500), (4, 20, 0), (999, 20, 199), (100, 99, 99), (0, 20, 0)],
    ids=["beginner kit", "rounds down to zero", "integer division", "largest percent", "free item"],
)
def test_discount_uses_integer_division(price_cents: int, pct: int, expected: int):
    assert first_purchase_discount(price_cents, pct) == expected
