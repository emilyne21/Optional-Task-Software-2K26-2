"""Ciclo de vida de los pedidos: crear, consultar, editar, cancelar, eliminar y confirmar.

Incluye pruebas unitarias del modelo OrderInput y pruebas de integración por HTTP.
Los valores esperados salen de las reglas del README, calculados a mano.
"""
import pytest
from pydantic import ValidationError

from app.orders import ItemInput, OrderInput


def order_body(customer_id, product_id, quantity=2, shipping="standard"):
    return {"customer_id": customer_id, "address": "Av. Primavera 100", "city": "Lima",
            "postal_code": "15001", "shipping": shipping,
            "items": [{"product_id": product_id, "quantity": quantity}]}


def totals(order):
    return (order["subtotal_cents"], order["discount_cents"], order["shipping_cents"], order["total_cents"])


@pytest.fixture
def order_in_state(client, new_customer, new_product, new_order):
    """Crea un pedido (cliente regular, 2 unidades, stock 5) y lo deja pending, confirmed o cancelled."""

    def factory(state):
        customer = new_customer()
        product = new_product(stock=5)
        order = new_order(customer["id"], product["id"], quantity=2)
        if state == "confirmed":
            assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
        elif state == "cancelled":
            assert client.post(f"/orders/{order['id']}/cancel").status_code == 200
        return client.get(f"/orders/{order['id']}").json()

    return factory


# ---------- Modelo OrderInput (unitarias, sin base de datos) ----------

def test_order_input_rejects_repeated_products():
    with pytest.raises(ValidationError) as error:
        OrderInput(customer_id=1, address="Calle 1", city="Lima", postal_code="15001",
                   items=[ItemInput(product_id=1, quantity=1), ItemInput(product_id=1, quantity=2)])
    assert "No repita productos" in str(error.value)


def test_order_input_defaults_to_standard_shipping_and_strips_text():
    order = OrderInput(customer_id=1, address="  Calle 1  ", city=" Lima ", postal_code=" 15001 ",
                       items=[ItemInput(product_id=1, quantity=1)])
    assert order.shipping == "standard"
    assert (order.address, order.city, order.postal_code) == ("Calle 1", "Lima", "15001")


# ---------- Crear ----------

def test_create_order_stores_pending_status_totals_and_lines(client, new_customer, new_product, new_order):
    customer = new_customer("premium")
    product = new_product(price_cents=2500, stock=5)
    order = new_order(customer["id"], product["id"], quantity=2)
    assert order["id"] == 1
    assert order["status"] == "pending"
    assert order["customer_id"] == customer["id"]
    assert order["customer_category"] == "premium"
    assert (order["address"], order["city"], order["postal_code"], order["shipping"]) == (
        "Av. Primavera 100", "Lima", "15001", "standard")
    assert totals(order) == (5000, 500, 1000, 5500)
    assert order["items"] == [{"product_id": product["id"], "quantity": 2, "unit_price_cents": 2500}]


def test_create_pickup_order_has_free_shipping(client, new_customer, new_product, new_order):
    customer = new_customer("premium")
    product = new_product(price_cents=12000)
    order = new_order(customer["id"], product["id"], quantity=1, shipping="pickup")
    assert totals(order) == (12000, 1200, 0, 10800)


@pytest.mark.parametrize(
    ("category", "shipping", "price"),
    [
        ("regular", "pickup", 5000),
        ("regular", "standard", 100),
        ("premium", "express", 3000),
        ("business", "standard", 9999),
        ("business", "standard", 10000),
    ],
)
def test_created_order_totals_match_the_quote(client, new_customer, new_product, category, shipping, price):
    customer = new_customer(category)
    product = new_product(price_cents=price, stock=5)
    body = order_body(customer["id"], product["id"], quantity=1, shipping=shipping)
    quote = client.post("/orders/quote", json=body).json()
    order = client.post("/orders", json=body).json()
    assert totals(order) == (quote["subtotal_cents"], quote["discount_cents"],
                             quote["shipping_cents"], quote["total_cents"])


def test_create_order_does_not_reserve_stock_even_if_quantity_exceeds_it(client, new_customer,
                                                                          new_product, new_order):
    customer = new_customer()
    product = new_product(stock=1)
    order = new_order(customer["id"], product["id"], quantity=3)
    assert order["status"] == "pending"
    assert client.get(f"/products/{product['id']}").json()["stock"] == 1


def test_create_order_with_unknown_customer_returns_404_and_saves_nothing(client, new_product):
    product = new_product()
    response = client.post("/orders", json=order_body(999, product["id"]))
    assert response.status_code == 404
    assert response.json() == {"detail": "Cliente no encontrado"}
    assert client.get("/orders").json() == []


def test_create_order_with_one_unknown_product_returns_404_and_saves_nothing(client, new_customer,
                                                                              new_product):
    customer = new_customer()
    product = new_product()
    body = order_body(customer["id"], product["id"])
    body["items"].append({"product_id": 999, "quantity": 1})
    response = client.post("/orders", json=body)
    assert response.status_code == 404
    assert response.json() == {"detail": "Producto 999 no encontrado"}
    assert client.get("/orders").json() == []


def test_order_keeps_category_and_totals_when_the_customer_changes_later(client, new_customer, new_product,
                                                                          new_order):
    customer = new_customer("premium")
    order = new_order(customer["id"], new_product(price_cents=2500)["id"], quantity=2)
    changed = client.put(f"/customers/{customer['id']}",
                         json={"name": customer["name"], "email": customer["email"], "category": "regular"})
    assert changed.status_code == 200
    stored = client.get(f"/orders/{order['id']}").json()
    assert stored["customer_category"] == "premium"
    assert totals(stored) == (5000, 500, 1000, 5500)


# ---------- Listar y consultar ----------

def test_list_orders_is_empty_on_a_fresh_database(client):
    response = client.get("/orders")
    assert response.status_code == 200
    assert response.json() == []


def test_list_orders_returns_summaries_ordered_by_id_and_detail_includes_lines(client, new_customer,
                                                                                new_product, new_order):
    customer = new_customer()
    product = new_product(price_cents=2500, stock=50)
    for quantity in (1, 2, 3):
        new_order(customer["id"], product["id"], quantity=quantity)
    listed = client.get("/orders").json()
    assert [row["id"] for row in listed] == [1, 2, 3]
    assert [row["subtotal_cents"] for row in listed] == [2500, 5000, 7500]
    assert all("items" not in row for row in listed)
    detail = client.get("/orders/2").json()
    assert detail["items"] == [{"product_id": product["id"], "quantity": 2, "unit_price_cents": 2500}]


def test_get_unknown_order_returns_404(client):
    response = client.get("/orders/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Pedido no encontrado"}


# ---------- Editar (PUT) ----------

def test_update_pending_order_recalculates_with_current_price_and_category(client, new_customer,
                                                                            new_product, new_order):
    customer = new_customer("premium")
    product = new_product(price_cents=2500, stock=5)
    order = new_order(customer["id"], product["id"], quantity=2)
    assert totals(order) == (5000, 500, 1000, 5500)
    client.put(f"/customers/{customer['id']}",
               json={"name": customer["name"], "email": customer["email"], "category": "business"})
    client.put(f"/products/{product['id']}", json={"name": "Libro", "price_cents": 4000, "stock": 5})

    response = client.put(f"/orders/{order['id']}", json=order_body(customer["id"], product["id"], 2))

    assert response.status_code == 200
    updated = response.json()
    assert updated["id"] == order["id"]
    assert updated["status"] == "pending"
    assert updated["customer_category"] == "business"
    assert totals(updated) == (8000, 1200, 1000, 7800)  # 2 x 4000, 15 %, envío estándar
    assert updated["items"] == [{"product_id": product["id"], "quantity": 2, "unit_price_cents": 4000}]
    assert client.get(f"/orders/{order['id']}").json() == updated
    assert len(client.get("/orders").json()) == 1


def test_update_order_replaces_customer_address_shipping_and_lines(client, new_customer, new_product,
                                                                   new_order):
    first = new_customer("regular")
    second = new_customer("business")
    book = new_product(price_cents=2500)
    pen = new_product(price_cents=1000)
    order = new_order(first["id"], book["id"], quantity=2)
    body = {"customer_id": second["id"], "address": "Jr. Lima 50", "city": "Cusco",
            "postal_code": "08000", "shipping": "express",
            "items": [{"product_id": pen["id"], "quantity": 3}]}

    updated = client.put(f"/orders/{order['id']}", json=body).json()

    assert updated["customer_id"] == second["id"]
    assert updated["customer_category"] == "business"
    assert (updated["address"], updated["city"], updated["postal_code"], updated["shipping"]) == (
        "Jr. Lima 50", "Cusco", "08000", "express")
    assert updated["items"] == [{"product_id": pen["id"], "quantity": 3, "unit_price_cents": 1000}]
    assert totals(updated) == (3000, 450, 2500, 5050)  # 3 x 1000, 15 %, express


def test_update_unknown_order_returns_404(client, new_customer, new_product):
    response = client.put("/orders/999", json=order_body(new_customer()["id"], new_product()["id"]))
    assert response.status_code == 404
    assert response.json() == {"detail": "Pedido no encontrado"}


@pytest.mark.parametrize(
    ("customer_offset", "product_offset", "detail"),
    [(1000, 0, "Cliente no encontrado"), (0, 1000, "Producto 1001 no encontrado")],
    ids=["unknown_customer", "unknown_product"],
)
def test_update_with_unknown_reference_returns_404_and_keeps_the_order(
        client, new_customer, new_product, new_order, customer_offset, product_offset, detail):
    customer = new_customer("premium")
    product = new_product()
    order = new_order(customer["id"], product["id"])
    body = order_body(customer["id"] + customer_offset, product["id"] + product_offset)
    response = client.put(f"/orders/{order['id']}", json=body)
    assert response.status_code == 404
    assert response.json() == {"detail": detail}
    assert client.get(f"/orders/{order['id']}").json() == order


def test_update_with_invalid_body_returns_422_and_keeps_the_order(client, new_customer, new_product,
                                                                   new_order):
    customer = new_customer()
    product = new_product()
    order = new_order(customer["id"], product["id"])
    response = client.put(f"/orders/{order['id']}", json=order_body(customer["id"], product["id"], 0))
    assert response.status_code == 422
    assert client.get(f"/orders/{order['id']}").json() == order


# ---------- Cancelar ----------

def test_cancel_pending_order_marks_it_cancelled_and_leaves_stock_untouched(client, new_customer,
                                                                             new_product, new_order):
    customer = new_customer()
    product = new_product(stock=5)
    order = new_order(customer["id"], product["id"])
    response = client.post(f"/orders/{order['id']}/cancel")
    assert response.status_code == 200
    assert response.json() == {"id": order["id"], "status": "cancelled"}
    assert client.get(f"/orders/{order['id']}").json()["status"] == "cancelled"
    assert client.get(f"/products/{product['id']}").json()["stock"] == 5
    assert client.get(f"/orders/{order['id']}/notifications").json() == []


def test_cancel_unknown_order_returns_404(client):
    response = client.post("/orders/999/cancel")
    assert response.status_code == 404
    assert response.json() == {"detail": "Pedido no encontrado"}


# ---------- Eliminar ----------

def test_delete_pending_order_returns_204_and_removes_it(client, new_customer, new_product, new_order):
    order = new_order(new_customer()["id"], new_product()["id"])
    response = client.delete(f"/orders/{order['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/orders/{order['id']}").status_code == 404
    assert client.get("/orders").json() == []


def test_delete_unknown_order_returns_404(client):
    response = client.delete("/orders/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Pedido no encontrado"}


# ---------- Confirmar ----------

def test_confirm_deducts_stock_of_every_line_and_records_one_notification(client, new_customer,
                                                                           new_product):
    customer = new_customer(email="ana@example.com")
    book = new_product(price_cents=2500, stock=5)
    pen = new_product(price_cents=1000, stock=10)
    body = order_body(customer["id"], book["id"], quantity=2)
    body["items"].append({"product_id": pen["id"], "quantity": 4})
    order = client.post("/orders", json=body).json()
    assert totals(order) == (9000, 0, 1000, 10000)

    response = client.post(f"/orders/{order['id']}/confirm")

    assert response.status_code == 200
    assert response.json() == {"id": 1, "status": "confirmed",
                               "message": "Pedido 1 confirmado. Total: 10000 centavos."}
    assert client.get(f"/orders/{order['id']}").json()["status"] == "confirmed"
    assert client.get(f"/products/{book['id']}").json()["stock"] == 3
    assert client.get(f"/products/{pen['id']}").json()["stock"] == 6
    notifications = client.get(f"/orders/{order['id']}/notifications").json()
    assert notifications == [{"id": 1, "order_id": 1, "recipient": "ana@example.com",
                              "message": "Pedido 1 confirmado. Total: 10000 centavos."}]


def test_confirm_succeeds_when_stock_equals_quantity(client, new_customer, new_product, new_order):
    product = new_product(stock=2)
    order = new_order(new_customer()["id"], product["id"], quantity=2)
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    assert client.get(f"/products/{product['id']}").json()["stock"] == 0


def test_confirm_with_insufficient_stock_returns_409_and_works_after_restocking(client, new_customer,
                                                                                 new_product, new_order):
    product = new_product(stock=1)
    order = new_order(new_customer()["id"], product["id"], quantity=2)

    failed = client.post(f"/orders/{order['id']}/confirm")

    assert failed.status_code == 409
    assert failed.json() == {"detail": f"Stock insuficiente para producto {product['id']}"}
    assert client.get(f"/orders/{order['id']}").json()["status"] == "pending"
    assert client.get(f"/products/{product['id']}").json()["stock"] == 1

    client.put(f"/products/{product['id']}", json={"name": "Libro", "price_cents": 2500, "stock": 5})
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    assert client.get(f"/products/{product['id']}").json()["stock"] == 3


def test_second_order_cannot_confirm_when_the_first_used_the_stock(client, new_customer, new_product,
                                                                    new_order):
    customer = new_customer()
    product = new_product(stock=3)
    first = new_order(customer["id"], product["id"], quantity=2)
    second = new_order(customer["id"], product["id"], quantity=2)

    assert client.post(f"/orders/{first['id']}/confirm").status_code == 200
    rejected = client.post(f"/orders/{second['id']}/confirm")

    assert rejected.status_code == 409
    assert client.get(f"/orders/{second['id']}").json()["status"] == "pending"
    assert client.get(f"/products/{product['id']}").json()["stock"] == 1
    assert client.get(f"/orders/{second['id']}/notifications").json() == []


def test_confirm_unknown_order_returns_404(client):
    response = client.post("/orders/999/confirm")
    assert response.status_code == 404
    assert response.json() == {"detail": "Pedido no encontrado"}


# ---------- Estados finales ----------

FINAL_STATE_ACTIONS = {
    "edit": "Solo puede editar pedidos pendientes",
    "delete": "Solo puede eliminar pedidos pendientes",
    "cancel": "Solo puede cancelar pedidos pendientes",
    "confirm": "El pedido ya no está pendiente",
}


def run_action(client, action, order):
    order_id = order["id"]
    if action == "edit":
        body = order_body(order["customer_id"], order["items"][0]["product_id"])
        return client.put(f"/orders/{order_id}", json=body)
    if action == "delete":
        return client.delete(f"/orders/{order_id}")
    return client.post(f"/orders/{order_id}/{action}")


@pytest.mark.parametrize("state", ["confirmed", "cancelled"])
@pytest.mark.parametrize("action", list(FINAL_STATE_ACTIONS))
def test_confirmed_and_cancelled_orders_are_final(client, order_in_state, state, action):
    order = order_in_state(state)
    stock_before = client.get(f"/products/{order['items'][0]['product_id']}").json()["stock"]

    response = run_action(client, action, order)

    assert response.status_code == 409
    assert response.json() == {"detail": FINAL_STATE_ACTIONS[action]}
    assert client.get(f"/orders/{order['id']}").json() == order
    assert client.get(f"/products/{order['items'][0]['product_id']}").json()["stock"] == stock_before


# ---------- Notificaciones ----------

def test_notifications_of_unknown_order_return_404(client):
    response = client.get("/orders/999/notifications")
    assert response.status_code == 404
    assert response.json() == {"detail": "Pedido no encontrado"}


def test_pending_order_has_no_notifications(client, order_in_state):
    order = order_in_state("pending")
    response = client.get(f"/orders/{order['id']}/notifications")
    assert response.status_code == 200
    assert response.json() == []