# ADR-0020 / OP-14 [CRÍTICO]: Acuerdo de integración con Pagos (Equipo 2) ante anulación automática (`DELIVERED → VOIDED`) posterior a solicitud de cuenta (`RF-41`, `RF-29`, `RF-27`)

- **Estado:** Punto Abierto de Integración (**CRÍTICO** — Pendiente de acuerdo formal en sesión de acople con Equipo 2 · `Payments-BE`)
- **Severidad de Integración:** **CRÍTICA (Alta Prioridad para Acople Inter-Equipos)**
- **Fecha:** 2026-11-19
- **Equipos involucrados:** Equipo 5 (`Orders & KDS` — Emisor) y Equipo 2 (`Payments-BE` — Consumidor / Caja)
- **Requisitos relacionados:** `RF-27` (Rechazo en cocina por falta física de stock), `RF-29` (Solicitud de rehechura de platillo entregado), `RF-41` (Solicitud de cuenta con precios congelados), `RF-42` (Cierre de comanda por confirmación de pago), `RNF-05` (Idempotencia en consumidores AMQP), Sección `2.7.1` del ERS v5.1.

---

## 1. Contexto y Secuencia del Escenario Extremo

Con la alineación de la máquina de estados del dominio (`ERS_IEEE_OrdenesKDS.md` v5.1 y `app/services/orders.py`), el microservicio `orders-backend` contempla y soporta la transición automática de salida **`DELIVERED → VOIDED` (`Entregada → Anulada`)** cuando el único platillo activo de una comanda entregada entra a rehechura y es rechazado físicamente por cocina.

Sin embargo, cuando dicho flujo ocurre **después** de que el mesero ya había emitido la solicitud de cuenta hacia el microservicio de Pagos (`Equipo 2`), se produce una condición de borde inter-servicio que requiere un acuerdo explícito de integración:

1. **Entrega inicial (`OrderStatus.DELIVERED`):** Una comanda con **un (1) solo platillo activo** (o cuyo único platillo no cancelado restante tiene `totalAmount > 0`) es entregada en mesa.
2. **Primera solicitud de cuenta (`RF-41` — `POST /orders/{orderId}/request-bill`):** El mesero solicita la cuenta. `orders-backend` publica en RabbitMQ el evento **`ordenes.cuenta.solicitada`** con `totalAmount` congelado y `eventId = uuid5("ordenes.cuenta.solicitada:{orderId}:v{version}:{totalAmount}:{activeCount}")`. El microservicio de **Pagos (Equipo 2)** recibe el evento y abre una cuenta pendiente de cobro en caja asociada a `orderId`.
3. **Devolución y solicitud de rehechura antes de pagar (`RF-29` — `PATCH /orders/{orderId}/items/{itemId}/redo`):** Antes de que el cliente liquide la cuenta (`pagos.pago.completado`), el comensal devuelve el platillo por defecto de preparación. El mesero solicita su rehechura: la comanda matriz permanece en `DELIVERED`, el ítem regresa a `ItemStatus.PENDING` y `orders-backend` bloquea cualquier nueva llamada a `POST /orders/{orderId}/request-bill` con `409 INVALID_STATE_TRANSITION` mientras la rehechura esté pendiente en cocina.
4. **Rechazo físico en cocina durante la rehechura (`RF-27` — `PATCH /orders/{orderId}/items/{itemId}/kitchen-reject`):** El Jefe de Cocina o Administrador rechaza el platillo en rehechura por agotamiento físico real de insumos.
   - Si en la comanda existieran otros platillos entregados activos, la orden permanecería en `DELIVERED` con el nuevo `totalAmount` reducido y el mesero podría reemitir `POST /orders/{orderId}/request-bill`, generando un nuevo `eventId` determinista con el monto actualizado.
   - **Caso crítico (`0` platillos activos restantes):** Al ser el **único platillo activo** de la comanda, el ítem pasa a `ItemStatus.CANCELLED`, `order.total_amount` se recalcula a **`0.00`** y `order.mark_voided()` transiciona automáticamente la comanda de **`DELIVERED` a `VOIDED` (`Anulada`, estado terminal sin cobro)**.
5. **Brecha de notificación hacia Pagos (Equipo 2):**
   - Al ejecutarse `kitchen_reject_item`, `orders-backend` publica en RabbitMQ **`ordenes.platillo.rechazado_cocina`** (orientado a Inventario y Menú para bloqueo local y reconciliación de stock).
   - No obstante, en el catálogo actual de eventos salientes (Tabla `2.7.1` del ERS), **no existe un evento específico de cancelación de cuenta dirigido a Pagos**, y como la orden ya está en estado terminal `VOIDED`, el endpoint `POST /orders/{orderId}/request-bill` ya no puede invocarse.
   - **Riesgo en el acople:** Si Pagos (Equipo 2) no se entera de que la orden `orderId` pasó a `VOIDED` (`\$0.00`), **la cuenta abierta en el paso 2 quedaría huérfana ("abierta" por el monto original) en la interfaz de caja de Pagos**, permitiendo potencialmente un cobro indebido o impidiendo el cierre limpio de la sesión de mesa.

---

## 2. Opciones Técnicas de Resolución para Acordar con el Equipo 2 (Pagos)

Para cerrar este Punto Abierto (`OP-14`) durante la sesión de acoplamiento con el **Equipo 2 (`Payments-BE`)**, se plantean tres alternativas ordenadas por bajo impacto arquitectónico:

### Opción A (Recomendada — Cero cambios de contrato en `OrdersKDS`): Suscripción de Pagos a `ordenes.platillo.rechazado_cocina` o `ordenes.comanda.mermada`
- **Mecanismo:** Dado que RabbitMQ opera con un *exchange* de tipo `topic` (Pub/Sub), el microservicio de **Pagos (Equipo 2)** enlaza su cola o añade un *binding* adicional, o bien `orders-backend` emite también `ordenes.comanda.mermada` (`reason="KITCHEN_ERROR"`) cuando `DELIVERED → VOIDED` ocurre en `kitchen_reject_item`.
- **Acción en Pagos:** Si Pagos recibe un evento de anulación/merma para un `orderId` que tenía una cuenta en estado `PENDING` / `OPEN` (aún no pagada), Pagos anula o cierra automáticamente dicha cuenta abierta con monto `\$0.00`.
- **Ventaja:** No requiere inventar un nuevo *routing key* fuera del catálogo oficial del salón ni intervención manual del cajero.

### Opción B (Reemisión automática de `ordenes.cuenta.solicitada` en `\$0.00` con `status="VOIDED"` al anular desde `DELIVERED`)
- **Mecanismo:** Cuando `OrderService.kitchen_reject_item` transiciona una orden desde `DELIVERED` hacia `VOIDED` (`total_amount == 0.00`), `orders-backend` encola automáticamente en el Outbox una última actualización de `ordenes.cuenta.solicitada` con:
  - `status: "VOIDED"`
  - `totalAmount: 0.0`
  - `items: []`
  - `attempt_key: f"v{order.version}:0.00:0:VOIDED"` (produciendo un `eventId` nuevo que no choca con la deduplicación `RNF-05`).
- **Acción en Pagos:** Pagos ya consume `ordenes.cuenta.solicitada`; al recibir una actualización para el mismo `orderId` con `status: "VOIDED"` o `totalAmount: 0.0`, cancela/cierra la cuenta abierta previa.
- **Ventaja:** Reutiliza el único canal (`ordenes.cuenta.solicitada`) que el Equipo 2 ya escucha de `OrdersKDS`.

### Opción C (Operativa / Manual en Caja de Pagos)
- **Mecanismo:** El cajero o administrador anula manualmente la cuenta abierta desde el panel de Pagos cuando cocina notifica la anulación total de la comanda en rehechura.
- **Desventaja:** Depende de coordinación humana en piso y deja una ventana temporal con una cuenta huérfana en caja.

---

## 3. Plan de Acción y Criterio de Cierre (`Definition of Done`)

- [ ] **1. Revisión en mesa de acople con Equipo 2 (Pagos):** Presentar las Opciones **A** y **B** al líder técnico del Equipo 2 (`Payments-BE`) antes de las pruebas de integración en el broker compartido (`TASK-38`).
- [ ] **2. Selección y ajuste en `OrderService.kitchen_reject_item` (`app/services/orders.py`):**
  - Si el Equipo 2 elige la **Opción B**, agregar una llamada a `await self._stage_payment_request(order, BillRequestedEvent, attempt_key=f"v{order.version or 1}:0.00:0:VOIDED")` dentro de la rama `if not _has_other_active_items(...)` en `OrderService.kitchen_reject_item`.
  - Si el Equipo 2 elige la **Opción A** con `ordenes.comanda.mermada`, invocar `self._stage_order_wasted(order, reason="KITCHEN_ERROR", motivo="error_cocina", note=reason)` al transicionar `DELIVERED → VOIDED`.
- [ ] **3. Prueba de contrato e integración:** Añadir caso de prueba en `tests/unit/api/test_kds_api.py` y `tests/contract/` que valide el payload acordado con el Equipo 2.
