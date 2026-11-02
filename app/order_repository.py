"""Acceso a datos del módulo de pedidos. Aquí vive todo el SQL de orders, order_items,
notifications, y las lecturas de clientes y productos que necesita un pedido.

El repositorio no abre conexiones ni confirma transacciones. Recibe la conexión que abre
OrderService con Database.connect(), así cada operación del servicio sigue siendo una sola
transacción (commit al terminar, rollback si algo falla).
"""
import json


class OrderRepository:
    def __init__(self, connection):
        self.connection = connection

    # ---------- lecturas ----------

    def find_order(self, order_id):
        return self.connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()

    def find_customer(self, customer_id):
        return self.connection.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()

    def find_product(self, product_id):
        return self.connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()

    def list_orders(self):
        return [dict(row) for row in self.connection.execute("SELECT * FROM orders ORDER BY id")]

    def list_items(self, order_id):
        return [dict(row) for row in self.connection.execute(
            "SELECT product_id, quantity, unit_price_cents FROM order_items WHERE order_id = ? ORDER BY id",
            (order_id,),
        )]

    def list_notifications(self, order_id):
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM notifications WHERE order_id = ? ORDER BY id", (order_id,),
        )]

    def find_order_with_items(self, order_id):
        """Pedido como diccionario más su lista de líneas, o None si no existe."""
        order = self.find_order(order_id)
        if order is None:
            return None
        result = dict(order)
        result["items"] = self.list_items(order_id)
        return result

    def find_idempotency_key(self, key):
        """Huella y respuesta guardadas para la clave, o None si la clave no se ha usado."""
        row = self.connection.execute(
            "SELECT request_hash, response FROM idempotency_keys WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return {"request_hash": row["request_hash"], "response": json.loads(row["response"])}

    # ---------- escrituras ----------

    def insert_order(self, data, category, pricing):
        cursor = self.connection.execute(
            """INSERT INTO orders(customer_id, customer_category, address, city, postal_code,
               shipping, subtotal_cents, discount_cents, shipping_cents, total_cents)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._order_values(data, category, pricing),
        )
        return cursor.lastrowid

    def update_order(self, order_id, data, category, pricing):
        self.connection.execute(
            """UPDATE orders SET customer_id = ?, customer_category = ?, address = ?, city = ?,
               postal_code = ?, shipping = ?, subtotal_cents = ?, discount_cents = ?,
               shipping_cents = ?, total_cents = ? WHERE id = ?""",
            (*self._order_values(data, category, pricing), order_id),
        )

    def replace_items(self, order_id, lines):
        """Borra las líneas del pedido y guarda las nuevas (product_id, quantity, unit_price_cents)."""
        self.connection.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        self.add_items(order_id, lines)

    def add_items(self, order_id, lines):
        self.connection.executemany(
            "INSERT INTO order_items(order_id, product_id, quantity, unit_price_cents) VALUES (?, ?, ?, ?)",
            [(order_id, *line) for line in lines],
        )

    def take_stock(self, product_id, quantity):
        """Descuenta stock solo si alcanza. Devuelve False cuando no había suficiente."""
        cursor = self.connection.execute(
            "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
            (quantity, product_id, quantity),
        )
        return cursor.rowcount > 0

    def add_notification(self, order_id, recipient, message):
        self.connection.execute(
            "INSERT INTO notifications(order_id, recipient, message) VALUES (?, ?, ?)",
            (order_id, recipient, message),
        )

    def set_status(self, order_id, status):
        self.connection.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))

    def delete_order(self, order_id):
        self.connection.execute("DELETE FROM orders WHERE id = ?", (order_id,))

    def add_idempotency_key(self, key, request_hash, response):
        """Guarda la clave con la huella de la petición y la respuesta original."""
        self.connection.execute(
            "INSERT INTO idempotency_keys(key, request_hash, response) VALUES (?, ?, ?)",
            (key, request_hash, json.dumps(response)),
        )

    # ---------- auxiliares ----------

    @staticmethod
    def _order_values(data, category, pricing):
        return (data.customer_id, category, data.address, data.city, data.postal_code, data.shipping,
                pricing["subtotal_cents"], pricing["discount_cents"],
                pricing["shipping_cents"], pricing["total_cents"])