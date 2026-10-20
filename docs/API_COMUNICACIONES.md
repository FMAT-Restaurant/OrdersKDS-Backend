# Arquitectura de Comunicación — Microservicio de Órdenes y Cocina (Orders & KDS)

> Este documento complementa `ordenes-kds-diagramas-secuencia.md`. Ese documento cubre **qué** eventos se intercambian con otros microservicios; este documento cubre **cómo** funciona internamente la API de Órdenes y Cocina para producir y consumir esos mensajes — tanto la capa síncrona (REST, hacia los clientes de este microservicio) como la capa asíncrona (broker, hacia/desde otros microservicios).

---

## 0. Aclaración previa: no hay llamadas síncronas hacia otros microservicios de negocio

Antes de listar cualquier llamada síncrona, vale dejar constancia de una restricción de diseño que ya está fijada en los tres documentos base del proyecto:

- El documento de diagramas de secuencia establece como supuesto de arquitectura que el proyecto es **100% orientado a eventos**, **sin llamadas síncronas entre microservicios de negocio**.
- El documento de requisitos funcionales no describe ningún endpoint que Órdenes deba invocar en Inventario, Pagos o Catálogo; toda interacción con esos dominios se describe como publicar o consumir eventos del broker.
- La especificación de UI lo confirma explícitamente al corregir el Requisito 7: el envío de la comanda a la cola del KDS **no ocurre de forma directa** al confirmar el pedido, sino **tras consumir** el evento `inventario.stock.reservado` — es decir, ni siquiera ese flujo interno usa una llamada síncrona esperando respuesta HTTP; es una reacción a un evento.

**Conclusión para este documento:** en el camino de cada request de usuario (crear orden, ver catálogo, marcar platillo listo, etc.) la lista de "llamadas API síncronas de Órdenes hacia otros microservicios" sigue siendo, por diseño, una lista vacía — eso no cambia. Sí se identificó **una única excepción legítima y acotada**: una llamada síncrona saliente hacia Catálogo y Menú, pero exclusivamente para **arranque inicial y reconciliación periódica** de la vista materializada del diagrama 7, nunca como parte de atender a un mesero o a la pantalla KDS (ver sección 1.4). Cualquier otra necesidad de consulta síncrona que surja más adelante sigue representando una **desviación de la arquitectura acordada** y debería documentarse y aprobarse explícitamente, no añadirse silenciosamente.

Lo que sí existe — y es el tema central de este documento — son tres superficies de comunicación **dentro** de este microservicio:

| Capa | Dirección | Protocolo | Hacia quién |
|---|---|---|---|
| API síncrona | Entrante | HTTP/REST (FastAPI) | Clientes de este microservicio: app del mesero, pantalla KDS |
| Sincronización de arranque (excepción) | Saliente, fuera del camino de request de usuario | HTTP/REST síncrono | Catálogo y Menú — solo bootstrap/reconciliación (ver 1.4) |
| Mensajería asíncrona | Saliente y entrante | AMQP vía RabbitMQ (Broker) | Otros microservicios de negocio: Inventario, Pagos, Catálogo/Menú, Sala |

---

## 1. Capa síncrona: API REST expuesta por Órdenes y Cocina

Esta es la única superficie síncrona real del microservicio. No comunica microservicios entre sí — comunica a los **dispositivos físicos del restaurante** (handhelds Android del mesero, monitores táctiles con teclado/Bump Bar secundario en cocina) con el backend de Órdenes.

### 1.1 Principios de diseño de esta capa

- **Autenticación por token JWT en el encabezado HTTP.** El identificador del usuario autenticado (mesero, cocinero, administrador) que exige el Requisito 4 de trazabilidad **no** se captura como campo de formulario en ninguna vista — se extrae del token en cada petición. Esto significa que el middleware de autenticación debe inyectar `usuarioId` (y su rol) en el contexto de cada request antes de que llegue al controlador, para que la lógica de negocio pueda registrar quién ejecutó cada transición de estado.
- **Cada endpoint que cambia estado dispara una transacción, no una llamada a otro microservicio.** El controlador nunca necesita esperar una respuesta remota síncrona para responder al cliente; responde en cuanto el cambio de estado y el registro en la tabla `outbox` quedan confirmados en PostgreSQL (ver sección 2).
- **Los efectos de fondo (reserva de stock, cálculo de cuenta, etc.) no son parte de la respuesta HTTP.** El mesero recibe `200 OK` a "confirmar orden" cuando la orden queda en estado `Pendiente de confirmación de stock`, no cuando Inventario ya respondió. La actualización real a "en KDS" o "Rechazada" llega después, empujada por WebSocket (sección 1.3).
- **Autorización por rol** debe validarse en el controlador antes de la lógica de negocio: mesero vs. administrador tienen distintos permisos sobre comandas ya avanzadas a "En preparación" o "Listo" (Requisito 6).

### 1.2 Endpoints propuestos por vista

Los documentos fuente describen las capacidades funcionales y las vistas de UI, pero no fijan nombres exactos de rutas/payloads — estos endpoints son una **propuesta a validar con el equipo**, derivada directamente de los requisitos y de las tres vistas de la especificación de UI:

**Vista 1 — Monitor de Mesas Activas (mesero)**
| Método | Ruta propuesta | Requisito | Nota |
|---|---|---|---|
| `GET` | `/orders?status=` | 5 | Estado por color de cada mesa/comanda |
| `POST` | `/orders` | 4 | Crea comanda vinculada a mesa, o `tipo: "para_llevar"` sin mesa física |

**Vista 2 — Gestor de Comanda (mesero)**
| Método | Ruta propuesta | Requisito | Nota |
|---|---|---|---|
| `PATCH` | `/orders/{orderId}/items` | 4 | Agrega platillos, modificadores, notas — solo si `estado = Creada` y sin `confirmed_at` |
| `PATCH` | `/orders/{orderId}/transfer` | 4 | Cambia la mesa asociada a la comanda |
| `POST` | `/orders/{orderId}/confirm` | 7 | Idempotente: fija `confirmed_at` y dispara la publicación de `ordenes.orden.creada` vía outbox (OP-05) |
| `PATCH` | `/orders/{orderId}/cancel` | 6 | Solo permitido si `estado = Creada`; dispara `ordenes.orden.cancelada` |
| `PATCH` | `/orders/{orderId}/items/{itemId}/redo` | 5 | Regresa un ítem de "Entregado" a "Pendiente"; requiere cuerpo `{ "note": "..." }` obligatorio (la orden conserva "Entregada", OP-12) |
| `POST` | `/orders/{orderId}/request-bill` | — | Dispara `ordenes.cuenta.solicitada` |

**Vista 3 — Pantalla KDS (cocina: monitor táctil principal, teclado/Bump Bar secundario)**
| Método | Ruta propuesta | Requisito | Nota |
|---|---|---|---|
| `GET` | `/kds/queue?station=&view=` | 1, 2 | Comandas ordenadas por mayor tiempo en cocina primero (OP-11); filtradas por estación (OP-09); vista principal activa (`view=active`, por defecto) vs apartado de entregadas (`view=delivered`); excluye "Pagada" |
| `PATCH` | `/orders/{orderId}/items/{itemId}/ready` | 3 | Marca un platillo como "Listo"; dispara `ordenes.platillo.preparado` |
| `PATCH` | `/orders/{orderId}/ready` | 3 | Marca la comanda completa como "Listo" |

**Acciones administrativas**
| Método | Ruta propuesta | Requisito | Nota |
|---|---|---|---|
| `PATCH` | `/orders/{orderId}/void` (o `/items/{itemId}/void`) | 6 | Solo rol admin, sobre comandas ya en "EnPreparacion"; dispara `ordenes.comanda.mermada` + `ordenes.cobro.solicitado` (Mermada) |
| `PATCH` | `/orders/{orderId}/void-kitchen-error` | — | Rol admin o chef; cuerpo `{ "note": "..." }` obligatorio; cancela la comanda completa por error de cocina (Anulada); dispara `ordenes.comanda.mermada` sin cobro (OP-07) |

**Vista 4 — Preparación de Platillos Intermedios (chef) — [NO-GO en fase actual / Abierto a futuro]**
| Método | Ruta propuesta | Requisito | Nota |
|---|---|---|---|
| `POST` | `/intermediate-dishes` | — | **Descartado para el horizonte de 7 sprints (ADR-0011):** Funcionalidad opcional desactivada (`INTERMEDIATE_DISHES_ENABLED=false`). **Queda abierta la posibilidad de implementarse en una fase futura o posterior release**, reactivando el feature flag y publicando el evento asíncrono `ordenes.platillo_intermedio.preparado` sin alterar la arquitectura base. |

> Cualquier endpoint de esta tabla que no coincida exactamente con lo que finalmente implemente el equipo de frontend debe corregirse aquí — esta tabla es la propuesta de contrato, no el contrato firmado.

### 1.3 Notificación de vuelta al cliente: canales WebSocket (OP-02 / ADR-0002)

Cuando un cambio de estado ocurre por reacción a un evento del broker (por ejemplo, `inventario.stock.reservado` llega y la comanda pasa a la cola del KDS), no hay ninguna petición HTTP en curso a la cual responder. Por eso la especificación indica que Órdenes informa al frontend **vía WebSocket** para que la pantalla se redibuje sola, sin que el cliente tenga que hacer polling.

Se definen dos endpoints y canales estructurados por rol:

| Canal | Endpoint | Roles | Modo de suscripción | Contenido recibido |
|---|---|---|---|---|
| `kds:station:{station}` | `/ws/kds` | `kitchen`, `chef`, `admin` | Solicitado por el cliente con mensaje `subscribe`; cambio de estación sin reconectar | Cambios de estado de órdenes con ítems de esa estación y de sus platillos |
| `kds:all` | `/ws/kds` | `kitchen`, `chef`, `admin` | Solicitado por el cliente (vista global) | Todas las órdenes activas en cocina |
| `floor` | `/ws/waiter` | `waiter`, `admin` | Automático al conectar | Cambios de estado de todas las órdenes activas para monitores de Sala y Menú |
| `user:{userId}` | `/ws/waiter` | `waiter`, `admin` | Automático al conectar (`userId` del JWT) | Alertas de órdenes del usuario: `REJECTED`, `EXPIRED` y `DELIVERED` ("lista para recoger") |

- **Tipos de mensaje:** `order.status_changed` e `item.status_changed`, con sobre en formato camelCase y `version` monótona para descartar eventos fuera de orden.
- **Seguridad y autenticación (ADR-0011):** La firma del JWT es verificada exclusivamente en el API Gateway; el backend únicamente extrae `user_id` y `role` de las claims del token sin mantener llaves locales ni `JWT_SECRET`. El token no viaja en la URL; cierre con código `4401` ante token ausente o inválido y `4403` si el rol no corresponde al endpoint; respuesta con error `{ "error": "FORBIDDEN_CHANNEL" }` ante suscripciones a canales no autorizados.

```mermaid
sequenceDiagram
    participant M as App Mesero / KDS
    participant API as API REST (FastAPI)
    participant WS as Canal WebSocket
    participant DB as PostgreSQL

    M->>API: POST /orders/{id}/confirm (JWT)
    API->>DB: INSERT/UPDATE orden + outbox (misma transacción)
    API-->>M: 200 OK (estado: pendiente de confirmación)
    Note over API,DB: Ver sección 2 — el Outbox Worker<br/>publica el evento de forma independiente

    Note over DB: ...tiempo después, llega inventario.stock.reservado (broker)...
    DB->>WS: Estado actualizado a "En cola KDS"
    WS-->>M: push: comanda visible en pantalla de cocina
```

### 1.4 Excepción documentada: llamada síncrona saliente hacia Catálogo y Menú (bootstrap y reconciliación)

Los eventos `menu.platillo.precio_actualizado` y `menu.catalogo.actualizado` (sección 2.2) son **deltas**: solo informan qué cambió. Eso funciona bien mientras Órdenes ya tiene una copia local completa y consistente para irle aplicando esos cambios — pero deja dos huecos que los eventos por sí solos no resuelven:

- **Arranque inicial**: la primera vez que el microservicio se despliega, o si su base de datos se restaura desde cero, no existe todavía ninguna copia local sobre la cual aplicar deltas. No hay forma de "reproducir" desde eventos pasados a menos que Menú ofrezca un log de eventos re-consumible desde el origen, lo cual no está documentado como parte de su arquitectura.
- **Reconciliación por drift**: si en algún momento se pierde un evento (una caída del consumer, un mensaje mal encolado, etc.), la vista local queda desalineada del catálogo real de forma silenciosa — nadie se entera hasta que un mesero intenta vender un platillo con precio o disponibilidad incorrectos.

Para ambos casos, la solución es una llamada síncrona **acotada a un proceso de sistema, nunca a una request de usuario**:

- **Disparadores**: (a) rutina de arranque del microservicio (`on startup`), antes de aceptar tráfico si la tabla local está vacía; (b) job programado de reconciliación (por ejemplo, cada N horas) — frecuencia a definir con el equipo según qué tan seguido cambia el menú en producción.
- **Endpoint propuesto en Menú**: `GET /menu/catalogo/completo` (o equivalente) — una llamada síncrona real hacia otro microservicio, y por eso debe quedar explícitamente aprobada por el equipo de Catálogo y Menú, no asumida.
- **Comportamiento al recibir la respuesta**: comparar el snapshot completo contra la vista local; aplicar upserts fila por fila; si un ítem local no aparece en el snapshot (fue eliminado del catálogo) o viceversa, registrar la discrepancia. Un volumen alto de discrepancias en una reconciliación es una señal de que se están perdiendo eventos aguas arriba y merece alerta, no solo corrección silenciosa.
- **No debe pisar cambios más recientes**: si un evento en tiempo real actualizó un ítem después de que arrancó la reconciliación pero antes de que termine, la reconciliación no debe sobrescribirlo con un dato más viejo. Conviene comparar por timestamp (`updated_at` local vs. el que traiga el snapshot) antes de aplicar cada upsert, no reemplazar la tabla completa a ciegas.
- **Tolerancia a fallos**: si Menú no responde durante el arranque o la reconciliación, Órdenes debe seguir funcionando con su última copia local conocida (aunque esté algo desactualizada) en vez de bloquear su propia disponibilidad — la meta es no propagar una caída de Menú hacia el mesero o la cocina. Reintentar la sincronización más tarde y alertar si el atraso se vuelve significativo.
- **Fuera de alcance de esta llamada**: la Vista 2 (pestaña de catálogo del mesero) y el filtrado del KDS por estación siguen leyendo exclusivamente la vista local, tal como está en la sección 2.2 — esta llamada síncrona nunca se ejecuta como parte de atender esas pantallas.

---

## 2. Capa asíncrona: cómo esta API produce y consume eventos

Esta es la parte que efectivamente conecta a Órdenes con Inventario, Pagos, Catálogo/Menú y Sala — y es asíncrona en ambos sentidos.

### 2.0 Matriz Resumen de Comunicaciones (Español ↔ Inglés ↔ Código ↔ Broker)

Para mantener la alineación estricta entre la documentación de diseño (en español por entendimiento de equipo), el código fuente (`app/messaging/events.py` y `DEVELOPMENT_GUIDELINES.md` §2.4, formalizados en inglés) y los contratos pactados con otros microservicios (como el contrato acordado con Edwin para Pagos), se establece la siguiente matriz canónica de equivalencias:

| Evento en Broker (Routing Key ES) | Routing Key / Contrato EN | Constante en Código (`EventName`) | Dirección | Microservicio | Estado del Contrato |
|---|---|---|---|---|---|
| `ordenes.orden.creada` | `orders.order.created` | `ORDER_CREATED` | Publica | Inventario | Confirmado |
| `ordenes.orden.cancelada` | `orders.order.cancelled` | `ORDER_CANCELLED` | Publica | Inventario | Confirmado |
| `ordenes.platillo.preparado` | `orders.dish.prepared` | `DISH_PREPARED` | Publica | Inventario | Confirmado |
| `ordenes.comanda.mermada` | `orders.order.wasted` | `ORDER_WASTED` | Publica | Inventario | Confirmado (`ADMINISTRATIVE` \| `KITCHEN_ERROR`) |
| `ordenes.cuenta.solicitada` | `orders.bill.requested` | `BILL_REQUESTED` | Publica | Pagos | **Confirmado (Contrato Edwin)** |
| `ordenes.cobro.solicitado` | `orders.charge.requested` | `CHARGE_REQUESTED` | Publica | Pagos | **Confirmado (Contrato Edwin)** |
| `ordenes.orden.expirada` | `orders.order.expired` | `ORDER_EXPIRED` | Publica | Inventario | Propuesto (OP-04) |
| `sala.dining_session.iniciada` | `dining.session.started` | `DINING_SESSION_STARTED` | Consume | Sala | Confirmado |
| `inventario.stock.reservado` | `inventory.stock.reserved` | `STOCK_RESERVED` | Consume | Inventario | Confirmado |
| `inventario.stock.insuficiente` | `inventory.stock.insufficient` | `STOCK_INSUFFICIENT` | Consume | Inventario | Confirmado |
| `menu.platillo.precio_actualizado` | `menu.dish.price_updated` | `DISH_PRICE_UPDATED` | Consume | Catálogo y Menú | Confirmado |
| `menu.catalogo.actualizado` | `menu.catalog.updated` | `CATALOG_UPDATED` | Consume | Catálogo y Menú | Confirmado |
| `pagos.pago.completado` | `payments.payment.completed` | `PAYMENT_COMPLETED` | Consume | Pagos | **Confirmado (Contrato Edwin)** |

> **Regla de nomenclatura (`DEVELOPMENT_GUIDELINES.md` §2.4):** Las cadenas de enrutamiento en español residen exclusivamente aisladas en `app/messaging/events.py` y `contracts/events/`. En toda la lógica de negocio, repositorios y servicios se utilizan únicamente los identificadores tipados en inglés (`EventName.BILL_REQUESTED`, `EventName.PAYMENT_COMPLETED`, etc.).

---

### 2.1 Publicación: patrón Transactional Outbox

Ningún endpoint de la sección 1 publica directamente al broker. En su lugar:

1. El controlador ejecuta la lógica de negocio dentro de **una sola transacción** de PostgreSQL.
2. Esa transacción hace dos cosas atómicamente: (a) escribe el cambio de estado en las tablas de dominio (`orders`, `order_items`), y (b) inserta una fila en la tabla `outbox` con el evento a publicar (tipo, payload, `orderId`/`sessionId`, timestamp).
3. Un proceso independiente, el **Event Outbox Worker**, hace polling (o escucha vía CDC) de filas no publicadas en `outbox`, las publica al exchange de RabbitMQ con el routing key correspondiente, y marca la fila como publicada.

Esto garantiza que **nunca** se publique un evento sin que el cambio de estado correspondiente ya esté confirmado en base de datos, y viceversa — no hay escenario de "evento publicado pero orden no guardada" ni al revés.

Eventos que salen de este microservicio por Transactional Outbox:

| Evento (Routing Key) | Disparado por | Payload resumen | Consumidor | Estado |
|---|---|---|---|---|
| `ordenes.orden.creada` | `POST /orders/{id}/confirm` | `{orderId, items[]}` | Inventario | Confirmado |
| `ordenes.orden.cancelada` | `PATCH /orders/{id}/cancel` | `{orderId, itemsCancelados[]}` | Inventario | Confirmado |
| `ordenes.platillo.preparado` | `PATCH /orders/{id}/items/{itemId}/ready` | `{orderId, itemId}` | Inventario | Confirmado |
| `ordenes.comanda.mermada` | `PATCH /orders/{id}/void` o `/void-kitchen-error` | `{orderId, itemsAfectados[], reason}` | Inventario | Confirmado (`reason`: `ADMINISTRATIVE` \| `KITCHEN_ERROR`) |
| `orders.bill.requested` (`ordenes.cuenta.solicitada`) | `POST /orders/{id}/request-bill` | `{orderId, sessionId, tableId, waiterId, totalAmount, items[]}` | Pagos | **Confirmado (Contrato Edwin)** |
| `orders.charge.requested` (`ordenes.cobro.solicitado`) | `PATCH /orders/{id}/void` (solo `WASTED`) | `{orderId, sessionId, tableId, waiterId, totalAmount, items[]}` | Pagos | **Confirmado (Contrato Edwin)** |
| `ordenes.orden.expirada` | Watcher tras 60 s desde `confirmed_at` | `{orderId, elapsedSeconds}` | Inventario | Propuesto (OP-04) |

---

### 2.2 Consumo: listeners reaccionando a eventos externos

En sentido inverso, Órdenes mantiene *consumers* suscritos a las colas del broker para los eventos de otros dominios. Cada consumer:

- Recibe el mensaje del broker.
- Valida idempotencia (ver 2.4) antes de tocar la base de datos.
- Ejecuta la actualización de estado dentro de una transacción.
- Si esa actualización debe reflejarse en la UI en vivo, dispara el push por WebSocket descrito en 1.3.

Eventos consumidos por este microservicio:

| Evento consumido | Origen | Efecto en Órdenes | Estado |
|---|---|---|---|
| `sala.dining_session.iniciada` (`dining.session.started`) | Sala | Crea el registro base de la orden (semilla), usando `sessionId` como identificador de correlación | Confirmado |
| `inventario.stock.reservado` | Inventario | Pasa la comanda a `IN_PREPARATION`, estampa `in_preparation_at` y envía a la cola del KDS | Confirmado |
| `inventario.stock.insuficiente` | Inventario | Marca la comanda como `REJECTED` + alerta WebSocket a mesero | Confirmado |
| `menu.platillo.precio_actualizado` | Catálogo y Menú | Actualiza precio en la vista materializada local (Redis/DB) | Confirmado |
| `menu.catalogo.actualizado` | Catálogo y Menú | Actualiza disponibilidad, categoría y estación en la vista local | Confirmado |
| `payments.payment.completed` (`pagos.pago.completado`) | Pagos y Facturación | Marca la comanda como `PAID` (estado terminal) y la retira de KDS | **Confirmado (Contrato Edwin)** |

---

### 2.3 Especificación del Contrato de Integración con Pagos (Contrato con Edwin)

A partir del acuerdo formal con el equipo de Pagos y Facturación (`contrato_ordenes_pagos_edwin.md`), se establecen los siguientes flujos operativos y estructuras de datos:

#### A. Flujos hacia Pagos
1. **Solicitud Normal de Cuenta (`orders.bill.requested` / `ordenes.cuenta.solicitada`):**
   - **Disparador:** El mesero solicita la cuenta desde su app móvil una vez que todos los platillos han sido completados (`status = DELIVERED`).
2. **Cobro por Merma Administrativa (`orders.charge.requested` / `ordenes.cobro.solicitado`):**
   - **Disparador:** Un administrador anula una comanda ya en preparación (`status = WASTED`). Dado que los insumos fueron elaborados y el restaurante no asume la pérdida (RF-26, RF-27), se envía a Pagos con el total acumulado de lo elaborado.
   - **Exclusión por Error de Cocina:** Si la orden se cancela por error interno de cocina (`status = VOIDED`), el restaurante absorbe el coste y **no se envía ningún evento a Pagos**.

#### B. Estructura del Payload Emitido hacia Pagos
Ambos eventos comparten el mismo esquema estandarizado:

```json
{
  "eventId": "b9f2a41d-9e33-4a12-8f2c-123456789abc",
  "eventType": "orders.bill.requested",
  "timestamp": "2026-10-14T12:00:00Z",
  "data": {
    "orderId": "d3b07384-d113-4674-87f5-a7b290eb4e51",
    "sessionId": "a1c2e3f4-5678-90ab-cdef-1234567890ab",
    "tableId": "MESA-04",
    "waiterId": "f8a12345-6789-abcd-ef01-234567890123",
    "status": "DELIVERED",
    "totalAmount": 485.50,
    "items": [
      {
        "itemId": "e1f2a3b4-1111-2222-3333-444455556666",
        "plateId": "PLT-101",
        "name": "Hamburguesa Clásica",
        "quantity": 2,
        "unitPrice": 180.00,
        "subtotal": 360.00,
        "modifiers": [
          {
            "modifierId": "MOD-01",
            "name": "Extra Queso",
            "additionalPrice": 25.00
          }
        ],
        "notes": "Término medio, sin cebolla"
      }
    ]
  }
}
```

#### C. Evento de Respuesta Recibido desde Pagos (`payments.payment.completed`)
Al confirmarse la transacción monetaria en Pagos:

```json
{
  "eventId": "c7a8b9d0-1234-5678-90ab-cdef12345678",
  "eventType": "payments.payment.completed",
  "timestamp": "2026-10-14T12:05:00Z",
  "data": {
    "orderId": "d3b07384-d113-4674-87f5-a7b290eb4e51",
    "sessionId": "a1c2e3f4-5678-90ab-cdef-1234567890ab",
    "paymentId": "PAY-987654",
    "paymentMethod": "CARD",
    "totalPaid": 485.50,
    "status": "PAID"
  }
}
```

#### D. Principios Clave del Contrato con Pagos
1. **Doble Identificador:** Se envían tanto `orderId` (clave primaria de Órdenes) como `sessionId` (sesión activa de Sala). Pagos debe devolver **obligatoriamente `orderId`** para que Órdenes actualice la comanda a `PAID`.
2. **Precios Congelados:** El cobro se realiza con `unitPrice`, `quantity` y `modifiers` provistos en el mensaje; no se re-consulta Menú durante el cobro.
3. **Divisas e Impuestos:** Órdenes no maneja multidivisa ni facturación; el cálculo de IVA, propinas y pasarelas es competencia exclusiva de Pagos.

---

### 2.4 Idempotencia y Deduplicación

Como el broker puede reentregar mensajes, tanto los publicados como los consumidos deben validarse contra una clave de deduplicación antes de aplicar efecto:
- `orderId + eventId` para los flujos de creación, reserva, cobro y cancelación.
- `sessionId + eventId` específicamente para el consumo de `sala.dining_session.iniciada`, ya que en ese punto todavía no existe un `orderId`.

---

### 2.5 Diagrama combinado: de la petición HTTP al evento en el broker

```mermaid
flowchart TB
    subgraph Sincrono["Capa síncrona (dentro de Órdenes y Cocina)"]
        C[Cliente: App Mesero / KDS] -->|HTTP + JWT| CTRL[Controlador REST]
        CTRL --> BL[Lógica de negocio]
        BL --> TX[(Transacción PostgreSQL:<br/>estado + outbox)]
    end

    subgraph Asincrono["Capa asíncrona (hacia/desde otros microservicios)"]
        TX -.->|polling| WORKER[Event Outbox Worker]
        WORKER -->|publish| BROKER{{Broker RabbitMQ}}
        BROKER -->|consume| CONSUMER[Consumers de Órdenes]
        CONSUMER --> TX2[(Transacción PostgreSQL:<br/>actualiza estado)]
    end

    BROKER -->|hacia| OTROS[Inventario / Pagos / Menú]
    OTROS -->|desde| BROKER

    TX2 -.->|push| WS[Canal WebSocket]
    WS -.-> C
```

---

## 3. Puntos abiertos y estado de resolución

1. ~~**Contratos de los endpoints REST de la sección 1.2**~~ — **[RESUELTO]** Rutas y parámetros estandarizados en inglés (`?status=`, `?station=`, `/void-kitchen-error`, `/intermediate-dishes` descartado, cuerpo `note`; OP-07 / ADR-0006).
2. ~~**Timeout de Saga**~~ — **[RESUELTO]** Fijado en 60 s como máximo desde `confirmed_at` con watcher cada 5 s (OP-01 / ADR-0001); Inventario consume `ordenes.orden.expirada` (OP-04) para liberar reservas tardías.
3. ~~**Alcance exacto del canal WebSocket**~~ — **[RESUELTO]** Conexión en dos endpoints (`/ws/kds` y `/ws/waiter`) con canales `kds:station:{station}`, `kds:all`, `floor` y `user:{userId}` (OP-02 / ADR-0002).
4. ~~**Contrato con Pagos y Facturación**~~ — **[RESUELTO]** Especificación cerrada y validada con Edwin (`contrato_ordenes_pagos_edwin.md`), formalizando `orders.bill.requested`, `orders.charge.requested` y `payments.payment.completed` (§2.3).
5. ~~**Autenticación y firmas JWT**~~ — **[RESUELTO]** Validación de firma delegada al API Gateway; sin gestión de `JWT_SECRET` en este servicio (ADR-0011).
6. **Contrato del endpoint `GET /menu/catalogo/completo` (sección 1.4)** — Pendiente de aprobación final con el equipo de Catálogo y Menú para la sincronización de arranque / reconciliación.
7. **Frecuencia del job de reconciliación** (sección 1.4) — Pendiente de acordar según la volatilidad de precios en producción.
8. **OP-04: Contrato de `ordenes.orden.expirada` y campo `motivo`** — Pendiente de confirmar con Inventario (Saga timeout vs rechazo).
9. **OP-13: Notificación de orden entregada al mesero** — Pendiente de definir con Sala si se consume en monitor de Sala o en widget de mesero.
