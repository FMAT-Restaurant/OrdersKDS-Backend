# OBS-02 — Validación de Liquidación de Pagos (`pagos.pago.completado`) y Mermas con Cobro

**Módulo contraparte:** Pagos (`Payments-BE`)  
**Responsable contraparte:** Líder Técnico de Pagos  
**Referencia de auditoría:** Auditoría Equipo 6 — `OBS-02` y `DEF-E5-014` (`CP-036`) / `TASK-45`  
**Estado en `orders-backend`:** Implementado y activo (`handle_payment_completed` y `OrderService.void_order`)

---

## 1. Contexto y Comportamiento Actual en `orders-backend`

`orders-backend` participa en dos flujos con `Payments-BE` a través de RabbitMQ:

1. **Solicitudes salientes desde Órdenes (exchange `ordenes.topic`):**
   - **`ordenes.cuenta.solicitada`** (`BillRequestedEvent`): publicada cuando el mesero solicita la cuenta (`POST /api/v1/orders/{orderId}/request-bill`) sobre una orden `DELIVERED` con precios unitarios congelados.
   - **`ordenes.cobro.solicitado`** (`ChargeRequestedEvent`): publicada cuando un administrador anula una orden en preparación con cargo al cliente (`PATCH /api/v1/orders/{orderId}/void`, `chargeCustomer=true`).
     - *Corrección `DEF-E5-014`:* En la misma transacción de base de datos, Órdenes emite también `ordenes.comanda.mermada` hacia Inventario para registrar el desperdicio físico y `ordenes.cobro.solicitado` hacia Pagos con la orden en estado `WASTED`.
2. **Confirmación entrante desde Pagos (`pagos.pago.completado`):**
   - Consumida por `orders-backend` (`handle_payment_completed` en `app/messaging/consumers/payment_completed.py`) para transicionar la orden (`DELIVERED` o `WASTED`) hacia el estado terminal **`PAID`**.

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

## 3. Brecha Actual en `Payments-BE`

Al inspeccionar el repositorio `Payments-BE`, el servicio (`PaymentsModule/Program.cs`) se encuentra actualmente en etapa de plantilla base (boilerplate ASP.NET Core con `/health` y OpenAPI) y **aún no implementa la integración de mensajería RabbitMQ** (ni consumo de `ordenes.cuenta.solicitada` / `ordenes.cobro.solicitado`, ni publicación de `pagos.pago.completado`).

Mientras Pagos no implemente el publicador/consumidor, las órdenes en estado `DELIVERED` o `WASTED` no recibirán el cierre automático a `PAID` en integración real.

---

## 4. Contratos JSON Esperados

### 4.1 Evento saliente: `ordenes.cuenta.solicitada` (y análogo `ordenes.cobro.solicitado`)

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

### 4.2 Evento entrante esperado desde Pagos: `pagos.pago.completado`

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

> Importante: Pagos debe asegurarse de emitir `pagos.pago.completado` **solo cuando la liquidación total de la orden haya alcanzado `>= totalAmount`** y con uno de los valores de `status` liquidados aprobados (recomendado: `"PAID"` o `"COMPLETED"`). Si hay cuentas divididas o pagos parciales en Caja, Pagos debe acumularlos y publicar `pagos.pago.completado` hasta completar el 100% de la orden (o incluir el acumulado total en `totalPaid`).

---

## 5. Checklist de Acuerdos a Cerrar con el Líder de Pagos

- [ ] Confirmar exchange y routing keys en RabbitMQ (`ordenes.topic` para `ordenes.cuenta.solicitada` / `ordenes.cobro.solicitado`; exchange de Pagos y routing key `pagos.pago.completado`).
- [ ] Confirmar que Pagos enviará `eventId` (UUID v4 único por evento), `orderId` (UUID v4), `status` (`"PAID"` o `"COMPLETED"`) y `totalPaid` (numérico/decimal) en `pagos.pago.completado`.
- [ ] Definir el manejo de cuentas divididas o abonos parciales: acordar que Pagos no emitirá `pagos.pago.completado` con `status="PAID"` hasta que el acumulado pagado cubra `order.total_amount`.
- [ ] Confirmar consumo de `ordenes.cobro.solicitado` para órdenes anuladas administrativamente con cargo (`WASTED`).
- [ ] Acordar fecha para prueba E2E real con RabbitMQ levantado entre `orders-backend` y `Payments-BE`.
