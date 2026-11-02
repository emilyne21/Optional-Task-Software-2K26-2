# Adicional 1: idempotencia en POST /orders

Una petición repetida con la misma clave no crea otro pedido. Sirve cuando el cliente no sabe si su
primera petición llegó (corte de red, doble clic, reintento automático).

## Contrato

- El cliente envía el encabezado `Idempotency-Key` con una clave que él elige, por ejemplo un UUID.
- La clave es opcional, tiene entre 1 y 255 caracteres y distingue mayúsculas de minúsculas.
- Sin el encabezado, `POST /orders` funciona igual que antes y cada petición crea un pedido.

| Situación | Respuesta |
| --- | --- |
| Clave nueva | `201` con el pedido creado. Se guarda la clave y la respuesta |
| Misma clave y mismo contenido | `201` con el cuerpo original y el encabezado `Idempotency-Replayed: true`. No se crea otro pedido |
| Misma clave y contenido distinto | `409` con `{"detail": "La clave de idempotencia ya se usó con un contenido distinto"}` |
| Clave vacía o de más de 255 caracteres | `422` |
| Cualquier error (`404`, `422`, `503` o un fallo interno) | No se guarda la clave ni el pedido. El cliente puede reintentar con la misma clave |

## Qué cuenta como mismo contenido

- Se calcula una huella SHA-256 del cuerpo ya validado: `customer_id`, `address`, `city`, `postal_code`,
  `shipping` e `items` (`app/idempotency.py`).
- No cambian la huella los espacios sobrantes de los textos ni el orden de las claves del JSON.
- Sí cambia la huella el orden de las líneas de `items`, porque el pedido guarda las líneas en ese orden.

## Cómo funciona

- La tabla `idempotency_keys` (`key` como llave primaria, `request_hash` y `response`) vive en el mismo
  archivo SQLite. Por eso la clave sobrevive a un reinicio de la aplicación.
- `OrderService.create_once` busca la clave, crea el pedido y guarda la clave en una sola transacción.
  Si algo falla, el rollback deshace el pedido y la clave juntos. No queda un pedido parcial ni una clave bloqueada.
- `Database.connect()` abre cada transacción con `BEGIN IMMEDIATE`, que toma el bloqueo de escritura de SQLite.
  Dos peticiones con la misma clave se atienden una tras otra, y la segunda ya encuentra la clave guardada.
- La llave primaria de la tabla es una segunda protección: la base rechaza una clave duplicada.

## Alcance y limitaciones

- Cubre solo `POST /orders`. No cubre `/orders/quote`, `PUT`, `confirm`, `cancel`, `DELETE` ni clientes y productos.
  Confirmar dos veces ya responde `409` por el estado del pedido.
- La repetición devuelve la respuesta guardada del primer intento, no el estado actual del pedido. Si después el
  pedido se confirmó, cambió el precio de un producto o se eliminó el pedido, repetir la clave devuelve igual la
  respuesta original y no vuelve a crear el pedido.
- Las respuestas de error no se guardan. Un reintento tras un error vuelve a ejecutarse completo.
- La clave es global, porque la aplicación no tiene autenticación ni usuarios.
- Las claves no caducan (el README no lo exige). La tabla crece con cada clave usada.
- La garantía de concurrencia se apoya en el bloqueo de escritura de SQLite. Sirve con varios hilos o procesos que
  comparten el mismo archivo, pero no con varias instancias que usen bases distintas.

## Pruebas

Están en `tests/test_idempotency.py`.

| Criterio de aceptación | Pruebas |
| --- | --- |
| Misma clave y mismo contenido devuelve el original | `test_same_key_and_same_content_return_the_original_order_without_creating_another`, `test_only_the_repeated_response_carries_the_replay_header`, `test_the_key_can_be_repeated_many_times`, `test_repeating_does_not_touch_stock_or_notifications`, `test_replay_returns_the_original_result_even_if_the_price_changed_later`, `test_replay_after_confirming_returns_the_original_response_and_does_not_charge_stock_again`, `test_replay_after_deleting_the_order_does_not_create_it_again` |
| Misma clave y contenido distinto da conflicto | `test_same_key_with_different_content_returns_a_conflict` (4 casos), `test_conflict_when_the_quantity_or_the_product_changes`, `test_conflict_when_the_customer_changes`, `test_a_conflict_does_not_replace_the_stored_response` |
| Concurrencia con la misma clave crea un solo pedido | `test_concurrent_requests_with_the_same_key_create_a_single_order`, `test_concurrent_requests_with_the_same_key_and_different_content_create_a_single_order`, `test_concurrent_requests_with_different_keys_do_not_block_each_other`, `test_the_database_itself_refuses_a_duplicated_key` |
| El comportamiento se conserva tras reiniciar | `test_the_key_survives_an_application_restart`, `test_initializing_the_database_again_keeps_the_stored_keys` |
| Un error no deja clave bloqueada ni pedido parcial | `test_unknown_customer_does_not_block_the_key`, `test_unknown_product_does_not_block_the_key`, `test_invalid_body_does_not_use_the_key`, `test_failure_after_creating_the_order_leaves_no_order_and_no_key`, `test_failure_while_saving_the_lines_leaves_no_partial_order_and_no_key` |

Las pruebas de concurrencia lanzan 8 peticiones a la vez con una barrera y alargan la creación del pedido 0,2 s,
para que las demás peticiones lleguen mientras la primera trabaja.

## Probarlo en Swagger

1. En `POST /orders` aparece el campo `Idempotency-Key`. Escribe una clave, por ejemplo `pedido-ana-001`.
2. Ejecuta con el cuerpo del recorrido inicial. Responde `201`.
3. Ejecuta otra vez sin cambiar nada. Responde `201` con el mismo pedido y el encabezado `idempotency-replayed: true`.
   `GET /orders` sigue mostrando un solo pedido.
4. Cambia la ciudad y ejecuta con la misma clave. Responde `409`.
