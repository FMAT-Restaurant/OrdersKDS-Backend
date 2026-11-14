# Análisis Profundo de Integración: Orders & KDS Backend con Menú e Inventario (RabbitMQ & Saga)

> [!IMPORTANT]
> **Propósito del Documento:**
> Este análisis técnico sirve como documento base y de negociación para la **reunión de integración inter-servicios de esta noche** con los equipos de **Menú** e **Inventario**, así como para guiar las pruebas de mensajería con la configuración de **RabbitMQ** compartida por Héctor.
> 
> **Condición de No-Regresión:** Todas las adaptaciones aquí descritas **preservan intacta la compatibilidad con el microservicio de Auth** (Kong API Gateway PBAC, JWT con roles, cabeceras `X-User-Id` / `X-User-Roles`) y las reglas de negocio existentes de Orders & KDS.

---

## 1. Resumen Ejecutivo y Estado de Situación

En la arquitectura de microservicios de FMAT Restaurant, la creación y confirmación de una comanda involucra un ciclo distribuido (Saga) entre tres dominios:
1. **Menú:** Provee el catálogo comercial de platillos y ofertas disponibles.
2. **Orders & KDS:** Orquesta la comanda del mesero, coordina los estados de preparación en cocina y solicita la reserva y consumo de stock.
3. **Inventario:** Conoce las recetas y almacenes físicos (materias primas), reserva ingredientes con expiración temporal (TTL) y realiza los consumos definitivos por método FEFO.

Para la reunión de esta noche se acordó implementar la **Opción A (Pre-reserva con TTL, Reserva Firme en cola de KDS y Consumo al pasar a `IN_PREPARATION`)**. Sin embargo, la auditoría cruzada del código de `orders-backend`, `inventory-backend` y el módulo compartido de RabbitMQ de Héctor revela **discrepancias críticas a nivel de broker y contratos** que impedirían la comunicación si no se homogenizan antes de probar.

---

## 2. Auditoría Técnica de RabbitMQ: Órdenes vs Broker de Héctor vs Inventario

```mermaid
flowchart TD
    subgraph Orders["Orders & KDS (Python / aio-pika)"]
        O_Pub["Publisher (OutboxWorker)<br/>Exchange: ordenes.topic<br/>durable: true"]
        O_Sub["Consumer (Dispatcher)<br/>DLQ Router"]
    end

    subgraph Broker["RabbitMQ Broker (AMQP 5672 / UI 15672)"]
        ExOrders["Exchange: ordenes.topic (topic, durable: true)"]
        ExInv["Exchange: inventory.topic (topic, durable: true)"]
        ExMenu["Exchange: menu.topic (topic, durable: true)"]
        Q_Orders["Queue: cola_orders_events"]
        Q_InvInbound["Queue: orders.inventory.inbound"]
    end

    subgraph Inventory["Inventory Backend (NestJS / amqplib)"]
        I_Sub["OrdersEvents Consumer<br/>Queue: cola_orders_events"]
        I_Pub["ReservationService Publisher<br/>Exchange: inventory.topic"]
    end

    O_Pub -->|ordenes.order.created<br/>ordenes.order.confirmed<br/>ordenes.order.cancelled| ExOrders
    ExOrders -->|Binding: ordenes.order.*| Q_Orders
    Q_Orders --> I_Sub

    I_Pub -->|inventory.reservation.confirmed<br/>inventory.reservation.rejected<br/>inventory.reservation.consumed| ExInv
    ExInv -->|Binding: inventory.reservation.*| Q_InvInbound
    Q_InvInbound --> O_Sub
```

### 2.1 Verificación de Durabilidad en Exchanges (`durable: true`)

* **Auditoría Cruzada (Snippet antiguo de `infogeneral` vs Repositorio Actual de `inventory-backend`):**
  * En el prototipo inicial compartido semanas atrás en `infogeneral/broker/broker/`, se observaba `{ durable: false }`.
  * **Verificación con el repositorio actual:** Al auditar el código fuente más reciente en [`inventory-backend/src/broker/event-publisher.service.ts`](file:///c:/Users/j_pab/Documents/dev/fmat-restaurant/inventory-backend/src/broker/event-publisher.service.ts#L129) y [`event-customer.service.ts`](file:///c:/Users/j_pab/Documents/dev/fmat-restaurant/inventory-backend/src/broker/event-customer.service.ts#L161), se constata que el equipo de Inventario **ya actualizó sus exchanges y colas a `{ durable: true }`**:
    ```typescript
    // En inventory-backend (ACTUALIZADO):
    await this.channel.assertExchange(exchange, 'topic', { durable: true });
    ```
  * En [`orders-backend/app/messaging/publisher.py`](file:///c:/Users/j_pab/Documents/dev/fmat-restaurant/orders-backend/app/messaging/publisher.py#L85), nosotros también declaramos:
    ```python
    self._exchange = await self._channel.declare_exchange(
        name=self._exchange_name,
        type=aio_pika.ExchangeType.TOPIC,
        durable=True,
    )
    ```
* **Conclusión Positiva:**
  **La durabilidad ya está alineada en ambos repositorios en `durable: true`.** No existirá el error `406 PRECONDITION_FAILED` por discrepancia de durabilidad al conectar a `ordenes.topic` ni a `inventory.topic`. Ambos servicios operan bajo el estándar de persistencia de grado de producción.

---

### 2.2 Alerta Crítica 2: Discrepancia en Nombres de Exchanges y Routing Keys

Actualmente existe una divergencia de nombres e idiomas entre los tres microservicios:

| Concepto / Evento | `orders-backend` (Actual en código) | `inventory-backend` (`event-conventions.ts`) | Conclusión / Acuerdo para Hoy |
| :--- | :--- | :--- | :--- |
| **Exchange de Órdenes** | `RABBITMQ_EXCHANGE` (ej. `amq.topic` o `ordenes.events`) | `ORDERS_EXCHANGE = 'ordenes.topic'` | **Fijar `ordenes.topic`** como exchange estándar. |
| **Exchange de Inventario** | Espera eventos en su propio exchange configurado | `INVENTORY_EXCHANGE = 'inventory.topic'` | **Fijar `inventory.topic`** para respuestas de inventario. |
| **Exchange de Menú** | Escucha eventos de catálogo | `MENU_EXCHANGE = 'menu.topic'` | **Fijar `menu.topic`**. |
| **Orden Creada (Solicitud Reserva)** | `ordenes.orden.creada` | `ordenes.order.created` | **Soporte dual en routing keys** (ver sección 2.4). |
| **Orden Confirmada (Reserva Firme)** | No emitía confirmación explícita a broker | `ordenes.order.confirmed` | Emitir `ordenes.order.confirmed` al pasar a `RESERVED`. |
| **Orden Cancelada / Expirada** | `ordenes.orden.cancelada` | `ordenes.order.cancelled` | Emitir `ordenes.order.cancelled` para liberar stock. |
| **Orden en Preparación (Consumo)** | `ordenes.orden.en_preparacion` | `ordenes.order.served` (Inventory) | Emitir `ordenes.order.in_preparation` / `served`. |
| **Reserva Exitosa (Inbound a Orders)** | `inventario.stock.reservado` | `inventory.reservation.confirmed` | **Crítico:** Si Orders solo escucha `inventario.stock.reservado`, ignorará `inventory.reservation.confirmed`. |
| **Reserva Fallida (Inbound a Orders)** | `inventario.stock.insuficiente` | `inventory.reservation.rejected` | **Crítico:** Si Orders solo escucha `inventario.stock.insuficiente`, enviará a DLQ `inventory.reservation.rejected`. |

---

### 2.3 Alerta Crítica 3: Discrepancia de Payload en Ítems (`offerId` vs `plateId` vs `itemId`)

En [`inventory-backend/src/modules/reservation/orders.events.ts`](file:///c:/Users/j_pab/Documents/dev/fmat-restaurant/inventory-backend/src/modules/reservation/orders.events.ts#L204-L215), el parser de Inventario ejecuta:
```typescript
private parseItem(raw: unknown): OrderEventItem | null {
  const quantity = this.toPositiveQuantity(raw.quantity);
  if (quantity === null) return null;

  const plateId = this.getNonEmptyString(raw.plateId);
  if (plateId) return { plateId, quantity };

  const itemIdDirect = this.getNonEmptyString(raw.itemId);
  if (itemIdDirect) return { itemId: itemIdDirect, quantity };

  return null; // ¡SI NO VIENE plateId NI itemId, DESCARTA EL ÍTEM!
}
```
* **El Problema en `orders-backend`:**
  En [`OrderCreatedItem`](file:///c:/Users/j_pab/Documents/dev/fmat-restaurant/orders-backend/app/messaging/events.py#L93-L105), el alias de serialización es `serialization_alias="offerId"`. Al enviarse el JSON, solo sale `"offerId": "dish-uuid"`.
  Como Inventario busca `raw.plateId` o `raw.itemId` y **no evalúa `raw.offerId`**, `parseItem` retorna `null`, el arreglo queda vacío (`items: []`) e Inventario **rechaza automáticamente todas las órdenes** diciendo que no tienen ítems válidos.
* **Solución Técnica:**
  En `orders-backend`, serializar el ítem con campos universales compatibles:
  ```json
  {
    "offerId": "b3e34b9f-6fa1-4a55-93df-520e5c8e312a",
    "plateId": "b3e34b9f-6fa1-4a55-93df-520e5c8e312a",
    "itemId": "b3e34b9f-6fa1-4a55-93df-520e5c8e312a",
    "name": "Hamburguesa Clásica",
    "quantity": 2
  }
  ```

---

### 2.4 Alerta Crítica 4: Despachador de Eventos Inbound (`base.py`) y Descarte hacia DLQ

En [`orders-backend/app/messaging/consumers/base.py`](file:///c:/Users/j_pab/Documents/dev/fmat-restaurant/orders-backend/app/messaging/consumers/base.py#L123-L134):
```python
stock_res_keys = (EventName.STOCK_RESERVED, "stock_reserved", "stock.reservado")
if any(k in target_lower for k in stock_res_keys):
    msg_res = StockReservedMessage.model_validate(parsed_data)
    return await handle_stock_reserved(msg_res, uow, notifier=notifier)

stock_ins_keys = (EventName.STOCK_INSUFFICIENT, "stock_insufficient", "stock.insuficiente")
if any(k in target_lower for k in stock_ins_keys):
    msg_ins = StockInsufficientMessage.model_validate(parsed_data)
    return await handle_stock_insufficient(msg_ins, uow, notifier=notifier)
```
* Si Inventario publica con la routing key estándar `inventory.reservation.confirmed`, la cadena `"inventory.reservation.confirmed"` **no contiene ninguna subcadena de `stock_res_keys`**.
* El despachador lanza `ValueError: Unknown or unmapped event type: inventory.reservation.confirmed` y envía el mensaje a la cola de mensajes muertos (`DLQ`), dejando la orden congelada en espera de confirmación.
* **Solución Técnica:**
  Extender las tuplas de reconocimiento en `base.py` para incluir:
  * Para reserva exitosa: `"reservation.confirmed"`, `"reservation_confirmed"`, `"inventory.reservation.confirmed"`.
  * Para reserva rechazada: `"reservation.rejected"`, `"reservation_rejected"`, `"inventory.reservation.rejected"`.

---

### 2.5 Alerta Crítica 5: Proceso Consumidor RabbitMQ Activo en Background

Al inspeccionar [`orders-backend/app/workers/run_all.py`](file:///c:/Users/j_pab/Documents/dev/fmat-restaurant/orders-backend/app/workers/run_all.py#L85-L100):
* Actualmente `run_all.py` arranca:
  1. `OutboxWorker` (publica eventos salientes desde PostgreSQL a RabbitMQ).
  2. `SagaTimeoutWatcher` (monitorea órdenes estancadas en `CREATED`).
  3. `CatalogReconciliationWorker` (si `CATALOG_SYNC_URL` está configurado).
* **Falta registrar el Inbound RabbitMQ Consumer:** No hay una tarea asíncrona dentro de `run_all.py` que abra un canal `aio_pika`, cree la cola `orders.inventory.inbound`, la vincule al exchange `inventory.topic` y comience a despachar mensajes hacia `EventConsumerDispatcher`.
* Sin este worker consumidor escuchando activamente, `orders-backend` puede publicar eventos hacia Inventario, pero jamás procesará las respuestas de vuelta.

---

## 3. Arquitectura del Flujo de la Saga (Opción A Acordada)

### 3.1 Diagrama de Secuencia Completo

```mermaid
sequenceDiagram
    autonumber
    participant W as Mesero (OrderTicketWidget)
    participant O as Orders-Backend (API + Outbox)
    participant RMQ as RabbitMQ (ordenes.topic / inventory.topic)
    participant I as Inventory-Backend (Héctor)
    participant K as Cocinero (KDS Board)

    Note over W,I: FASE 1: Creación de la Comanda y Pre-reserva
    W->>O: POST /orders/{id}/confirm
    O->>RMQ: ordenes.order.created (Exchange: ordenes.topic)<br/>items: [{plateId: "BURGER", qty: 2}]
    RMQ->>I: Consume ordenes.order.created

    alt Stock Suficiente (Reserva Aprobada)
        Note over I: Reserva temporal de insumos<br/>Inicia TTL de seguridad (ej. 15 min)
        I->>RMQ: inventory.reservation.confirmed (Exchange: inventory.topic)
        RMQ->>O: Consume en Inbound Consumer
        O->>O: Transiciona orden a RESERVED (stock_reserved_at = now)
        O-->>W: WS: order.status_changed (RESERVED)
        O-->>K: WS: order.created (Aparece tarjeta en KDS)
        Note over I,K: FASE 2: En cola de KDS, la reserva es FIRME (TTL Pausado)
    else Stock Insuficiente (Reserva Rechazada)
        Note over I: Detecta falta de materias primas
        I->>RMQ: inventory.reservation.rejected (Exchange: inventory.topic)<br/>reason: "Stock insuficiente", items: [{itemId, requestedQuantity, availableStock}]
        RMQ->>O: Consume en Inbound Consumer
        O->>O: Transiciona orden a REJECTED (guarda detalle de faltantes)
        O-->>W: WS: order.status_changed (REJECTED + missing details)
        Note over W: Mesero ve aviso en comanda y ajusta cantidades
    end

    opt Chef inicia preparación en Cocina (FASE 3: Consumo Definitivo)
        K->>O: PATCH /orders/{id}/start-preparation
        O->>O: Transiciona orden a IN_PREPARATION
        O->>RMQ: ordenes.order.in_preparation (o ordenes.order.served)
        RMQ->>I: Consume ordenes.order.in_preparation
        Note over I: Descuento definitivo FEFO en almacén.<br/>Reserva cerrada exitosamente.
    end

    opt Cancelación de Comanda por Mesero o Expiración
        W->>O: POST /orders/{id}/cancel (o Saga Timeout)
        O->>O: Transiciona orden a CANCELLED / EXPIRED
        O->>RMQ: ordenes.order.cancelled
        RMQ->>I: Consume ordenes.order.cancelled
        Note over I: Libera materias primas pre-reservadas.
    end
```

### 3.2 Manejo del Tiempo de Reserva (TTL)
Para la negociación con Héctor, se ratifican las 3 fases operativas:
1. **Fase A (Pre-reserva con TTL activo):** Mientras el mesero confirma o si la orden fue rechazada y está en ajuste, el timer corre (5 a 10 minutos). Si no hay respuesta, la pre-reserva expira y libera stock.
2. **Fase B (Reserva Firme en cola de KDS - TTL Pausado):** Una vez que Inventario confirma y la orden pasa a `RESERVED`, entra a la cola de la cocina. En horas pico, un cocinero puede tardar 25 minutos en tomarla. **El TTL no debe matar la orden mientras esté en cola de espera**.
3. **Fase C (Consumo Definitivo):** Al presionar "Iniciar Preparación" (`IN_PREPARATION`), el evento saliente cierra definitivamente la reserva y ejecuta los asientos de consumo en inventario.

---

## 4. Multi-Rondas en Sesión de Mesa (`1 DiningSession : N Orders`)

### 4.1 El Problema Actual en PostgreSQL
En [`orders-backend/app/db/models.py`](file:///c:/Users/j_pab/Documents/dev/fmat-restaurant/orders-backend/app/db/models.py#L67), la columna `Order.dining_session_id` tiene restricción única (`unique=True` / `uq_orders_dining_session_id`).
* **Impacto Operativo:**
  1. Si una mesa pide una segunda ronda (ej. bebidas adicionales o postres), el sistema rechaza la creación de una nueva orden para la misma mesa con `500 UniqueConstraintViolation`.
  2. Si una orden previa se canceló o expiró, la mesa queda inhabilitada para ordenar de nuevo durante toda su estancia.
* **Solución de Base de Datos:**
  * Crear migración Alembic para remover el constraint `uq_orders_dining_session_id` y reemplazarlo por un índice estándar de búsqueda: `ix_orders_dining_session_id`.
  * Permitir recibir `diningSessionId` en el payload de `POST /orders` para asociar múltiples comandas a una misma sesión de sala.

---

## 5. Topología Docker y Red de Contenedores (`restaurant-network`)

Para que todos los microservicios se comuniquen sin colisiones de puertos ni problemas de resolución DNS, se establece la siguiente configuración unificada:

### 5.1 Red Externa Compartida
Todos los contenedores deben conectarse a una red tipo `bridge` compartida:
```bash
docker network create restaurant-network
```

### 5.2 Mapa de Puertos y Servicios

| Microservicio / Componente | Contenedor | Puerto Host | Puerto Interno | Red |
| :--- | :--- | :--- | :--- | :--- |
| **Kong API Gateway** | `kong-gateway` | `8000` (Proxy), `8001` (Admin) | `8000`, `8001` | `restaurant-network` |
| **RabbitMQ Broker** | `fmat-rabbitmq` | `5672` (AMQP), `15672` (Web UI) | `5672`, `15672` | `restaurant-network` |
| **Auth Backend** | `auth-backend` | `3001` | `3001` | `restaurant-network` |
| **Auth Frontend (Shell)** | `auth-frontend` | `3000` | `3000` | `restaurant-network` |
| **Menu Backend** | `menu-backend` | `8080` | `8080` | `restaurant-network` |
| **Menu Frontend** | `menu-frontend` | `5175` | `5175` | `restaurant-network` |
| **Inventory Backend** | `inventory-backend` | `8083` | `8083` | `restaurant-network` |
| **Inventory MongoDB** | `inventory-mongo` | `27017` | `27017` | `restaurant-network` |
| **Orders Backend** | `orders-backend` | `8082` | `8000` | `restaurant-network` |
| **Orders PostgreSQL** | `orders-db` | `5432` | `5432` | `restaurant-network` |
| **Orders Redis** | `orders-redis` | `6379` | `6379` | `restaurant-network` |
| **Orders Frontend** | `orders-frontend` | `5177` | `5177` | `restaurant-network` |

> [!NOTE]
> En entorno local standalone, `orders-backend` corre en el puerto `8000`. Al correr en Docker junto con Kong API Gateway (que expone el puerto `8000` al público), `orders-backend` se mapea externamente al puerto `8082:8000` para evitar conflicto.

---

## 6. Variables de Entorno Recomendadas para `orders-backend`

En el archivo `.env` o en `docker-compose.yaml` para las pruebas de esta noche:

```env
# Conexión a RabbitMQ compartido
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
RABBITMQ_EXCHANGE=ordenes.topic
RABBITMQ_VHOST=/

# Base de datos y Redis
DATABASE_URL=postgresql+asyncpg://orders_user:orders_pass@localhost:5432/orders_db
REDIS_URL=redis://localhost:6379/0

# Configuración de la Saga
SAGA_TIMEOUT_SECONDS=900
SAGA_WATCHER_INTERVAL_SECONDS=5.0
OUTBOX_POLL_INTERVAL_SECONDS=1.0
OUTBOX_BATCH_SIZE=100

# Sincronización de Catálogo con Menú
CATALOG_SYNC_URL=http://localhost:8080/api/v1/menu/catalog
CATALOG_RECONCILIATION_INTERVAL_SECONDS=300
```

---

## 7. Plan de Pruebas de Integración para la Reunión de Hoy

### Paso 1: Verificación de Topología en RabbitMQ Management
1. Abrir en navegador: `http://localhost:15672` (usuario: `guest`, contraseña: `guest`).
2. Ir a la pestaña **Exchanges** y verificar que existan con `Type: topic` y `Durability: Durable`:
   * `ordenes.topic`
   * `inventory.topic`
   * `menu.topic`
3. Ir a la pestaña **Queues** y comprobar la existencia de:
   * `cola_orders_events` (Consumidor de Inventario suscrito a `ordenes.order.*`).
   * `orders.inventory.inbound` (Consumidor de Órdenes suscrito a `inventory.reservation.*`).

### Paso 2: Prueba de Creación y Reserva Exitosa (Happy Path)
1. Iniciar los contenedores de RabbitMQ, Orders e Inventory.
2. Crear comanda en Orders: `POST /orders` con un platillo con stock suficiente en Inventory.
3. Confirmar la comanda: `POST /orders/{orderId}/confirm`.
4. **Verificación en RabbitMQ:** Comprobar que en `ordenes.topic` sale `ordenes.order.created` con `plateId`, `quantity` y `orderId`.
5. **Verificación en Inventario:** Comprobar que procesa la reserva y publica `inventory.reservation.confirmed` en `inventory.topic`.
6. **Verificación en Orders:** Comprobar que el estado de la orden transiciona a `RESERVED` y emite evento WebSocket al mesero y al KDS.

### Paso 3: Prueba de Stock Insuficiente (Unhappy Path)
1. Crear comanda con una cantidad excesiva de un platillo (ej. 99 porciones).
2. Confirmar la comanda: `POST /orders/{orderId}/confirm`.
3. **Verificación en Inventario:** Comprobar que rechaza la reserva y emite `inventory.reservation.rejected` con el detalle de items.
4. **Verificación en Orders:** Comprobar que la orden pasa a `REJECTED`, guardando el motivo de fallo y sin crashear el despachador de eventos.

### Paso 4: Prueba de Inicio de Preparación (Consumo FEFO)
1. Desde la vista de cocina, marcar la orden como iniciada: `PATCH /orders/{orderId}/start-preparation`.
2. Verificar que Orders emite `ordenes.order.in_preparation` a RabbitMQ.
3. Verificar que Inventario procesa el consumo definitivo y publica `inventory.reservation.consumed`.
