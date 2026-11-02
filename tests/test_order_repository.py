"""OrderRepository probado directo contra SQLite, sin pasar por HTTP ni por OrderService."""
import pytest

from app.order_repository import OrderRepository
from app.orders import OrderStatus
from app.pricing import calculate_totals


@pytest.fixture
def pricing():
    return calculate_totals("premium", "standard", 5000)


def save_order(database, build_order, customer_id, product_id, pricing, quantity=2):
    """Inserta un pedido con una línea usando solo el repositorio y devuelve su id."""
    data = build_order(customer_id, [(product_id, quantity)])
    with database.connect() as connection:
        repository = OrderRepository(connection)
        order_id = repository.insert_order(data, "premium", pricing)
        repository.add_items(order_id, [(product_id, quantity, 2500)])
        return order_id


# ---------- lecturas ----------

def test_find_returns_none_when_the_row_does_not_exist(database):
    with database.connect() as connection:
        repository = OrderRepository(connection)
        assert repository.find_order(1) is None
        assert repository.find_customer(1) is None
        assert repository.find_product(1) is None
        assert repository.find_order_with_items(1) is None


def test_find_customer_and_product_return_the_stored_row(database, make_customer, make_product):
    customer_id = make_customer("business")
    product_id = make_product(price_cents=1234, stock=7)
    with database.connect() as connection:
        repository = OrderRepository(connection)
        assert repository.find_customer(customer_id)["category"] == "business"
        product = repository.find_product(product_id)
        assert (product["price_cents"], product["stock"]) == (1234, 7)


def test_insert_order_stores_every_field_and_starts_pending(database, build_order, make_customer,
                                                           make_product, pricing):
    customer_id, product_id = make_customer("premium"), make_product()
    order_id = save_order(database, build_order, customer_id, product_id, pricing)
    with database.connect() as connection:
        order = OrderRepository(connection).find_order(order_id)
    assert order["customer_id"] == customer_id
    assert order["customer_category"] == "premium"
    assert (order["address"], order["city"], order["postal_code"]) == ("Av. Primavera 100", "Lima", "15001")
    assert order["shipping"] == "standard"
    assert (order["subtotal_cents"], order["discount_cents"], order["shipping_cents"], order["total_cents"]) == (
        5000, 500, 1000, 5500)
    assert order["status"] == OrderStatus.PENDING


def test_list_orders_and_list_items_follow_insertion_order(database, build_order, make_customer,
                                                          make_product, pricing):
    customer_id = make_customer()
    first_product, second_product = make_product(), make_product()
    first = save_order(database, build_order, customer_id, first_product, pricing)
    second = save_order(database, build_order, customer_id, second_product, pricing)
    with database.connect() as connection:
        repository = OrderRepository(connection)
        repository.add_items(second, [(first_product, 1, 2500)])
        assert [order["id"] for order in repository.list_orders()] == [first, second]
        assert repository.list_items(second) == [
            {"product_id": second_product, "quantity": 2, "unit_price_cents": 2500},
            {"product_id": first_product, "quantity": 1, "unit_price_cents": 2500},
        ]


def test_find_order_with_items_returns_the_order_and_its_lines(database, build_order, make_customer,
                                                              make_product, pricing):
    customer_id, product_id = make_customer(), make_product()
    order_id = save_order(database, build_order, customer_id, product_id, pricing)
    with database.connect() as connection:
        order = OrderRepository(connection).find_order_with_items(order_id)
    assert order["id"] == order_id
    assert order["items"] == [{"product_id": product_id, "quantity": 2, "unit_price_cents": 2500}]


# ---------- escrituras ----------

def test_update_order_changes_the_data_but_keeps_the_status(database, build_order, make_customer,
                                                           make_product, pricing):
    customer_id, product_id = make_customer(), make_product()
    order_id = save_order(database, build_order, customer_id, product_id, pricing)
    new_data = build_order(customer_id, [(product_id, 1)], shipping="express")
    new_pricing = calculate_totals("business", "express", 2500)
    with database.connect() as connection:
        repository = OrderRepository(connection)
        repository.update_order(order_id, new_data, "business", new_pricing)
        order = repository.find_order(order_id)
    assert order["shipping"] == "express"
    assert order["customer_category"] == "business"
    assert order["total_cents"] == new_pricing["total_cents"]
    assert order["status"] == OrderStatus.PENDING


def test_replace_items_removes_the_old_lines(database, build_order, make_customer, make_product, pricing):
    customer_id, old_product, new_product = make_customer(), make_product(), make_product()
    order_id = save_order(database, build_order, customer_id, old_product, pricing)
    with database.connect() as connection:
        repository = OrderRepository(connection)
        repository.replace_items(order_id, [(new_product, 3, 900)])
        assert repository.list_items(order_id) == [
            {"product_id": new_product, "quantity": 3, "unit_price_cents": 900}]


def test_take_stock_discounts_when_there_is_enough(database, make_product):
    product_id = make_product(stock=5)
    with database.connect() as connection:
        repository = OrderRepository(connection)
        assert repository.take_stock(product_id, 3) is True
        assert repository.find_product(product_id)["stock"] == 2


def test_take_stock_accepts_exactly_the_remaining_stock(database, make_product):
    product_id = make_product(stock=4)
    with database.connect() as connection:
        repository = OrderRepository(connection)
        assert repository.take_stock(product_id, 4) is True
        assert repository.find_product(product_id)["stock"] == 0


def test_take_stock_refuses_and_leaves_stock_untouched_when_it_is_not_enough(database, make_product):
    product_id = make_product(stock=2)
    with database.connect() as connection:
        repository = OrderRepository(connection)
        assert repository.take_stock(product_id, 3) is False
        assert repository.find_product(product_id)["stock"] == 2


def test_notifications_are_listed_by_order_in_insertion_order(database, build_order, make_customer,
                                                             make_product, pricing):
    customer_id, product_id = make_customer(), make_product()
    first = save_order(database, build_order, customer_id, product_id, pricing)
    second = save_order(database, build_order, customer_id, product_id, pricing)
    with database.connect() as connection:
        repository = OrderRepository(connection)
        repository.add_notification(first, "a@example.com", "primero")
        repository.add_notification(second, "b@example.com", "otro pedido")
        repository.add_notification(first, "a@example.com", "segundo")
        notifications = repository.list_notifications(first)
    assert [(n["recipient"], n["message"]) for n in notifications] == [
        ("a@example.com", "primero"), ("a@example.com", "segundo")]


def test_set_status_changes_only_that_order(database, build_order, make_customer, make_product, pricing):
    customer_id, product_id = make_customer(), make_product()
    first = save_order(database, build_order, customer_id, product_id, pricing)
    second = save_order(database, build_order, customer_id, product_id, pricing)
    with database.connect() as connection:
        repository = OrderRepository(connection)
        repository.set_status(first, OrderStatus.CONFIRMED)
        assert repository.find_order(first)["status"] == "confirmed"
        assert repository.find_order(second)["status"] == "pending"


def test_delete_order_also_removes_its_lines_and_notifications(database, build_order, make_customer,
                                                              make_product, pricing):
    customer_id, product_id = make_customer(), make_product()
    order_id = save_order(database, build_order, customer_id, product_id, pricing)
    with database.connect() as connection:
        repository = OrderRepository(connection)
        repository.add_notification(order_id, "a@example.com", "hola")
        repository.delete_order(order_id)
        assert repository.find_order(order_id) is None
        assert repository.list_items(order_id) == []
        assert repository.list_notifications(order_id) == []


# ---------- transacción ----------

def test_repository_leaves_the_commit_to_the_caller(database, build_order, make_customer, make_product, pricing):
    customer_id, product_id = make_customer(), make_product(stock=5)
    data = build_order(customer_id, [(product_id, 2)])
    with pytest.raises(RuntimeError):
        with database.connect() as connection:
            repository = OrderRepository(connection)
            repository.insert_order(data, "premium", pricing)
            repository.take_stock(product_id, 2)
            raise RuntimeError("fallo a mitad de la transacción")
    with database.connect() as connection:
        repository = OrderRepository(connection)
        assert repository.list_orders() == []
        assert repository.find_product(product_id)["stock"] == 5


# ---------- OrderStatus ----------

def test_order_status_values_are_the_strings_stored_in_the_database():
    assert [status.value for status in OrderStatus] == ["pending", "confirmed", "cancelled"]


def test_database_default_status_matches_order_status_pending(database, make_customer):
    customer_id = make_customer()
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO orders(customer_id, customer_category, address, city, postal_code, shipping,
               subtotal_cents, discount_cents, shipping_cents, total_cents)
               VALUES (?, 'regular', 'Calle 1', 'Lima', '15001', 'standard', 1, 0, 0, 1)""", (customer_id,))
        status = connection.execute("SELECT status FROM orders").fetchone()["status"]
    assert status == OrderStatus.PENDING


def test_status_is_stored_and_returned_as_plain_text(client, new_customer, new_product, new_order):
    customer, product = new_customer(), new_product()
    order = new_order(customer["id"], product["id"])
    assert client.post(f"/orders/{order['id']}/confirm").json()["status"] == "confirmed"
    raw = client.get(f"/orders/{order['id']}")
    assert '"status":"confirmed"' in raw.text.replace(" ", "")