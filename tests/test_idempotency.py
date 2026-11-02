"""Adicional 1: idempotencia de POST /orders con el encabezado Idempotency-Key.

Cada sección corresponde a un criterio de aceptación del README.
"""
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.idempotency import request_fingerprint
from app.order_repository import OrderRepository
from app.orders import ItemInput, OrderInput
from main import create_app

KEY = "pedido-de-ana-001"


def order_body(customer_id, product_id, quantity=1, **changes):
    body = {"customer_id": customer_id, "address": "Av. Primavera 100", "city": "Lima",
            "postal_code": "15001", "shipping": "standard",
            "items": [{"product_id": product_id, "quantity": quantity}]}
    body.update(changes)
    return body


def post_order(client, body, key=KEY):
    headers = {} if key is None else {"Idempotency-Key": key}
    return client.post("/orders", json=body, headers=headers)


def stored_keys(client):
    with client.app.state.database.connect() as connection:
        return [row["key"] for row in connection.execute("SELECT key FROM idempotency_keys ORDER BY key")]


@pytest.fixture
def body(new_customer, new_product):
    """Cuerpo válido de pedido, con un cliente premium y un producto con stock."""
    return order_body(new_customer("premium")["id"], new_product(price_cents=2500, stock=10)["id"])


def fingerprint_of(**changes):
    values = {"customer_id": 1, "address": "Av. Primavera 100", "city": "Lima", "postal_code": "15001",
              "shipping": "standard", "items": [ItemInput(product_id=1, quantity=2)]}
    values.update(changes)
    return request_fingerprint(OrderInput(**values))


# ---------- huella del contenido ----------

def test_same_content_gives_the_same_fingerprint():
    assert fingerprint_of() == fingerprint_of()
    assert len(fingerprint_of()) == 64


@pytest.mark.parametrize("changes", [
    {"customer_id": 2},
    {"address": "Av. Otra 200"},
    {"city": "Cusco"},
    {"postal_code": "15002"},
    {"shipping": "express"},
    {"items": [ItemInput(product_id=1, quantity=3)]},
    {"items": [ItemInput(product_id=2, quantity=2)]},
    {"items": [ItemInput(product_id=1, quantity=2), ItemInput(product_id=2, quantity=1)]},
])
def test_any_change_in_the_content_changes_the_fingerprint(changes):
    assert fingerprint_of(**changes) != fingerprint_of()


def test_fingerprint_ignores_the_spaces_that_validation_trims():
    assert fingerprint_of(address="  Av. Primavera 100  ", city=" Lima ") == fingerprint_of()


def test_fingerprint_depends_on_the_order_of_the_lines():
    first = [ItemInput(product_id=1, quantity=1), ItemInput(product_id=2, quantity=1)]
    assert fingerprint_of(items=first) != fingerprint_of(items=list(reversed(first)))


# ---------- criterio 1: misma clave y mismo contenido ----------

def test_same_key_and_same_content_return_the_original_order_without_creating_another(client, body):
    first = post_order(client, body)
    second = post_order(client, body)
    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json() == first.json()
    assert len(client.get("/orders").json()) == 1
    assert len(client.get(f"/orders/{first.json()['id']}").json()["items"]) == 1


def test_only_the_repeated_response_carries_the_replay_header(client, body):
    first = post_order(client, body)
    second = post_order(client, body)
    assert "idempotency-replayed" not in first.headers
    assert second.headers["idempotency-replayed"] == "true"


def test_the_key_can_be_repeated_many_times(client, body):
    ids = {post_order(client, body).json()["id"] for _ in range(5)}
    assert len(ids) == 1
    assert len(client.get("/orders").json()) == 1


def test_repeating_does_not_touch_stock_or_notifications(client, body):
    order = post_order(client, body).json()
    post_order(client, body)
    assert client.get(f"/products/{body['items'][0]['product_id']}").json()["stock"] == 10
    assert client.get(f"/orders/{order['id']}/notifications").json() == []


def test_replay_returns_the_original_result_even_if_the_price_changed_later(client, body):
    original = post_order(client, body).json()
    product_id = body["items"][0]["product_id"]
    assert client.put(f"/products/{product_id}",
                      json={"name": "Producto 1", "price_cents": 9999, "stock": 10}).status_code == 200
    replay = post_order(client, body)
    assert replay.json() == original
    assert replay.json()["total_cents"] == original["total_cents"]


def test_replay_after_confirming_returns_the_original_response_and_does_not_charge_stock_again(client, body):
    original = post_order(client, body).json()
    assert client.post(f"/orders/{original['id']}/confirm").status_code == 200
    replay = post_order(client, body)
    assert replay.status_code == 201
    assert replay.json() == original and replay.json()["status"] == "pending"
    assert client.get(f"/products/{body['items'][0]['product_id']}").json()["stock"] == 9
    assert len(client.get("/orders").json()) == 1


def test_replay_after_deleting_the_order_does_not_create_it_again(client, body):
    original = post_order(client, body).json()
    assert client.delete(f"/orders/{original['id']}").status_code == 204
    replay = post_order(client, body)
    assert replay.status_code == 201 and replay.json() == original
    assert client.get("/orders").json() == []


def test_different_keys_with_the_same_content_create_different_orders(client, body):
    first = post_order(client, body, key="clave-a")
    second = post_order(client, body, key="clave-b")
    assert first.json()["id"] != second.json()["id"]
    assert "idempotency-replayed" not in second.headers
    assert stored_keys(client) == ["clave-a", "clave-b"]


def test_requests_without_a_key_keep_creating_one_order_each(client, body):
    first = post_order(client, body, key=None)
    second = post_order(client, body, key=None)
    assert first.json()["id"] != second.json()["id"]
    assert len(client.get("/orders").json()) == 2
    assert stored_keys(client) == []


def test_keys_are_case_sensitive(client, body):
    first = post_order(client, body, key="Clave")
    second = post_order(client, body, key="clave")
    assert first.json()["id"] != second.json()["id"]


def test_the_key_header_is_documented_as_optional_in_openapi(client):
    parameters = client.get("/openapi.json").json()["paths"]["/orders"]["post"]["parameters"]
    header = next(parameter for parameter in parameters if parameter["name"] == "Idempotency-Key")
    assert header["in"] == "header" and header["required"] is False


@pytest.mark.parametrize("key, expected", [("", 422), ("k" * 255, 201), ("k" * 256, 422)])
def test_key_length_is_validated(client, body, key, expected):
    assert post_order(client, body, key=key).status_code == expected
    if expected == 422:
        assert client.get("/orders").json() == []
        assert stored_keys(client) == []


# ---------- criterio 2: misma clave y contenido distinto ----------

@pytest.mark.parametrize("changes", [
    {"city": "Cusco"},
    {"address": "Av. Otra 200"},
    {"postal_code": "15002"},
    {"shipping": "express"},
])
def test_same_key_with_different_content_returns_a_conflict(client, body, changes):
    original = post_order(client, body)
    conflict = post_order(client, {**body, **changes})
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "La clave de idempotencia ya se usó con un contenido distinto"}
    assert len(client.get("/orders").json()) == 1
    assert client.get(f"/orders/{original.json()['id']}").json() == original.json()


def test_conflict_when_the_quantity_or_the_product_changes(client, body, new_product):
    post_order(client, body)
    line = body["items"][0]
    assert post_order(client, {**body, "items": [{**line, "quantity": 2}]}).status_code == 409
    other = new_product()["id"]
    assert post_order(client, {**body, "items": [{"product_id": other, "quantity": 1}]}).status_code == 409
    assert len(client.get("/orders").json()) == 1


def test_conflict_when_the_customer_changes(client, body, new_customer):
    post_order(client, body)
    other_customer = new_customer("regular")["id"]
    assert post_order(client, {**body, "customer_id": other_customer}).status_code == 409


def test_a_conflict_does_not_replace_the_stored_response(client, body):
    original = post_order(client, body)
    post_order(client, {**body, "city": "Cusco"})
    replay = post_order(client, body)
    assert replay.status_code == 201 and replay.json() == original.json()
    assert len(client.get("/orders").json()) == 1


def test_the_conflict_does_not_depend_on_the_other_fields_being_valid_for_the_store(client, body):
    post_order(client, body)
    assert post_order(client, {**body, "customer_id": 9999}).status_code == 409


# ---------- criterio 3: peticiones concurrentes ----------

@pytest.fixture
def slow_insert(monkeypatch):
    """Alarga la creación del pedido para que las demás peticiones lleguen mientras la primera trabaja."""
    original = OrderRepository.insert_order

    def slow(self, *args, **kwargs):
        time.sleep(0.2)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(OrderRepository, "insert_order", slow)


def run_together(client, requests):
    """Lanza todas las peticiones (body, key) a la vez y devuelve sus respuestas."""
    barrier = threading.Barrier(len(requests))

    def send(request):
        barrier.wait()
        return post_order(client, request[0], key=request[1])

    with ThreadPoolExecutor(len(requests)) as pool:
        return list(pool.map(send, requests))


def test_concurrent_requests_with_the_same_key_create_a_single_order(client, body, slow_insert):
    responses = run_together(client, [(body, KEY)] * 8)
    assert [response.status_code for response in responses] == [201] * 8
    assert len({response.json()["id"] for response in responses}) == 1
    assert sum("idempotency-replayed" not in response.headers for response in responses) == 1
    assert len(client.get("/orders").json()) == 1
    assert stored_keys(client) == [KEY]


def test_concurrent_requests_with_the_same_key_and_different_content_create_a_single_order(
        client, body, slow_insert):
    other = {**body, "city": "Cusco"}
    responses = run_together(client, [(body, KEY)] * 4 + [(other, KEY)] * 4)
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [201] * 4 + [409] * 4
    assert len({response.json()["id"] for response in responses if response.status_code == 201}) == 1
    assert len(client.get("/orders").json()) == 1


def test_concurrent_requests_with_different_keys_do_not_block_each_other(client, body, slow_insert):
    responses = run_together(client, [(body, f"clave-{n}") for n in range(6)])
    assert [response.status_code for response in responses] == [201] * 6
    assert len({response.json()["id"] for response in responses}) == 6
    assert len(client.get("/orders").json()) == 6


def test_the_database_itself_refuses_a_duplicated_key(database):
    with database.connect() as connection:
        OrderRepository(connection).add_idempotency_key(KEY, "huella", {"id": 1})
    with pytest.raises(sqlite3.IntegrityError):
        with database.connect() as connection:
            OrderRepository(connection).add_idempotency_key(KEY, "otra", {"id": 2})
    with database.connect() as connection:
        assert OrderRepository(connection).find_idempotency_key(KEY) == {"request_hash": "huella",
                                                                        "response": {"id": 1}}


# ---------- criterio 4: reinicio de la aplicación ----------

def test_the_key_survives_an_application_restart(tmp_path):
    path = tmp_path / "restart.sqlite3"
    with TestClient(create_app(path)) as before:
        customer = before.post("/customers", json={"name": "Ana", "email": "ana@example.com",
                                                   "category": "premium"}).json()
        product = before.post("/products", json={"name": "Libro", "price_cents": 2500, "stock": 5}).json()
        request_body = order_body(customer["id"], product["id"], quantity=2)
        original = post_order(before, request_body)
        assert original.status_code == 201

    with TestClient(create_app(path)) as after:
        replay = post_order(after, request_body)
        assert replay.status_code == 201
        assert replay.json() == original.json()
        assert replay.headers["idempotency-replayed"] == "true"
        assert post_order(after, {**request_body, "city": "Cusco"}).status_code == 409
        assert len(after.get("/orders").json()) == 1


def test_initializing_the_database_again_keeps_the_stored_keys(database):
    with database.connect() as connection:
        OrderRepository(connection).add_idempotency_key(KEY, "huella", {"id": 1})
    database.initialize()
    with database.connect() as connection:
        assert OrderRepository(connection).find_idempotency_key(KEY)["request_hash"] == "huella"


# ---------- criterio 5: errores sin clave bloqueada ni pedido parcial ----------

def test_unknown_customer_does_not_block_the_key(client, body):
    assert post_order(client, {**body, "customer_id": 9999}).status_code == 404
    assert stored_keys(client) == []
    retry = post_order(client, body)
    assert retry.status_code == 201
    assert "idempotency-replayed" not in retry.headers
    assert len(client.get("/orders").json()) == 1


def test_unknown_product_does_not_block_the_key(client, body):
    line = body["items"][0]
    bad = {**body, "items": [line, {"product_id": 9999, "quantity": 1}]}
    assert post_order(client, bad).status_code == 404
    assert client.get("/orders").json() == []
    assert stored_keys(client) == []
    assert post_order(client, body).status_code == 201


def test_invalid_body_does_not_use_the_key(client, body):
    assert post_order(client, {**body, "items": []}).status_code == 422
    assert stored_keys(client) == []
    assert post_order(client, body).status_code == 201


def test_failure_after_creating_the_order_leaves_no_order_and_no_key(service, database, make_customer,
                                                                    make_product, build_order, monkeypatch):
    data = build_order(make_customer("premium"), [(make_product(), 2)])

    def fail(self, *args, **kwargs):
        raise RuntimeError("fallo al guardar la clave")

    with monkeypatch.context() as patch:
        patch.setattr(OrderRepository, "add_idempotency_key", fail)
        with pytest.raises(RuntimeError):
            service.create_once(data, KEY)

    with database.connect() as connection:
        repository = OrderRepository(connection)
        assert repository.list_orders() == []
        assert repository.find_idempotency_key(KEY) is None

    order, repeated = service.create_once(data, KEY)
    assert repeated is False and order["id"] == 1


def test_failure_while_saving_the_lines_leaves_no_partial_order_and_no_key(service, database, make_customer,
                                                                          make_product, build_order,
                                                                          monkeypatch):
    data = build_order(make_customer(), [(make_product(), 1)])

    def fail(self, *args, **kwargs):
        raise RuntimeError("fallo al guardar las líneas")

    with monkeypatch.context() as patch:
        patch.setattr(OrderRepository, "add_items", fail)
        with pytest.raises(RuntimeError):
            service.create_once(data, KEY)

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 0

    assert service.create_once(data, KEY)[1] is False


def test_service_returns_the_stored_response_and_flags_the_repetition(service, make_customer, make_product,
                                                                     build_order):
    data = build_order(make_customer("business"), [(make_product(), 2)])
    original, first_repeated = service.create_once(data, KEY)
    replay, second_repeated = service.create_once(data, KEY)
    assert (first_repeated, second_repeated) == (False, True)
    assert replay == original
    assert original["status"] == "pending" and original["items"][0]["quantity"] == 2


# ---------- repositorio ----------

def test_repository_stores_and_reads_the_response_as_json(database):
    with database.connect() as connection:
        repository = OrderRepository(connection)
        assert repository.find_idempotency_key(KEY) is None
        repository.add_idempotency_key(KEY, "abc", {"id": 7, "items": [{"product_id": 1}], "nota": "ñ"})
        assert repository.find_idempotency_key(KEY) == {
            "request_hash": "abc", "response": {"id": 7, "items": [{"product_id": 1}], "nota": "ñ"}}
        assert repository.find_idempotency_key("otra") is None
