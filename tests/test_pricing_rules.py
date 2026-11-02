"""Reglas de precio probadas como funciones puras: sin base de datos ni HTTP."""
import pytest

from app.pricing import calculate_discount, calculate_shipping, calculate_totals


@pytest.mark.parametrize(
    ("category", "subtotal", "expected"),
    [
        ("regular", 5000, 0),
        ("premium", 5000, 500),
        ("business", 5000, 750),
        ("premium", 999, 99),    # 99,9 se trunca a 99
        ("business", 999, 149),  # 149,85 se trunca a 149
        ("business", 6, 0),      # 0,9 se trunca a 0
    ],
)
def test_calculate_discount(category, subtotal, expected):
    assert calculate_discount(category, subtotal) == expected


@pytest.mark.parametrize(
    ("shipping", "subtotal", "expected"),
    [
        ("pickup", 100, 0),
        ("standard", 9999, 1000),
        ("standard", 10000, 0),   # el umbral incluye 10000
        ("standard", 10001, 0),
        ("express", 100, 2500),
        ("express", 50000, 2500),
    ],
)
def test_calculate_shipping(shipping, subtotal, expected):
    assert calculate_shipping(shipping, subtotal) == expected


def test_calculate_totals_matches_the_readme_example():
    assert calculate_totals("premium", "standard", 5000) == {
        "subtotal_cents": 5000, "discount_cents": 500, "shipping_cents": 1000, "total_cents": 5500}


def test_free_shipping_threshold_uses_subtotal_before_discount():
    totals = calculate_totals("premium", "standard", 10000)
    assert totals["discount_cents"] == 1000
    assert totals["shipping_cents"] == 0
    assert totals["total_cents"] == 9000