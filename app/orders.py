from enum import StrEnum
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.idempotency import request_fingerprint
from app.order_repository import OrderRepository
from app.pricing import calculate_totals


class ItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0, strict=True)
    quantity: int = Field(gt=0, le=10_000, strict=True)


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customer_id: int = Field(gt=0, strict=True)
    address: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=20)
    shipping: Literal["pickup", "standard", "express"] = "standard"
    items: list[ItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_products(self):
        if len({item.product_id for item in self.items}) != len(self.items):
            raise ValueError("No repita productos; indique la cantidad en una sola línea")
        return self


class OrderStatus(StrEnum):
    """Estados posibles de un pedido. Un pedido solo cambia de estado desde PENDING."""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class OrderService:
    def __init__(self, database):
        self.database = database

    def save(self, data, order_id=None):
        with self.database.connect() as connection:
            return self._save(OrderRepository(connection), data, order_id)

    def create_once(self, data, key):
        """Crea el pedido una sola vez para esta clave de idempotencia. Devuelve (pedido, repetido).

        La búsqueda de la clave, la creación del pedido y el registro de la clave ocurren en la
        misma transacción. Connect() ya tomó el bloqueo de escritura de SQLite (BEGIN IMMEDIATE),
        así que dos peticiones con la misma clave se atienden una tras otra: la segunda ya ve la
        clave guardada. Si algo falla, el rollback deshace el pedido y la clave juntos.
        """
        fingerprint = request_fingerprint(data)
        with self.database.connect() as connection:
            repository = OrderRepository(connection)
            stored = repository.find_idempotency_key(key)
            if stored is not None:
                if stored["request_hash"] != fingerprint:
                    raise HTTPException(409, "La clave de idempotencia ya se usó con un contenido distinto")
                return stored["response"], True
            order = self._save(repository, data)
            repository.add_idempotency_key(key, fingerprint, order)
            return order, False

    def _save(self, repository, data, order_id=None):
        if order_id is not None:
            old = repository.find_order(order_id)
            if old is None:
                raise HTTPException(404, "Pedido no encontrado")
            if old["status"] != OrderStatus.PENDING:
                raise HTTPException(409, "Solo puede editar pedidos pendientes")
        customer = repository.find_customer(data.customer_id)
        if customer is None:
            raise HTTPException(404, "Cliente no encontrado")
        subtotal = 0
        lines = []
        for item in data.items:
            product = repository.find_product(item.product_id)
            if product is None:
                raise HTTPException(404, f"Producto {item.product_id} no encontrado")
            subtotal += product["price_cents"] * item.quantity
            lines.append((item.product_id, item.quantity, product["price_cents"]))
        pricing = calculate_totals(customer["category"], data.shipping, subtotal)
        if order_id is None:
            order_id = repository.insert_order(data, customer["category"], pricing)
            repository.add_items(order_id, lines)
        else:
            repository.update_order(order_id, data, customer["category"], pricing)
            repository.replace_items(order_id, lines)
        return repository.find_order_with_items(order_id)

    def quote(self, data):
        with self.database.connect() as connection:
            repository = OrderRepository(connection)
            customer = repository.find_customer(data.customer_id)
            if customer is None:
                raise HTTPException(404, "Cliente no encontrado")
            subtotal = 0
            for item in data.items:
                product = repository.find_product(item.product_id)
                if product is None:
                    raise HTTPException(404, f"Producto {item.product_id} no encontrado")
                subtotal += product["price_cents"] * item.quantity
            return calculate_totals(customer["category"], data.shipping, subtotal)

    def get(self, order_id):
        with self.database.connect() as connection:
            order = OrderRepository(connection).find_order_with_items(order_id)
            if order is None:
                raise HTTPException(404, "Pedido no encontrado")
            return order

    def list(self):
        with self.database.connect() as connection:
            return OrderRepository(connection).list_orders()

    def confirm(self, order_id):
        with self.database.connect() as connection:
            repository = OrderRepository(connection)
            order = repository.find_order(order_id)
            if order is None:
                raise HTTPException(404, "Pedido no encontrado")
            if order["status"] != OrderStatus.PENDING:
                raise HTTPException(409, "El pedido ya no está pendiente")
            for item in repository.list_items(order_id):
                if not repository.take_stock(item["product_id"], item["quantity"]):
                    raise HTTPException(409, f"Stock insuficiente para producto {item['product_id']}")
            customer = repository.find_customer(order["customer_id"])
            message = f"Pedido {order_id} confirmado. Total: {order['total_cents']} centavos."
            repository.add_notification(order_id, customer["email"], message)
            repository.set_status(order_id, OrderStatus.CONFIRMED)
            return {"id": order_id, "status": OrderStatus.CONFIRMED, "message": message}

    def cancel(self, order_id):
        with self.database.connect() as connection:
            repository = OrderRepository(connection)
            order = repository.find_order(order_id)
            if order is None:
                raise HTTPException(404, "Pedido no encontrado")
            if order["status"] != OrderStatus.PENDING:
                raise HTTPException(409, "Solo puede cancelar pedidos pendientes")
            repository.set_status(order_id, OrderStatus.CANCELLED)
            return {"id": order_id, "status": OrderStatus.CANCELLED}

    def delete(self, order_id):
        with self.database.connect() as connection:
            repository = OrderRepository(connection)
            order = repository.find_order(order_id)
            if order is None:
                raise HTTPException(404, "Pedido no encontrado")
            if order["status"] != OrderStatus.PENDING:
                raise HTTPException(409, "Solo puede eliminar pedidos pendientes")
            repository.delete_order(order_id)

    def notifications(self, order_id):
        self.get(order_id)
        with self.database.connect() as connection:
            return OrderRepository(connection).list_notifications(order_id)


router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post("/quote")
def quote_order(data: OrderInput, request: Request):
    return OrderService(request.app.state.database).quote(data)


@router.post("", status_code=201)
def create_order(
    data: OrderInput,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=255,
               description="Clave opcional elegida por el cliente. Repetir la petición con la misma "
                           "clave y el mismo contenido devuelve el pedido original sin crear otro."),
    ] = None,
):
    service = OrderService(request.app.state.database)
    if idempotency_key is None:
        return service.save(data)
    order, repeated = service.create_once(data, idempotency_key)
    if repeated:
        response.headers["Idempotency-Replayed"] = "true"
    return order


@router.get("")
def list_orders(request: Request):
    return OrderService(request.app.state.database).list()


@router.get("/{order_id}")
def get_order(order_id: int, request: Request):
    return OrderService(request.app.state.database).get(order_id)


@router.put("/{order_id}")
def update_order(order_id: int, data: OrderInput, request: Request):
    return OrderService(request.app.state.database).save(data, order_id)


@router.delete("/{order_id}", status_code=204)
def delete_order(order_id: int, request: Request):
    OrderService(request.app.state.database).delete(order_id)
    return Response(status_code=204)


@router.post("/{order_id}/confirm")
def confirm_order(order_id: int, request: Request):
    return OrderService(request.app.state.database).confirm(order_id)


@router.post("/{order_id}/cancel")
def cancel_order(order_id: int, request: Request):
    return OrderService(request.app.state.database).cancel(order_id)


@router.get("/{order_id}/notifications")
def list_notifications(order_id: int, request: Request):
    return OrderService(request.app.state.database).notifications(order_id)