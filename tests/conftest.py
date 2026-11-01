"""Fixtures compartidas. Cada prueba recibe una base SQLite temporal e independiente."""
import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.orders import ItemInput, OrderInput, OrderService
from main import create_app


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "unit.sqlite3")
    db.initialize()
    return db


@pytest.fixture
def service(database):
    return OrderService(database)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "api.sqlite3")) as session:
        yield session


@pytest.fixture
def make_customer(database):
    """Inserta un cliente directo en la base y devuelve su id."""
    counter = {"n": 0}

    def factory(category="regular"):
        counter["n"] += 1
        with database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO customers(name, email, category) VALUES (?, ?, ?)",
                (f"Cliente {counter['n']}", f"cliente{counter['n']}@example.com", category),
            )
            return cursor.lastrowid

    return factory


@pytest.fixture
def make_product(database):
    """Inserta un producto directo en la base y devuelve su id."""
    counter = {"n": 0}

    def factory(price_cents=2500, stock=5):
        counter["n"] += 1
        with database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO products(name, price_cents, stock) VALUES (?, ?, ?)",
                (f"Producto {counter['n']}", price_cents, stock),
            )
            return cursor.lastrowid

    return factory


@pytest.fixture
def new_customer(client):
    """Crea un cliente por HTTP (POST /customers) y devuelve su JSON."""
    counter = {"n": 0}

    def factory(category="regular", **changes):
        counter["n"] += 1
        body = {"name": f"Cliente {counter['n']}", "email": f"cliente{counter['n']}@example.com",
                "category": category}
        body.update(changes)
        response = client.post("/customers", json=body)
        assert response.status_code == 201
        return response.json()

    return factory


@pytest.fixture
def new_product(client):
    """Crea un producto por HTTP (POST /products) y devuelve su JSON."""
    counter = {"n": 0}

    def factory(price_cents=2500, stock=5):
        counter["n"] += 1
        response = client.post("/products", json={"name": f"Producto {counter['n']}",
                                                  "price_cents": price_cents, "stock": stock})
        assert response.status_code == 201
        return response.json()

    return factory


@pytest.fixture
def new_order(client):
    """Crea un pedido por HTTP (POST /orders) con una sola línea y devuelve su JSON."""

    def factory(customer_id, product_id, quantity=2, shipping="standard"):
        response = client.post("/orders", json={
            "customer_id": customer_id, "address": "Av. Primavera 100", "city": "Lima",
            "postal_code": "15001", "shipping": shipping,
            "items": [{"product_id": product_id, "quantity": quantity}],
        })
        assert response.status_code == 201
        return response.json()

    return factory


@pytest.fixture
def build_order():
    """Arma un OrderInput a partir de (id_producto, cantidad)."""

    def factory(customer_id, lines, shipping="standard"):
        return OrderInput(
            customer_id=customer_id,
            address="Av. Primavera 100",
            city="Lima",
            postal_code="15001",
            shipping=shipping,
            items=[ItemInput(product_id=product_id, quantity=quantity) for product_id, quantity in lines],
        )

    return factory