# Orders & KDS — Puntos Abiertos Pendientes de Resolución (Backlog de Decisiones)

> **Origen:** `docs/adr/Decisiones_Puntos_Abiertos_OP01-OP09.md`, `docs/adr/Decisiones_Puntos_Abiertos_Sprint4_Reunion_Inventario.md` y `Planificacion_Sprints_Orders_KDSV3.md`  
> **Fecha de corte:** 31 de octubre de 2026 (Cierre de Backend Sprint 4 / Inicio de Sprint 5)  
> **Aplica a:** `ordenes-kds-backend` y `ordenes-kds-frontend`  
> **Ubicación:** `docs/adr/Puntos_Abiertos_Pendientes.md`  
> **Propósito:** Registrar y dar seguimiento exclusivo a los puntos abiertos (OP) y acuerdos inter-equipo que permanecen **pendientes** o **en curso** para los Sprints 5 y 6, separándolos de los puntos que ya fueron cerrados e implementados en el Sprint 4 (documentados en `docs/adr/Decisiones_Puntos_Abiertos_Sprint4_Reunion_Inventario.md`).

---

## 1. Matriz de Seguimiento de Puntos Abiertos

### 1.1 Puntos Abiertos Pendientes / En Curso (Para Resolver en Sprint 5 y Sprint 6)

| ID | Tema | Estado Actual | Dueño / Contraparte | Hito Objetivo de Cierre | Impacto / Tareas Afectadas |
|---|---|:---:|---|:---:|---|
| **OP-12** | Rehacer platillo (`redo`) con orden en `DELIVERED` y descuento extra de insumos en Inventario | **Pendiente de cerrar contrato con Inventario** *(Diseño listo para S5)* | Backend + Frontend Orders + Inventario (Héctor) | Sprint 5 (`TASK-32`, `TASK-23`) | `TASK-32`, `TASK-23`, `INT-02` |
| **Propuesta Menú → Inventario** | Suscripción paralela (fan-out Pub/Sub) de Inventario y OrdersKDS a los eventos de disponibilidad de Menú | **Pendiente de formalizar en Broker** | Inventario (Héctor) + Menú + Backend Orders | Sprint 5 (`TASK-21b`, `TASK-38`) | `TASK-21b`, `INT-01`, `INT-04` |
| **OP-10** | Topología compartida de RabbitMQ, vhost, colas, DLQ y credenciales de staging | **En curso** | Infraestructura / Broker + Backend Orders | Sprint 5 (`TASK-38`) | `TASK-05`, `TASK-07`, `TASK-38`, `INT-01..12` |
| **OP-13** | Canal/MFE donde el mesero recibe el aviso visual de orden `DELIVERED` (Mapa de Sala vs Widget propio) | **Pendiente** | Frontend Orders + Sala | Sprint 6 (`INT-03`) | `TASK-16`, `TASK-42` (condicional) |

### 1.2 Puntos Abiertos Cerrados Recientemente en la Reunión de Sprint 4

> El detalle técnico completo, contratos JSON y ADRs (`ADR-0012` a `ADR-0018`) de estos puntos se encuentra en [`docs/adr/Decisiones_Puntos_Abiertos_Sprint4_Reunion_Inventario.md`](Decisiones_Puntos_Abiertos_Sprint4_Reunion_Inventario.md).

| ID | Tema | Estado | Resolución Implementada en Sprint 4 |
|---|---|:---:|---|
| **OP-04** | Contrato de `ordenes.orden.expirada` y campo `motivo`/`items` | **Cerrado (Sprint 4)** | Incluye `elapsedSeconds`, `reason: "timeout"`, `motivo: "timeout"` e `items: [{ offerId, quantity }]` consolidados (`ADR-0012`). |
| **OP-11** | Prioridad de cola KDS por antigüedad en cocina e inclusión de `RESERVED` | **Cerrado (Sprint 4)** | Cola activa incluye `RESERVED` e `IN_PREPARATION`, ordenada por `COALESCE(in_preparation_at, stock_reserved_at, created_at) ASC` (`ADR-0013`). |
| **ADR-0013..0018** | Estado `RESERVED`, `start-preparation` (`ordenes.orden.en_preparacion`), `kitchen-reject` (`ordenes.platillo.rechazado_cocina`), Opción A de stock parcial (`rejectedItems`/`reservedItems`), reapertura `REJECTED -> CREATED`, y relación `1:N` en `DiningSession` | **Cerrado (Sprint 4)** | Implementado al 100 % en `orders-backend` con migración Alembic, contratos JSON Schema y 599 pruebas automatizadas en verde. |

---

## 2. Detalle de los Puntos Abiertos Pendientes y Acciones Requeridas

### 2.1 OP-12: Rehacer platillo (`redo`) con orden matriz en `DELIVERED` y consumo extra en Inventario

- **Estado:** Pendiente de cerrar acuerdo de evento con Inventario (Diseño técnico listo para implementar en Sprint 5 · `TASK-32` y `TASK-23`).
- **Dueño:** Backend + Frontend Orders & KDS + Microservicio de Inventario (Héctor).
- **Hito de resolución:** Sprint 5 (`TASK-32`: `PATCH /orders/{orderId}/items/{itemId}/redo` y `TASK-23`: Diálogo de rehechura).
- **Contexto y problemática:**
  1. Cuando un comensal devuelve un platillo ya entregado por queja de calidad o término de cocción (RF-22), la orden matriz ya se encuentra en `OrderStatus.DELIVERED`.
  2. En el dominio de Órdenes ya se acordó que **la orden matriz conserva el estado `DELIVERED`** y únicamente el renglón afectado regresa de `ItemStatus.DELIVERED` a `ItemStatus.PENDING` con nota obligatoria (`{ "note": "..." }`), haciendo que la tarjeta reaparezca en la vista activa del KDS con ese platillo resaltado.
  3. **Punto a cerrar con Inventario (Héctor):** Como el platillo original ya había consumido sus insumos al entrar la orden a `IN_PREPARATION` (`ordenes.orden.en_preparacion`), elaborar de nuevo ese platillo implica un **consumo físico adicional de insumos (merma por rehechura)** sin incrementar el cobro al cliente.
- **Opciones técnicas para acordar con Héctor:**
  - **Alternativa A (Recomendada):** Cuando cocina termina de rehacer el platillo y lo marca de nuevo como `READY` (`PATCH /orders/{orderId}/items/{itemId}/ready`), `orders-backend` emite `ordenes.platillo.preparado` incluyendo `"quantity": item.quantity` y el flag **`"isRedo": true`**. Al ver `"isRedo": true`, Inventario descuenta las unidades adicionales de ese `offerId`.
  - **Alternativa B:** Emitir un evento dedicado `ordenes.platillo.rehecho` (`{ eventId, orderId, itemId, offerId, quantity, reason }`) en el instante en que el mesero ejecuta `PATCH /orders/{orderId}/items/{itemId}/redo`.
- **Acciones pendientes:**
  - [ ] Confirmar con Héctor cuál de las dos alternativas prefiere consumir en Inventario para descontar el insumo del platillo rehecho.
  - [ ] Implementar en `OrderService.mark_item_ready` (Sprint 5 · `TASK-32`) el soporte para `order.status == OrderStatus.DELIVERED` de modo que el ítem rehecho pase de `PENDING` a `DELIVERED` conservando la orden en `DELIVERED`.

---

### 2.2 Propuesta de Héctor: Validación inicial de disponibilidad de Menú hacia Inventario vs. Proyección en OrdersKDS

- **Estado:** Pendiente de formalizar acuerdo de topología Pub/Sub con Inventario y Menú.
- **Dueño:** Inventario (Héctor) + Menú + Backend Orders & KDS.
- **Hito de resolución:** Sprint 5 (`TASK-21b` / `TASK-38`).
- **Contexto de la propuesta:**
  - Héctor propuso que la notificación inicial de disponibilidad que publica `Menú` llegue directamente a `Inventario` en lugar de llegar a `OrdersKDS`.
- **Análisis arquitectónico y posición técnica de `OrdersKDS`:**
  1. **Por qué `Inventario` SÍ se beneficia de escuchar a `Menú`:** Es excelente idea que `Inventario` se suscriba a los eventos de `Menú` (`menu.catalogo.actualizado`, `menu.oferta.creada`, `menu.oferta.actualizada`) para conocer las recetas/ofertas activas y mantener sincronizado su caché de stock en Redis.
  2. **Por qué `OrdersKDS` NO debe dejar de recibir los eventos de `Menú`:**
     - En RabbitMQ los eventos se publican a un **Exchange tipo Topic/Fanout (Pub/Sub)**: que `Inventario` enlace su propia cola para recibir los eventos de `Menú` **no impide ni compite** con que `OrdersKDS` también los reciba en su propia cola.
     - `OrdersKDS` **necesita obligatoriamente** su proyección local en Redis (`CatalogRepository`) por tres razones críticas de negocio y rendimiento:
       1. **Metadatos exclusivos de Cocina (`RF-08`, `OP-09`):** La pantalla del KDS requiere mostrar y filtrar cada platillo por su estación de cocina (`station` / `estacion`: parrilla, barra, fría) y mostrar el tiempo estimado de preparación (`preparationTime` / `tiempoPreparacion`), datos que pertenecen al catálogo de Menú y no a Inventario.
       2. **Precios comerciales congelados (`RF-14`, `RF-15`):** Al agregar un ítem en borrador (`POST /orders` o `PATCH /orders/{id}/items`), `orders-backend` congela el nombre comercial y el `unit_price` desde su Redis local en `< 1 ms` sin hacer llamadas HTTP a Menú ni a Inventario.
       3. **Filtro inmediato de primera línea (`RF-17`, `< 1 ms`):** Si un platillo está deshabilitado comercialmente en Menú o fue rechazado físicamente por el Chef en cocina (`kitchen-reject`), `OrdersKDS` rechaza la adición al borrador en `< 1 ms` con `422 DISH_UNAVAILABLE` sin siquiera generar tráfico en RabbitMQ.
- **Resolución propuesta para acordar con Héctor:**
  - [ ] Confirmar con Héctor y el equipo del Broker que **tanto `Inventario` como `OrdersKDS` tendrán sus colas suscritas en paralelo al exchange de `Menú`** (patrón Pub/Sub estándar), logrando el objetivo de Héctor sin romper el KDS ni la congelación de precios en Órdenes.

---

### 2.3 OP-10: Topología de RabbitMQ y variables del broker compartido

- **Estado:** En curso.
- **Dueño:** Infraestructura / Dueño del Broker + Backend Orders & KDS.
- **Hito de resolución:** Sprint 5 (`TASK-38` — Ambiente compartido de integración y staging).
- **Acciones pendientes:**
  - [ ] Homologar con el administrador del broker el nombre del *exchange* compartido, el *vhost*, las colas de entrada y la configuración de Dead Letter Exchange (`orders.dlq`), incluyendo los nuevos routing keys de Sprint 4 (`ordenes.orden.en_preparacion` y `ordenes.platillo.rechazado_cocina`).
  - [ ] Configurar `RABBITMQ_URL` exclusivamente mediante *GitHub Environments / Secrets* (prohibido texto plano en el repositorio).

---

### 2.4 OP-13: Recepción del aviso de orden `DELIVERED` para el mesero

- **Estado:** Pendiente.
- **Dueño:** Frontend Orders & KDS + Microservicio de Sala.
- **Hito de resolución:** Sprint 6 (`INT-03`).
- **Acciones pendientes:**
  - [ ] Definir con el equipo de Sala si su mapa de mesas consumirá el canal WebSocket `/ws/waiter` (`floor` / `user:{userId}`) para pintar la alerta de orden lista para recoger (**Opción A, recomendada**) o si el frontend de Órdenes exportará el componente federado `./ReadyOrdersAlert` (**Opción B**, activando `TASK-42`).
