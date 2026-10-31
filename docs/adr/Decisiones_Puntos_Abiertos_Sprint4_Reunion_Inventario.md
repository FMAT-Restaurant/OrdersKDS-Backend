# Orders & KDS — Registro de Decisiones y Puntos Abiertos Cerrados en la Reunión con Inventario y Menú (Sprint 4)

> **Origen:** Reunión de alineación técnica con Inventario (Héctor) y Menú · Refactorización de Sprint 4  
> **Fecha de actualización:** 31 de octubre de 2026  
> **Aplica a:** `ordenes-kds-backend` y `ordenes-kds-frontend`  
> **Ubicación:** `docs/adr/Decisiones_Puntos_Abiertos_Sprint4_Reunion_Inventario.md`  
> **Complementa a:** `docs/adr/Decisiones_Puntos_Abiertos_OP01-OP09.md` y `docs/adr/Puntos_Abiertos_Pendientes.md`  
> **Estándar:** Formato Architecture Decision Record (ADR) según `CONTRIBUTING.md` §10  
>
> **Convención de este documento:**
> - Registra exclusivamente las decisiones arquitectónicas y puntos abiertos que **quedaron formalmente cerrados, acordados e implementados en el Sprint 4**.
> - Los dos únicos temas que quedaron abiertos para cierre posterior (`OP-12` sobre rehechura en `DELIVERED` y la suscripción paralela fan-out de Inventario a los eventos de Menú) se mantienen documentados en `docs/adr/Puntos_Abiertos_Pendientes.md`.

---

## 1. Matriz de Puntos Abiertos y Decisiones Cerradas en la Reunión (Sprint 4)

| Elemento / Código | Tema Acordado | Estado Consolidado (Sprint 4) | ADR / Referencia | Equipos Involucrados | Hito de Cierre |
|---|---|---|---|---|---|
| **OP-04** | Contrato de `ordenes.orden.expirada` con `reason`/`motivo` e `items[]` | `[x]` **Aceptado y Cerrado**: incluye `elapsedSeconds`, `reason: "timeout"`, `motivo: "timeout"` e `items` consolidados por `offerId` | ADR-0012 (§2.1) | Backend Orders + Inventario | Sprint 4 / Implementado |
| **OP-11** | Prioridad de cola KDS y visibilidad de comandas en `RESERVED` | `[x]` **Aceptado y Cerrado**: cola activa incluye `RESERVED` e `IN_PREPARATION`, ordenada por `COALESCE(in_preparation_at, stock_reserved_at, created_at) ASC` | ADR-0009 (Actualizado) y ADR-0013 (§2.2) | Backend + Frontend Orders | Sprint 4 / Implementado |
| **Estado `RESERVED`** | Desacoplamiento entre reserva temporal (TTL) e inicio de preparación en cocina | `[x]` **Aceptado y Cerrado**: `inventario.stock.reservado` transiciona la orden a `RESERVED` (`stock_reserved_at`) en vez de `IN_PREPARATION` | ADR-0013 (§2.2) | Backend + Frontend Orders + Inventario | Sprint 4 / Implementado |
| **Consumo en Cocina** | Descuento definitivo de stock al iniciar preparación física (`start-preparation`) | `[x]` **Aceptado y Cerrado**: `PATCH /orders/{id}/start-preparation` pasa de `RESERVED` a `IN_PREPARATION` y publica `ordenes.orden.en_preparacion` | ADR-0014 (§2.3) | Backend + Frontend Orders + Inventario | Sprint 4 / Implementado |
| **Rechazo en Cocina** | Rechazo físico de un platillo por el Chef en `RESERVED` (`kitchen-reject` — Opción 1) | `[x]` **Aceptado y Cerrado**: bloquea platillo en Redis (`available: false`), publica `ordenes.platillo.rechazado_cocina` a Inventario/Menú y devuelve la orden a `REJECTED` | ADR-0015 (§2.4) | Backend + Frontend Orders + Inventario + Menú | Sprint 4 / Implementado |
| **Opción A Stock Parcial** | Pre-reserva parcial en `inventario.stock.insuficiente` y reapertura editable desde `REJECTED` | `[x]` **Aceptado y Cerrado**: soporta `rejectedItems` y `reservedItems`; `PATCH /orders/{id}/items` reabre `REJECTED -> CREATED` sobre el mismo `orderId` | ADR-0016 (§2.5) | Backend + Frontend Orders + Inventario | Sprint 4 / Implementado |
| **Caché + Confirmar** | Mantener botón "Confirmar" con respuesta `< 20 ms` desde caché atómico de Inventario | `[x]` **Aceptado y Cerrado**: no se emiten mensajes por cada clic de platillo; se confirma al terminar de capturar frente a la mesa (`< 500 ms` round-trip WS) | ADR-0017 (§2.6) | Backend + Frontend Orders + Inventario | Sprint 4 / Implementado |
| **Saneamiento Contratos y `1:N`** | Consolidación de ítems por `offerId`, `quantity` en `platillo.preparado` y relación `1:N` en `DiningSession` | `[x]` **Aceptado y Cerrado**: suma cantidades de renglones repetidos, envía `quantity` al preparar y habilita múltiples órdenes por sesión de mesa | ADR-0018 (§2.7) | Backend Orders + Inventario + Sala | Sprint 4 / Implementado |

---

## 2. Registro Detallado de Decisiones de Arquitectura (ADR) Cerradas en Sprint 4

### 2.1 ADR-0012: Contrato definitivo de `ordenes.orden.expirada` con `reason`/`motivo` e `items` (Cierre de OP-04)

- **Estado:** Aceptado (Cerrado e implementado en Sprint 4)
- **Fecha:** 2026-10-31
- **Requisitos relacionados:** RF-35, RNF-02, RNF-13, OP-01, OP-04

#### Contexto
Cuando el `SagaTimeoutWatcher` detecta que una orden confirmada (`confirmed_at != null`) lleva `>= 60 s` en `CREATED` sin recibir respuesta de Inventario, la marca como `EXPIRED` y publica `ordenes.orden.expirada`. Si Inventario procesó la solicitud de forma tardía o dejó una reserva en vuelo, necesita conocer tanto la causa de expiración como la lista exacta de platillos (`offerId` y `quantity`) para liberar las reservas sin consultar a `orders-backend`.

#### Decisión
Se enriquece formalmente el contrato `contracts/events/ordenes.orden.expirada.v1.json` y el modelo `OrderExpiredEvent`:
1. Se incluyen los campos `reason: "timeout"` (convención en inglés) y `motivo: "timeout"` (alias en español para compatibilidad con Inventario).
2. Se incluye el arreglo `items: [{ "offerId": string, "quantity": integer }]` con los ítems activos de la orden consolidados por `offerId`.
3. En `OrderRepository.list_awaiting_stock_confirmed_before`, se precargan los ítems mediante `selectinload(Order.items)` para construir el payload completo dentro de la transacción del watcher.

#### Ejemplo del Contrato Vigente (`ordenes.orden.expirada.v1.json`)
```json
{
  "eventId": "55d8e221-9c4f-52a3-88b5-667788990011",
  "orderId": "9c25e121-8623-4084-9c25-52d1906d4dfb",
  "correlationId": null,
  "elapsedSeconds": 62,
  "reason": "timeout",
  "motivo": "timeout",
  "items": [
    {
      "offerId": "b57f7e2a-02a4-4fb7-bb41-55f2d974b101",
      "quantity": 2
    }
  ]
}
```

---

### 2.2 ADR-0013: Nuevo Estado `OrderStatus.RESERVED` y Prioridad de Cola KDS (Cierre de OP-11)

- **Estado:** Aceptado (Cerrado e implementado en Sprint 4)
- **Fecha:** 2026-10-31
- **Requisitos relacionados:** RF-01..04, RF-09, RF-18, RF-24, OP-11

#### Contexto
Anteriormente, al recibir `inventario.stock.reservado`, la orden pasaba de inmediato a `IN_PREPARATION`. Esto impedía distinguir entre una orden cuyos insumos ya fueron apartados en Inventario (en espera de que cocina inicie a cocinarla) y una orden que ya está físicamente en el fuego. Además, bloqueaba la posibilidad de que el cliente cancelara la orden sin merma antes de que el cocinero empezara a elaborarla.

#### Decisión
1. **Incorporación de `OrderStatus.RESERVED` (`RESERVADO`):**
   - Al consumir `inventario.stock.reservado`, `handle_stock_reserved` invoca `order.mark_reserved()`, pasando la orden de `CREATED` a `RESERVED` y registrando la marca de tiempo `stock_reserved_at` (solo la primera vez, de modo que si la orden fue devuelta por un rechazo en cocina y re-confirmada, conserva su antigüedad original en la fila del KDS).
2. **Visibilidad y Ordenamiento en la Cola del KDS (`OP-11` Cerrado):**
   - La vista activa del KDS (`GET /kds/queue?view=active`) incluye las comandas en `RESERVED`, `IN_PREPARATION` y `DELIVERED` con algún ítem `PENDING`.
   - El ordenamiento FIFO por antigüedad en cocina se calcula mediante:
     `COALESCE(Order.in_preparation_at, Order.stock_reserved_at, Order.created_at) ASC`, desempatando por `Order.created_at ASC` y `Order.public_id ASC`.
3. **Cancelación sin merma permitida en `CREATED`, `RESERVED` y `REJECTED`:**
   - Mientras la orden no haya pasado a `IN_PREPARATION`, `PATCH /orders/{orderId}/cancel` permite cancelarla sin cargo y emite `ordenes.orden.cancelada` con `itemsCancelados` consolidados para liberar las reservas en Inventario.
   - Una vez en `IN_PREPARATION`, `cancel` responde `409 INVALID_STATE_TRANSITION` (requiriendo anulación administrativa `WASTED` o por error de cocina `VOIDED`).

---

### 2.3 ADR-0014: Inicio Manual de Preparación en KDS y Consumo Definitivo en Inventario (`ordenes.orden.en_preparacion`)

- **Estado:** Aceptado (Cerrado e implementado en Sprint 4)
- **Fecha:** 2026-10-31
- **Requisitos relacionados:** RF-09, RF-10, RF-18, RNF-01, OP-11

#### Contexto
El cocinero puede tardar varios minutos desde que la orden aparece como `RESERVED` en la pantalla del KDS hasta que realmente comienza a cocinarla. Inventario necesita saber el instante exacto en que inicia la cocción física para apagar el TTL de la reserva temporal y descontar definitivamente los insumos.

#### Decisión
1. Se expone el endpoint `PATCH /orders/{orderId}/start-preparation` (y alias `POST`) para roles `kitchen`, `chef` y `admin`.
2. Al invocarse sobre una orden en `RESERVED`:
   - Transiciona la orden a `OrderStatus.IN_PREPARATION` y fija `in_preparation_at`.
   - Encola en el Outbox el evento **`ordenes.orden.en_preparacion`** (`contracts/events/ordenes.orden.en_preparacion.v1.json`) con la lista de ítems activos no cancelados consolidados por `offerId`.
   - Emite `notify_order_changed` por WebSocket a cocina y meseros.
3. Si se invoca sobre una orden que ya está en `IN_PREPARATION`, responde `200 OK` de forma idempotente sin duplicar el evento.
4. Al consumir `ordenes.orden.en_preparacion`, Inventario apaga el TTL de reserva de la orden y ejecuta el consumo definitivo del stock.

---

### 2.4 ADR-0015: Rechazo Físico por Ítem desde Cocina en `RESERVED` con Bloqueo en Redis y Aviso a Inventario/Menú (Opción 1)

- **Estado:** Aceptado (Cerrado e implementado en Sprint 4)
- **Fecha:** 2026-10-31
- **Requisitos relacionados:** RF-09, RF-17, RF-18, RF-40

#### Contexto
Puede ocurrir que el sistema de Inventario tuviera stock teórico de un insumo y aprobara la reserva (`RESERVED`), pero al revisar físicamente la estación en cocina el Chef descubra que dicho insumo no está disponible (por merma no registrada o deterioro). Se requería un flujo que no cancelara toda la comanda del cliente y que impidiera inmediatamente que otros meseros siguieran pidiendo ese platillo.

#### Decisión (Opción 1 acordada)
Se implementa el endpoint `PATCH /orders/{orderId}/items/{itemId}/kitchen-reject` (con cuerpo `{ "reason": "..." }`) para órdenes en estado `RESERVED`:
1. **Bloqueo instantáneo en el Redis local de Órdenes (`< 1 ms`):**
   - Invoca `await catalog_repo.set_availability(target_item.dish_id, False)`. A partir de ese milisegundo, cualquier intento de cualquier mesero de agregar ese platillo recibe `422 DISH_UNAVAILABLE`.
2. **Publicación de `ordenes.platillo.rechazado_cocina` hacia Inventario y Menú:**
   - Encola `KitchenDishRejectedEvent` (`contracts/events/ordenes.platillo.rechazado_cocina.v1.json`) con `{ eventId, orderId, itemId, offerId, quantity, reason }`.
   - **Inventario** libera la reserva de ese `offerId` en esa orden, ajusta su existencia física y mantiene apartados con TTL los demás platillos de la orden.
   - **Menú** actualiza el estado oficial de la oferta a no disponible (`available: false`).
3. **Devolución de la orden a `REJECTED` para edición del mesero:**
   - La orden pasa de `RESERVED` a `REJECTED` almacenando en `rejection_details` el origen `"source": "kitchen"` y los datos del ítem rechazado, conservando su `stock_reserved_at` original.
   - El mesero recibe la alerta en tiempo real por WebSocket (`/ws/waiter`), reemplaza o quita ese platillo en la misma orden y vuelve a confirmar.
4. Adicionalmente, se soporta `PATCH /orders/{orderId}/items/{itemId}/cancel` (`ItemStatus.CANCELLED`) para cancelar un renglón pendiente recalculando `order.total_amount`.

---

### 2.5 ADR-0016: Manejo de Stock Insuficiente Parcial (Opción A) y Reapertura Editable `REJECTED -> CREATED`

- **Estado:** Aceptado (Cerrado e implementado en Sprint 4)
- **Fecha:** 2026-10-31
- **Requisitos relacionados:** RF-13, RF-14, RF-32, RF-34, OP-05

#### Contexto
Si un cliente pide varios platillos (o 3 unidades de un mismo platillo y solo alcanza para 1), rechazar la orden como estado terminal obligaría al mesero a capturar una orden nueva desde cero y perdería el apartado de los platillos que sí tenían stock.

#### Decisión
1. **Contrato enriquecido de `inventario.stock.insuficiente` (`StockInsufficientMessage`):**
   - Soporta `missingIngredients` (`faltantes`), `rejectedItems: [{ offerId, requestedQuantity, availableQuantity }]` y `reservedItems: [{ offerId, quantity }]` (lo que Inventario dejó pre-reservado con TTL en el primer intento).
   - El consumer guarda esta estructura en `order.rejection_details` y la expone en `OrderResponse.rejectionDetails` y en el mensaje WebSocket `order.status_changed`.
2. **Reapertura editable sobre el mismo `orderId` (`PATCH /orders/{orderId}/items`):**
   - Cuando el mesero edita los ítems de una orden en `OrderStatus.REJECTED`, el backend ejecuta `order.reopen_as_draft()`, regresando la orden a `OrderStatus.CREATED` y limpiando `confirmed_at = None` y `rejection_details = None`.
3. **Re-confirmación determinista (`POST /orders/{orderId}/confirm`):**
   - Al volver a confirmar la orden editada, se emite un nuevo `ordenes.orden.creada` con el **mismo `orderId`**, la **lista completa y consolidada de `items` actualizados** y un `eventId` determinista único por intento calculado con `attempt_key=order.version`.
   - Inventario ajusta su pre-reserva para ese `orderId` y renueva el TTL de reserva.

---

### 2.6 ADR-0017: Contra-propuesta Acordada con Inventario — Caché Atómico `< 20 ms` + Mantener Botón "Confirmar"

- **Estado:** Aceptado (Cerrado en Sprint 4)
- **Fecha:** 2026-10-31
- **Requisitos relacionados:** RF-17, RF-32, RF-33, RF-34, RNF-01, RNF-06

#### Contexto
Se analizó la propuesta de enviar un evento a RabbitMQ por cada clic en el botón `+` mientras el mesero arma el borrador. Se determinó que dicho enfoque multiplicaría por 5x–10x el tráfico en RabbitMQ, obligaría a emitir compensaciones por cada clic en `-` y bloquearía insumos por borradores abandonados.

#### Decisión
Se acordó la **contra-propuesta ganadora**:
1. **Inventario implementa caché en memoria/Redis con descuento atómico (`< 20 ms`):** Permite responder a `ordenes.orden.creada` en milisegundos.
2. **Se mantiene el botón "Confirmar" mientras el mesero sigue frente a la mesa:**
   - El mesero captura los platillos en borrador (`CREATED`) validando disponibilidad general en `< 1 ms` contra el Redis local de Órdenes.
   - Al presionar "Confirmar" ahí mismo frente a los comensales, el viaje completo (`POST /confirm` → RabbitMQ → Caché de Inventario → RabbitMQ → WebSocket `/ws/waiter`) toma **menos de 500 ms**.
   - Si solo alcanza para 1 de 3 unidades, la tablet del mesero muestra la alerta en ese mismo segundo frente al cliente, el mesero ajusta la cantidad con la reapertura editable (`REJECTED -> CREATED`) y re-confirma al instante.
3. **Bloqueo proactivo cuando el stock llega a `0`:**
   - Cuando una orden confirmada agota el último insumo de un platillo (dejando su stock en `0`), la orden actual se aprueba (`inventario.stock.reservado`) e inmediatamente Inventario notifica a Menú → Menú publica `available: false` → el Redis de Órdenes bloquea el platillo (`422 DISH_UNAVAILABLE`) antes de que otro mesero intente pedirlo.

---

### 2.7 ADR-0018: Consolidación por `offerId`, `quantity` en `platillo.preparado` y Relación `1:N` en `DiningSession`

- **Estado:** Aceptado (Cerrado e implementado en Sprint 4)
- **Fecha:** 2026-10-31
- **Requisitos relacionados:** RF-10, RF-12, RF-16, RF-32, RF-39

#### Decisión
1. **Consolidación de cantidades por `offerId` (`_consolidate_order_items`):**
   - Si una comanda contiene múltiples renglones con el mismo `dish_id` (por ejemplo, dos hamburguesas en renglones separados por tener distintas notas o modificadores), `orders-backend` agrupa y suma sus `quantity` por `offerId` al emitir `ordenes.orden.creada`, `ordenes.orden.en_preparacion`, `ordenes.orden.cancelada` y `ordenes.orden.expirada`.
2. **Campo `quantity` en `ordenes.platillo.preparado`:**
   - Se añade `quantity: int` (mínimo 1) a `DishPreparedEvent` y a `contracts/events/ordenes.platillo.preparado.v1.json`, poblándose con `item.quantity` en `mark_item_ready` y `mark_order_ready`.
3. **Relación `1:N` entre `DiningSession` y `Order`:**
   - Mediante la migración Alembic `20261030_0100_task_reserved_status.py`, se elimina la restricción `uq_orders_dining_session_id` y se crea el índice `ix_orders_dining_session_id`.
   - Una misma sesión de comensales (`sessionId`) puede tener múltiples órdenes a lo largo de su estancia (`POST /orders` con `diningSessionId` y consulta mediante `GET /orders?diningSessionId=`).

