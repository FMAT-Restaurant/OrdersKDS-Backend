# OBS-04 — Propiedad de Órdenes por Mesero (BOLA) y Reasignación desde Sala

**Módulo contraparte:** Sala (`Floor-Module`)  
**Responsable contraparte:** Líder Técnico de Sala  
**Referencia de auditoría:** Auditoría Equipo 6 — `OBS-04` (Broken Object-Level Authorization) / `TASK-45`  
**Estado en `orders-backend`:** Implementado y activo (`ENFORCE_WAITER_OWNERSHIP=true` por defecto)

---

## 1. Contexto y Comportamiento Actual en `orders-backend`

Para mitigar el riesgo de **BOLA / IDOR** (`OBS-04`) reportado por Equipo 6, `orders-backend` aplica la regla `ensure_waiter_order_ownership(order, auth)` sobre todas las mutaciones operativas iniciadas por un mesero sobre una orden existente:

| Endpoint | Operación | Comportamiento BOLA |
| :--- | :--- | :--- |
| `PATCH /api/v1/orders/{orderId}/items` | Editar platillos / cantidades | Mesero dueño `200`, otro mesero `403 FORBIDDEN_ROLE`, Admin `200` |
| `PATCH /api/v1/orders/{orderId}/transfer` | Transferir orden a otra mesa | Mesero dueño `200`, otro mesero `403 FORBIDDEN_ROLE`, Admin `200` |
| `POST /api/v1/orders/{orderId}/confirm` | Confirmar orden hacia Inventario/KDS | Mesero dueño `200`, otro mesero `403 FORBIDDEN_ROLE`, Admin `200` |
| `PATCH /api/v1/orders/{orderId}/cancel` | Cancelar orden (`CREATED`/`RESERVED`/`REJECTED`) | Mesero dueño `200`, otro mesero `403 FORBIDDEN_ROLE`, Admin `200` |
| `PATCH /api/v1/orders/{orderId}/items/{itemId}/redo` | Solicitar rehacer platillo (`DELIVERED`) | Mesero dueño `200`, otro mesero `403 FORBIDDEN_ROLE`, Admin `200` |
| `POST /api/v1/orders/{orderId}/request-bill` | Solicitar cuenta hacia Pagos | Mesero dueño `200`, otro mesero `403 FORBIDDEN_ROLE`, Admin `200` |

### Regla de evaluación
1. Cuando se crea una orden (`POST /orders` o al iniciar por evento de Sala), se asigna `responsible_waiter_id` al UUID v4 del mesero (`sub`/`user_id` del JWT o `meseroId` recibido en `sala.dining_session.iniciada`).
2. Si `order.responsible_waiter_id` es distinto de `caller_uuid` (y el usuario no tiene rol `ADMIN` ni permiso wildcard `*`), la API rechaza la petición con **`403 Forbidden`** (`error.code = "FORBIDDEN_ROLE"`).
3. El control se gobierna con la variable de entorno `ENFORCE_WAITER_OWNERSHIP` (por defecto `true`).

---

## 2. Brecha Detectada con Sala (`Floor-Module`)

Al revisar el contrato compartido de Sala (`Floor-Module/packages/shared/src/events/index.ts`), actualmente solo existen los siguientes eventos:
- `sala.dining_session.iniciada` (emite `sessionId`, `meseroId`, `mesaId` / `mesasIds`, `comensales` / `numComensales`)
- `sala.mesa.estado_actualizado`
- `sala.reserva.proxima_alerta`

**No existe un evento emitido por Sala cuando se reasigna un mesero a una mesa o sesión de comedor activa** (por ejemplo, al ejecutar la mutación/comando `sala.tables.assign_waiter` por cambio de turno, descanso o cobertura de emergencia).

---

## 3. Riesgo Operativo en Producción

1. El Mesero A abre mesa/sesión en Sala y crea la orden en `orders-backend` (`responsible_waiter_id = Waiter A`).
2. Más adelante, un gerente reasigna la mesa al Mesero B desde el módulo de Sala (`Floor-Module`).
3. Sala no notifica el cambio a Órdenes; `orders-backend` conserva `responsible_waiter_id = Waiter A`.
4. Cuando el Mesero B intenta agregar platillos, confirmar, transferir, pedir rehacer un platillo o pedir la cuenta, **`orders-backend` bloquea la operación con `403 FORBIDDEN_ROLE`**.
5. Como solución temporal únicamente operativa, un `ADMIN` tendría que intervenir manualmente cada operación o desactivar `ENFORCE_WAITER_OWNERSHIP=false` (perdiendo protección BOLA).

---

## 4. Contrato Propuesto para Alinear con Sala

Definir y publicar en RabbitMQ (`sala.topic`) un evento de reasignación de mesero para sesiones/órdenes activas:

- **Exchange:** `sala.topic` (o el exchange oficial de Sala acordado)
- **Routing key sugerida:** `sala.dining_session.mesero_reasignado` (alternativa: `sala.mesa.mesero_reasignado`)
- **Payload JSON propuesto:**

```json
{
  "eventId": "11111111-2222-4333-8444-555555555555",
  "eventType": "sala.dining_session.mesero_reasignado",
  "timestamp": "2026-11-25T18:00:00Z",
  "sessionId": "22222222-3333-4444-8555-666666666666",
  "tableId": "MESA-12",
  "tableNumber": 12,
  "previousWaiterId": "00000000-0000-0000-0000-000000000001",
  "newWaiterId": "00000000-0000-0000-0000-000000000002",
  "reassignedBy": "00000000-0000-0000-0000-000000000004"
}
```

### Requisitos clave de identidad
- Tanto `meseroId` (en `sala.dining_session.iniciada`) como `newWaiterId` deben corresponder exactamente al **`sub` (UUID v4) emitido en el JWT por Auth-Backend**, no a un `staffId` numérico ni a un ID interno distinto de Sala.
- Al recibir el evento, `orders-backend` actualizaría `responsible_waiter_id = newWaiterId` en las órdenes activas (no terminales) asociadas a `sessionId` (y registraría idempotencia por `eventId`).

---

## 5. Checklist de Acuerdos a Cerrar con el Líder de Sala

- [ ] Confirmar si `meseroId` en `sala.dining_session.iniciada` coincide 1:1 con el claim `sub` (UUID v4) del JWT de Auth.
- [ ] Aprobar nombre de evento, routing key y schema del evento de reasignación (`sala.dining_session.mesero_reasignado`).
- [ ] Confirmar si la reasignación en `Floor-Module` aplica a nivel de `sessionId` (todas las mesas de la sesión) o por `mesaId`.
- [ ] Definir política de despliegue: mantener `ENFORCE_WAITER_OWNERSHIP=true` (recomendado) una vez publicado el evento, o acordar ventana de transición con `ENFORCE_WAITER_OWNERSHIP=false` solo en pruebas controladas si Sala aún no emite el evento.
