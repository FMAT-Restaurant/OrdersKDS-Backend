# OBS-02 — Validación de Liquidación de Pagos (`pagos.pago.completado`) y Mermas con Cobro

**Módulo contraparte:** Pagos (`Payments-BE`)  
**Responsable contraparte:** Líder Técnico de Pagos  
**Referencia de auditoría:** Auditoría Equipo 6 — `OBS-02` y `DEF-E5-014` (`CP-036`) / `TASK-45`  
**Estado en `orders-backend`:** Implementado y activo (`handle_payment_completed` y `OrderService.void_order`)  
**Estado observado en `Payments-BE` (`main` @ `6758faf`):** RabbitMQ (MassTransit), SQL Server y Redis ya implementados; **existen incompatibilidades de contrato y transporte entre ambos servicios**

---

## 1. Contexto y Comportamiento Actual en `orders-backend`

`orders-backend` participa en dos flujos con `Payments-BE` a través de RabbitMQ:

1. **Solicitudes salientes desde Órdenes (exchange `ordenes.topic`, JSON plano):**
   - **`ordenes.cuenta.solicitada`** (`BillRequestedEvent`): publicada cuando el mesero solicita la cuenta (`POST /api/v1/orders/{orderId}/request-bill`) sobre una orden `DELIVERED` con precios unitarios congelados.
   - **`ordenes.cobro.solicitado`** (`ChargeRequestedEvent`): publicada cuando un administrador anula una orden en preparación con cargo al cliente (`PATCH /api/v1/orders/{orderId}/void`, `chargeCustomer=true`).
     - *Corrección `DEF-E5-014`:* En la misma transacción de base de datos, Órdenes emite también `ordenes.comanda.mermada` hacia Inventario para registrar el desperdicio físico y `ordenes.cobro.solicitado` hacia Pagos con la orden en estado `WASTED`.
2. **Confirmación entrante desde Pagos (`pagos.pago.completado`):**
   - Consumida por `orders-backend` (`handle_payment_completed` en `app/messaging/consumers/payment_completed.py`) mediante `InboundConsumerWorker` (bindings `pagos.#`, `payments.#`, `pago.#` sobre `ordenes.topic`) para transicionar la orden (`DELIVERED` o `WASTED`) hacia el estado terminal **`PAID`**.

---

## 2. Reglas Estrictas Implementadas por `OBS-02` en `orders-backend`

Para evitar que una orden se marque como `PAID` por un pago fallido, pendiente, cancelado o parcial, `handle_payment_completed` valida estrictamente:

1. **Validación de `status`:**
   - Si el campo `status` viene presente en el mensaje, debe pertenecer (case-insensitive) al conjunto de estados liquidados aceptados:
     ```text
     PAID, COMPLETED, APPROVED, SUCCEEDED, SETTLED, PAGADO, COMPLETADO, APROBADO
     ```
   - Cualquier otro estado (por ejemplo `FAILED`, `PENDING`, `CANCELLED`, `DECLINED`, `REFUNDED`, `PARTIAL`) es **ignorado** con log `WARNING`, **no** registra `processed_events` y **no** cambia la orden a `PAID`.
   - *(Solo si `status` se omite / es `null`, se acepta por retrocompatibilidad con mensajes legacy).*
2. **Validación de monto pagado (`totalPaid` o `montoTotal`):**
   - Cuando `order.total_amount > 0` y el evento incluye `totalPaid` (o su alias `montoTotal`), debe cumplirse:
     `paid_amount >= order.total_amount`.
   - Si el monto reportado es **menor al total de la orden** (pago parcial/insuficiente), `orders-backend` ignora el mensaje sin transicionar a `PAID`.
   - Los pagos con propina o excedente (`totalPaid >= order.total_amount`) sí se aceptan.
3. **Idempotencia:**
   - Cada evento válido se registra en `processed_events` por clave `(orderId, eventId)`. Reentregas con el mismo `eventId` son idempotentes y no reprocesan la transición.

---

## 3. Brechas e Incompatibilidades Detectadas con `Payments-BE` (`main` @ `6758faf`)

Al inspeccionar el código vigente de `Payments-BE`, el servicio ya tiene RabbitMQ funcionando mediante **MassTransit v8.5** y EF Bus Outbox, pero **sus contratos actuales no empatan con `orders-backend`**:

### 3.1 Salida de Pagos → Órdenes (`PaymentCompletedEvent` vs. `pagos.pago.completado`)
| Aspecto | Lo que publica `Payments-BE` hoy (`ChargeService.cs`) | Lo que espera `orders-backend` hoy (`PaymentCompletedMessage`) | Impacto si no se alinea |
| :--- | :--- | :--- | :--- |
| **Transporte / Exchange** | MassTransit `[MessageUrn("PaymentCompletedEvent")]` → exchange `PaymentCompletedEvent`, envelope `application/vnd.masstransit+json` con payload bajo `"message": { ... }` | `ordenes.topic` con routing key `pagos.pago.completado` (o `pagos.#` / `payments.#` / `pago.#`) y JSON plano (o bajo `"data"`) | **Los mensajes nunca llegan a `orders-backend`** (exchange distinto y envelope `"message"` no soportado) |
| **Identificador de orden** | `dinningSessionId` (`Guid`, no envía `orderId`) | `orderId` (`UUID` **obligatorio**, sin default) y `sessionId` opcional | **`ValidationError` → DLQ en `orders-backend`**; además no distingue múltiples órdenes en una sesión ni órdenes `isTakeaway` (`sessionId = null`) |
| **Monto y estado** | `total` (no envía `totalPaid`, `montoTotal` ni `status`) | `totalPaid` / `montoTotal` (`Decimal`) y `status` liquidado (`"PAID"` / `"COMPLETED"`) | No se ejecuta la validación de cobertura de monto (`_is_sufficient_payment` cae en fallback permisivo si falta el campo) |

### 3.2 Salida de Órdenes → Pagos (`ordenes.cuenta.solicitada` / `ordenes.cobro.solicitado` vs. `DinningSessionReadyEvent`)
| Aspecto | Lo que publica `orders-backend` hoy | Lo que consume `Payments-BE` hoy (`DinningSessionReadyConsumer`) | Impacto si no se alinea |
| :--- | :--- | :--- | :--- |
| **Transporte / URN** | Exchange `ordenes.topic`, routing key `ordenes.cuenta.solicitada` (`DELIVERED`) y `ordenes.cobro.solicitado` (`WASTED`), JSON plano | Cola MassTransit ligada a URN `[MessageUrn("DinningSessionReadyEvent")]` (+ `DinningSessionCancelledEvent`) | **`Payments-BE` no recibe las solicitudes de cuenta ni los cobros por merma `WASTED`** |
| **Identificadores** | `orderId` (obligatorio), `sessionId` (`UUID` nullable en takeaway), `tableId` string (`"MESA-7"`), `waiterId` | `dinningSessionId` (`Guid` obligatorio), `mesaId` (`Guid` obligatorio), `meseroId` (`Guid` obligatorio) | `DinningSessionReadyEventValidator` rechaza tipos/nombres (`tableId` no es `Guid`, `sessionId` puede ser `null`) |
| **Ítems y totales** | `items`: `{ itemId, plateId (string), name, quantity, unitPrice, subtotal }`; `totalAmount` plano en raíz | `items`: `{ platilloId (Guid), nombre, cantidad, precioUnitario, importe }`; `totales: { subtotal, impuestos, totalEsperado > 0 }` | Fallo de deserialización/validación → cola `_error` de MassTransit en Pagos |
| **Mermas con cargo (`WASTED`)** | `ordenes.cobro.solicitado` con `status="WASTED"` | Sin consumidor diferenciado para `ordenes.cobro.solicitado` | Riesgo de cobrar doble o perder cobros de mermas administrativas si no se enruta igual o con consumidor específico |

### 3.3 Punto compatible a destacar
- En `ChargeService.cs`, `Payments-BE` **solo emite el evento cuando los pagos cubren el 100 % de la cuenta (`ExpectedTotal + Tip`)**, cumpliendo en lógica de negocio con el requisito de `OBS-02` de no cerrar órdenes con abonos parciales.

---

## 4. Contratos JSON de Referencia (`orders-backend`)

### 4.1 Evento saliente actual de Órdenes: `ordenes.cuenta.solicitada` / `ordenes.cobro.solicitado`

```json
{
  "eventId": "44444444-5555-4666-8777-888888888888",
  "orderId": "33333333-3333-3333-3333-333333333333",
  "sessionId": "22222222-3333-4444-8555-666666666666",
  "tableId": "MESA-7",
  "waiterId": "00000000-0000-0000-0000-000000000001",
  "status": "DELIVERED",
  "totalAmount": 250.0,
  "items": [
    {
      "itemId": "33333333-0000-0000-0000-000000000001",
      "plateId": "OFFER-PASTA-01",
      "name": "Pasta Tartufo",
      "quantity": 2,
      "unitPrice": 125.0,
      "subtotal": 250.0,
      "modifiers": ["extra queso"],
      "notes": "Servir caliente"
    }
  ],
  "timestamp": "2026-11-25T18:05:00Z"
}
```

### 4.2 Evento entrante esperado por Órdenes: `pagos.pago.completado`

```json
{
  "eventId": "55555555-6666-4777-8888-999999999999",
  "orderId": "33333333-3333-3333-3333-333333333333",
  "sessionId": "22222222-3333-4444-8555-666666666666",
  "status": "PAID",
  "totalPaid": 250.0,
  "montoTotal": 250.0,
  "paymentMethod": "CARD",
  "paidAt": "2026-11-25T18:07:30Z"
}
```

---

## 5. Checklist de Acuerdos a Cerrar con el Líder de Pagos

- [ ] **Definir estrategia de transporte:** acordar si `Payments-BE` agrega consumidor/publicador en crudo (`RabbitMQ.Client`, migración ya contemplada en `Payments-BE/AGENTS.md`) sobre `ordenes.topic`, o si `orders-backend` agrega soporte de envelope MassTransit + exchange `PaymentCompletedEvent` / `DinningSessionReadyEvent`.
- [ ] **Alinear identificador primario (`orderId` vs. `dinningSessionId`):** acordar cómo viajará `orderId` en ida y vuelta (incluyendo órdenes para llevar `isTakeaway=true` donde `sessionId` es `null`) y si habrá una o varias órdenes por sesión.
- [ ] **Alinear campos de liquidación (`OBS-02`):** incluir `orderId`, `status: "PAID" | "COMPLETED"` y `totalPaid` (`total`) en el evento emitido por `ChargeService.cs`.
- [ ] **Alinear campos de solicitud de cuenta:** resolver diferencias de formato entre `BillRequestedEvent` (`tableId` string, `plateId` string, `totalAmount` único) y `DinningSessionReadyEvent` (`mesaId` Guid, `platilloId` Guid, `totales` desglosados).
- [ ] **Confirmar cobertura de mermas con cargo (`WASTED`):** acordar el manejo de `ordenes.cobro.solicitado` en `Payments-BE` y la anulación vía `DinningSessionCancelledEvent` vs. `ordenes.orden.cancelada`.
- [ ] **Ejecutar prueba E2E con RabbitMQ real** entre `orders-backend` y `Payments-BE` una vez acordado el mapeo.
