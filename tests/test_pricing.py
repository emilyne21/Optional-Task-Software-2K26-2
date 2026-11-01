
import pytest
from fastapi import HTTPException


def quote_one_line(service, make_customer, make_product, build_order, *, category="regular",
                   shipping="standard", price=2500, quantity=1):
    customer = make_customer(category)
    product = make_product(price_cents=price)
    return service.quote(build_order(customer, [(product, quantity)], shipping))


# ---------- Descuentos ----------

@pytest.mark.parametrize(
    ("category", "expected_discount"),
    [("regular", 0), ("premium", 500), ("business", 750)],
)
def test_discount_depends_on_customer_category(service, make_customer, make_product, build_order,
                                               category, expected_discount):
    quote = quote_one_line(service, make_customer, make_product, build_order,
                           category=category, price=2500, quantity=2)
    assert quote["subtotal_cents"] == 5000
    assert quote["discount_cents"] == expected_discount


@pytest.mark.parametrize(
    ("category", "price", "expected_discount"),
    [
        ("premium", 999, 99),    # 99,9 se trunca a 99, no se redondea a 100
        ("business", 999, 149),  # 149,85 se trunca a 149, no se redondea a 150
        ("premium", 9, 0),       # 0,9 se trunca a 0
        ("business", 6, 0),      # 0,9 se trunca a 0
    ],
)
def test_discount_is_truncated_to_whole_cents(service, make_customer, make_product, build_order,
                                              category, price, expected_discount):
    quote = quote_one_line(service, make_customer, make_product, build_order,
                           category=category, price=price)
    assert quote["discount_cents"] == expected_discount


# ---------- Envíos ----------

@pytest.mark.parametrize(
    ("shipping", "price", "expected_shipping"),
    [
        ("pickup", 100, 0),
        ("pickup", 5000, 0),
        ("standard", 100, 1000),
        ("standard", 9999, 1000),   # justo debajo del umbral
        ("standard", 10000, 0),     # en el umbral: gratis
        ("standard", 10001, 0),
        ("express", 100, 2500),
        ("express", 50000, 2500),   # express nunca es gratis
    ],
)
def test_shipping_cost_by_type_and_threshold(service, make_customer, make_product, build_order,
                                             shipping, price, expected_shipping):
    quote = quote_one_line(service, make_customer, make_product, build_order,
                           shipping=shipping, price=price)
    assert quote["shipping_cents"] == expected_shipping


def test_free_shipping_threshold_uses_subtotal_before_discount(service, make_customer, make_product,
                                                               build_order):
    quote = quote_one_line(service, make_customer, make_product, build_order,
                           category="premium", price=10000)
    assert quote["subtotal_cents"] == 10000
    assert quote["discount_cents"] == 1000
    assert quote["shipping_cents"] == 0  # 10000 antes del descuento alcanza el umbral
    assert quote["total_cents"] == 9000


def test_subtotal_just_below_threshold_pays_shipping_even_with_discount_applied_later(
        service, make_customer, make_product, build_order):
    quote = quote_one_line(service, make_customer, make_product, build_order,
                           category="premium", price=9999)
    assert quote["discount_cents"] == 999
    assert quote["shipping_cents"] == 1000
    assert quote["total_cents"] == 9999 - 999 + 1000


# ---------- Total ----------

def test_total_is_subtotal_minus_discount_plus_shipping_over_several_lines(
        service, make_customer, make_product, build_order):
    customer = make_customer("business")
    book = make_product(price_cents=2500)
    pen = make_product(price_cents=1000)
    quote = service.quote(build_order(customer, [(book, 2), (pen, 3)], shipping="express"))
    assert quote == {
        "subtotal_cents": 8000,   # 2 x 2500 + 3 x 1000
        "discount_cents": 1200,   # 15 % de 8000
        "shipping_cents": 2500,
        "total_cents": 9300,      # 8000 - 1200 + 2500
    }


def test_readme_example_matches_expected_numbers(service, make_customer, make_product, build_order):
    quote = quote_one_line(service, make_customer, make_product, build_order,
                           category="premium", price=2500, quantity=2)
    assert quote == {"subtotal_cents": 5000, "discount_cents": 500,
                     "shipping_cents": 1000, "total_cents": 5500}


# ---------- Errores y efectos ----------

def test_quote_unknown_customer_raises_404(service, make_product, build_order):
    product = make_product()
    with pytest.raises(HTTPException) as error:
        service.quote(build_order(999, [(product, 1)]))
    assert error.value.status_code == 404
    assert error.value.detail == "Cliente no encontrado"


def test_quote_unknown_product_raises_404(service, make_customer, build_order):
    customer = make_customer()
    with pytest.raises(HTTPException) as error:
        service.quote(build_order(customer, [(999, 1)]))
    assert error.value.status_code == 404
    assert error.value.detail == "Producto 999 no encontrado"


def test_quote_does_not_create_orders_or_touch_stock(service, database, make_customer, make_product,
                                                      build_order):
    customer = make_customer("premium")
    product = make_product(price_cents=2500, stock=5)
    service.quote(build_order(customer, [(product, 2)]))
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert connection.execute("SELECT stock FROM products WHERE id = ?", (product,)).fetchone()[0] == 5


# ---------- Cotización por HTTP (integración) ----------

def api_customer_and_product(client, category="premium", price=2500):
    customer = client.post("/customers", json={"name": "Ana Torres", "email": "ana@example.com",
                                               "category": category}).json()
    product = client.post("/products", json={"name": "Libro", "price_cents": price, "stock": 5}).json()
    return customer["id"], product["id"]


def order_payload(customer_id=1, product_id=1, quantity=2, **changes):
    payload = {"customer_id": customer_id, "address": "Av. Primavera 100", "city": "Lima",
               "postal_code": "15001", "shipping": "standard",
               "items": [{"product_id": product_id, "quantity": quantity}]}
    payload.update(changes)
    return payload


def test_http_quote_returns_readme_numbers(client):
    customer_id, product_id = api_customer_and_product(client)
    response = client.post("/orders/quote", json=order_payload(customer_id, product_id))
    assert response.status_code == 200
    assert response.json() == {"subtotal_cents": 5000, "discount_cents": 500,
                               "shipping_cents": 1000, "total_cents": 5500}


def test_http_quote_defaults_to_standard_shipping_when_omitted(client):
    customer_id, product_id = api_customer_and_product(client)
    payload = order_payload(customer_id, product_id)
    del payload["shipping"]
    response = client.post("/orders/quote", json=payload)
    assert response.status_code == 200
    assert response.json()["shipping_cents"] == 1000


def test_http_quote_unknown_customer_and_product_return_404_with_detail(client):
    customer_id, product_id = api_customer_and_product(client)
    missing_customer = client.post("/orders/quote", json=order_payload(999, product_id))
    missing_product = client.post("/orders/quote", json=order_payload(customer_id, 999))
    assert missing_customer.status_code == 404
    assert missing_customer.json() == {"detail": "Cliente no encontrado"}
    assert missing_product.status_code == 404
    assert missing_product.json() == {"detail": "Producto 999 no encontrado"}


@pytest.mark.parametrize(
    "change",
    [
        {"customer_id": 0},
        {"customer_id": "1"},
        {"address": ""},
        {"address": "   "},
        {"city": ""},
        {"postal_code": ""},
        {"shipping": "drone"},
        {"items": []},
        {"items": [{"product_id": 1, "quantity": 0}]},
        {"items": [{"product_id": 1, "quantity": -1}]},
        {"items": [{"product_id": 1, "quantity": 10_001}]},
        {"items": [{"product_id": 1, "quantity": 1.5}]},
        {"items": [{"product_id": 0, "quantity": 1}]},
        {"items": [{"product_id": i, "quantity": 1} for i in range(1, 102)]},
        {"campo_extra": "x"},
    ],
    ids=["customer_zero", "customer_string", "address_empty", "address_blank", "city_empty",
         "postal_empty", "shipping_unknown", "no_items", "quantity_zero", "quantity_negative",
         "quantity_over_limit", "quantity_float", "product_zero", "over_100_lines", "extra_field"],
)
def test_http_quote_rejects_invalid_input_with_422(client, change):
    response = client.post("/orders/quote", json=order_payload(**change))
    assert response.status_code == 422