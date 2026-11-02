"""Reglas de precio de un pedido. Son funciones puras, sin base de datos."""


def calculate_discount(category, subtotal):
    if category == "premium":
        return subtotal * 10 // 100
    if category == "business":
        return subtotal * 15 // 100
    return 0


def calculate_shipping(shipping, subtotal):
    if shipping == "pickup":
        return 0
    if shipping == "standard":
        return 0 if subtotal >= 10_000 else 1_000
    return 2_500


def calculate_totals(category, shipping, subtotal):
    discount = calculate_discount(category, subtotal)
    delivery = calculate_shipping(shipping, subtotal)
    return {"subtotal_cents": subtotal, "discount_cents": discount,
            "shipping_cents": delivery, "total_cents": subtotal - discount + delivery}