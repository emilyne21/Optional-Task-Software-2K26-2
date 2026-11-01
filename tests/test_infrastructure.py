"""Persistencia (Database) y arranque de la aplicación (main.py)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.database import Database
from main import create_app


# ---------- Database ----------

def test_initialize_creates_missing_parent_folders(tmp_path):
    path = tmp_path / "nuevo" / "subcarpeta" / "store.sqlite3"
    Database(path).initialize()
    assert path.exists()


def test_initialize_twice_keeps_existing_data(database):
    with database.connect() as connection:
        connection.execute("INSERT INTO products(name, price_cents, stock) VALUES ('Libro', 2500, 5)")
    database.initialize()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1


def test_connect_commits_when_the_block_finishes(database):
    with database.connect() as connection:
        connection.execute("INSERT INTO products(name, price_cents, stock) VALUES ('Libro', 2500, 5)")
    with database.connect() as connection:
        assert connection.execute("SELECT name FROM products").fetchone()["name"] == "Libro"


def test_connect_rolls_back_when_the_block_raises(database):
    with pytest.raises(RuntimeError):
        with database.connect() as connection:
            connection.execute("INSERT INTO products(name, price_cents, stock) VALUES ('Libro', 2500, 5)")
            raise RuntimeError("fallo a mitad de la transacción")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0


def test_foreign_keys_are_enforced(database):
    with pytest.raises(sqlite3.IntegrityError):
        with database.connect() as connection:
            connection.execute(
                """INSERT INTO orders(customer_id, customer_category, address, city, postal_code, shipping,
                   subtotal_cents, discount_cents, shipping_cents, total_cents)
                   VALUES (999, 'regular', 'Calle 1', 'Lima', '15001', 'standard', 1, 0, 0, 1)""")


def test_check_constraints_reject_negative_stock(database):
    with pytest.raises(sqlite3.IntegrityError):
        with database.connect() as connection:
            connection.execute("INSERT INTO products(name, price_cents, stock) VALUES ('Libro', 2500, -1)")


# ---------- main.py ----------

def test_create_app_uses_lab_db_path_when_no_path_is_given(tmp_path, monkeypatch):
    path = tmp_path / "desde-entorno.sqlite3"
    monkeypatch.setenv("LAB_DB_PATH", str(path))
    with TestClient(create_app()) as client:
        assert client.post("/products", json={"name": "Libro", "price_cents": 2500, "stock": 5}).status_code == 201
    assert path.exists()


@pytest.mark.parametrize("path", ["/products", "/customers", "/orders", "/orders/1", "/orders/1/notifications"])
def test_sqlite_failure_returns_503_with_detail(client, monkeypatch, path):
    def unavailable(self):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Database, "connect", unavailable)
    response = client.get(path)
    assert response.status_code == 503
    assert response.json() == {"detail": "Base de datos no disponible; reintente más tarde"}


def test_sqlite_failure_on_write_returns_503(client, monkeypatch):
    def unavailable(self):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Database, "connect", unavailable)
    response = client.post("/products", json={"name": "Libro", "price_cents": 2500, "stock": 5})
    assert response.status_code == 503