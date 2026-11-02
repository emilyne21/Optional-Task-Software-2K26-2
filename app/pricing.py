"""Reglas de precio de un pedido. Son funciones puras, sin base de datos."""

# Porcentaje de descuento sobre el subtotal. El resultado se trunca a centavos enteros.
DISCOUNT_PERCENT_BY_CATEGORY = {"regular": 0, "premium": 10, "business": 15}

# Tarifa de envío en centavos por tipo de envío.
SHIPPING_FEE_CENTS = {"pickup": 0, "standard": 1_000, "express": 2_500}

# El envío standard es gratis desde este subtotal, antes del descuento.
FREE_STANDARD_SHIPPING_FROM_CENTS = 10_000


def calculate_discount(category, subtotal):
    return subtotal * DISCOUNT_PERCENT_BY_CATEGORY[category] // 100


def calculate_shipping(shipping, subtotal):
    if shipping == "standard" and subtotal >= FREE_STANDARD_SHIPPING_FROM_CENTS:
        return 0
    return SHIPPING_FEE_CENTS[shipping]


def calculate_totals(category, shipping, subtotal):
    discount = calculate_discount(category, subtotal)
    delivery = calculate_shipping(shipping, subtotal)
    return {"subtotal_cents": subtotal, "discount_cents": discount,
            "shipping_cents": delivery, "total_cents": subtotal - discount + delivery}