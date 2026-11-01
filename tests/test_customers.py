"""CRUD de clientes por HTTP (integración API + persistencia)."""
import pytest


def customer_body(**changes):
    body = {"name": "Ana Torres", "email": "ana@example.com", "category": "premium"}
    body.update(changes)
    return body


# ---------- Crear ----------

def test_create_customer_returns_201_and_the_saved_record(client):
    response = client.post("/customers", json=customer_body())
    assert response.status_code == 201
    assert response.json() == {"id": 1, "name": "Ana Torres", "email": "ana@example.com",
                               "category": "premium"}


def test_create_customer_defaults_to_regular_category(client):
    body = customer_body()
    del body["category"]
    response = client.post("/customers", json=body)
    assert response.status_code == 201
    assert response.json()["category"] == "regular"


def test_create_customer_strips_whitespace_around_name(client):
    response = client.post("/customers", json=customer_body(name="  Ana Torres  "))
    assert response.json()["name"] == "Ana Torres"


def test_create_customer_with_duplicate_email_returns_409_and_keeps_one_record(client):
    client.post("/customers", json=customer_body())
    duplicate = client.post("/customers", json=customer_body(name="Otra Ana"))
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "El correo ya está registrado"}
    assert len(client.get("/customers").json()) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 121},
        {"email": "no-es-un-correo"},
        {"email": ""},
        {"category": "vip"},
        {"campo_extra": "x"},
    ],
    ids=["name_empty", "name_blank", "name_too_long", "email_invalid", "email_empty",
         "category_unknown", "extra_field"],
)
def test_create_customer_rejects_invalid_input_and_saves_nothing(client, change):
    response = client.post("/customers", json=customer_body(**change))
    assert response.status_code == 422
    assert client.get("/customers").json() == []


def test_create_customer_without_email_returns_422(client):
    body = customer_body()
    del body["email"]
    assert client.post("/customers", json=body).status_code == 422


# ---------- Listar y consultar ----------

def test_list_customers_is_empty_on_a_fresh_database(client):
    response = client.get("/customers")
    assert response.status_code == 200
    assert response.json() == []


def test_list_customers_is_ordered_by_id(client, new_customer):
    created = [new_customer() for _ in range(3)]
    listed = client.get("/customers").json()
    assert [row["id"] for row in listed] == [1, 2, 3]
    assert [row["email"] for row in listed] == [row["email"] for row in created]


def test_get_customer_returns_the_record(client, new_customer):
    created = new_customer("business")
    response = client.get(f"/customers/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_get_unknown_customer_returns_404(client):
    response = client.get("/customers/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Cliente no encontrado"}


def test_get_customer_with_non_numeric_id_returns_422(client):
    assert client.get("/customers/abc").status_code == 422


# ---------- Actualizar ----------

def test_update_customer_replaces_all_fields_and_persists(client, new_customer):
    created = new_customer()
    body = customer_body(name="Ana María", email="ana.maria@example.com", category="business")
    response = client.put(f"/customers/{created['id']}", json=body)
    expected = {"id": created["id"], **body}
    assert response.status_code == 200
    assert response.json() == expected
    assert client.get(f"/customers/{created['id']}").json() == expected


def test_update_customer_can_keep_its_own_email(client, new_customer):
    created = new_customer()
    response = client.put(f"/customers/{created['id']}",
                          json=customer_body(email=created["email"], category="premium"))
    assert response.status_code == 200
    assert response.json()["category"] == "premium"


def test_update_customer_with_email_of_another_returns_409_and_changes_nothing(client, new_customer):
    first = new_customer()
    second = new_customer()
    response = client.put(f"/customers/{second['id']}",
                          json=customer_body(name="Cambiado", email=first["email"]))
    assert response.status_code == 409
    assert response.json() == {"detail": "El correo ya está registrado"}
    assert client.get(f"/customers/{second['id']}").json() == second


def test_update_unknown_customer_returns_404(client):
    response = client.put("/customers/999", json=customer_body())
    assert response.status_code == 404
    assert response.json() == {"detail": "Cliente no encontrado"}


def test_update_customer_with_invalid_body_returns_422_and_changes_nothing(client, new_customer):
    created = new_customer()
    response = client.put(f"/customers/{created['id']}", json=customer_body(category="vip"))
    assert response.status_code == 422
    assert client.get(f"/customers/{created['id']}").json() == created


# ---------- Eliminar ----------

def test_delete_customer_returns_204_without_body_and_removes_it(client, new_customer):
    created = new_customer()
    response = client.delete(f"/customers/{created['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/customers/{created['id']}").status_code == 404
    assert client.get("/customers").json() == []


def test_delete_unknown_customer_returns_404(client):
    response = client.delete("/customers/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Cliente no encontrado"}


def test_delete_customer_with_orders_returns_409_and_keeps_it(client, new_customer, new_product, new_order):
    customer = new_customer()
    new_order(customer["id"], new_product()["id"])
    response = client.delete(f"/customers/{customer['id']}")
    assert response.status_code == 409
    assert response.json() == {"detail": "El cliente tiene pedidos"}
    assert client.get(f"/customers/{customer['id']}").json() == customer


def test_customer_can_be_deleted_once_its_pending_order_is_deleted(client, new_customer, new_product,
                                                                    new_order):
    customer = new_customer()
    order = new_order(customer["id"], new_product()["id"])
    assert client.delete(f"/orders/{order['id']}").status_code == 204
    assert client.delete(f"/customers/{customer['id']}").status_code == 204