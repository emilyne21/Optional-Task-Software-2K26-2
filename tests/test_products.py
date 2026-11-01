"""CRUD de productos por HTTP (integración API + persistencia)."""
import pytest


def product_body(**changes):
    body = {"name": "Libro de algoritmos", "price_cents": 2500, "stock": 5}
    body.update(changes)
    return body


# ---------- Crear ----------

def test_create_product_returns_201_and_the_saved_record(client):
    response = client.post("/products", json=product_body())
    assert response.status_code == 201
    assert response.json() == {"id": 1, "name": "Libro de algoritmos", "price_cents": 2500, "stock": 5}


def test_create_product_strips_whitespace_around_name(client):
    response = client.post("/products", json=product_body(name="  Libro  "))
    assert response.json()["name"] == "Libro"


@pytest.mark.parametrize(
    "change",
    [
        {"price_cents": 1, "stock": 0},
        {"price_cents": 100_000_000, "stock": 1_000_000},
    ],
    ids=["lower_bounds", "upper_bounds"],
)
def test_create_product_accepts_values_at_the_limits(client, change):
    response = client.post("/products", json=product_body(**change))
    assert response.status_code == 201
    assert response.json()["price_cents"] == change["price_cents"]
    assert response.json()["stock"] == change["stock"]


@pytest.mark.parametrize(
    "change",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 121},
        {"price_cents": 0},
        {"price_cents": -5},
        {"price_cents": 100_000_001},
        {"price_cents": "2500"},
        {"price_cents": 25.5},
        {"stock": -1},
        {"stock": 1_000_001},
        {"stock": "5"},
        {"campo_extra": "x"},
    ],
    ids=["name_empty", "name_blank", "name_too_long", "price_zero", "price_negative",
         "price_over_limit", "price_string", "price_float", "stock_negative", "stock_over_limit",
         "stock_string", "extra_field"],
)
def test_create_product_rejects_invalid_input_and_saves_nothing(client, change):
    response = client.post("/products", json=product_body(**change))
    assert response.status_code == 422
    assert client.get("/products").json() == []


def test_create_product_without_price_returns_422(client):
    body = product_body()
    del body["price_cents"]
    assert client.post("/products", json=body).status_code == 422


# ---------- Listar y consultar ----------

def test_list_products_is_empty_on_a_fresh_database(client):
    response = client.get("/products")
    assert response.status_code == 200
    assert response.json() == []


def test_list_products_is_ordered_by_id(client, new_product):
    created = [new_product(price_cents=1000 * (i + 1)) for i in range(3)]
    listed = client.get("/products").json()
    assert [row["id"] for row in listed] == [1, 2, 3]
    assert [row["price_cents"] for row in listed] == [row["price_cents"] for row in created]


def test_get_product_returns_the_record(client, new_product):
    created = new_product(price_cents=1800, stock=7)
    response = client.get(f"/products/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_get_unknown_product_returns_404(client):
    response = client.get("/products/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Producto no encontrado"}


# ---------- Actualizar ----------

def test_update_product_replaces_all_fields_and_persists(client, new_product):
    created = new_product()
    body = product_body(name="Libro 2da edición", price_cents=3200, stock=9)
    response = client.put(f"/products/{created['id']}", json=body)
    expected = {"id": created["id"], **body}
    assert response.status_code == 200
    assert response.json() == expected
    assert client.get(f"/products/{created['id']}").json() == expected


def test_update_unknown_product_returns_404(client):
    response = client.put("/products/999", json=product_body())
    assert response.status_code == 404
    assert response.json() == {"detail": "Producto no encontrado"}


def test_update_product_with_invalid_body_returns_422_and_changes_nothing(client, new_product):
    created = new_product()
    response = client.put(f"/products/{created['id']}", json=product_body(price_cents=0))
    assert response.status_code == 422
    assert client.get(f"/products/{created['id']}").json() == created


# ---------- Eliminar ----------

def test_delete_product_returns_204_without_body_and_removes_it(client, new_product):
    created = new_product()
    response = client.delete(f"/products/{created['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/products/{created['id']}").status_code == 404
    assert client.get("/products").json() == []


def test_delete_unknown_product_returns_404(client):
    response = client.delete("/products/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Producto no encontrado"}


def test_delete_product_in_an_order_returns_409_and_keeps_it(client, new_customer, new_product, new_order):
    product = new_product()
    new_order(new_customer()["id"], product["id"])
    response = client.delete(f"/products/{product['id']}")
    assert response.status_code == 409
    assert response.json() == {"detail": "El producto pertenece a un pedido"}
    assert client.get(f"/products/{product['id']}").json() == product


def test_product_can_be_deleted_once_its_pending_order_is_deleted(client, new_customer, new_product,
                                                                   new_order):
    product = new_product()
    order = new_order(new_customer()["id"], product["id"])
    assert client.delete(f"/orders/{order['id']}").status_code == 204
    assert client.delete(f"/products/{product['id']}").status_code == 204