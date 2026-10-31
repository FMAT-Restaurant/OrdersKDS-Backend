# Orders & KDS — Puntos Abiertos Pendientes de Resolución (Backlog de Decisiones)

> **Origen:** `docs/adr/Decisiones_Puntos_Abiertos_OP01-OP09.md` y `Planificacion_Sprints_Orders_KDSV3.md` §1.4 / §H  
> **Fecha de corte:** 29 de octubre de 2026 (Sprint 4 / Sprint 5)  
> **Aplica a:** `ordenes-kds-backend` y `ordenes-kds-frontend`  
> **Ubicación:** `docs/adr/Puntos_Abiertos_Pendientes.md`  
> **Propósito:** Registrar y dar seguimiento exclusivo a los puntos abiertos (OP) que permanecen **pendientes**, **en curso** o **propuestos (por validar)**, facilitando su resolución en las sesiones de trabajo e integración de los sprints posteriores.

---

## 1. Matriz de Seguimiento de Puntos Abiertos No Resueltos

| ID | Tema | Estado Actual | Dueño / Contraparte | Hito Objetivo de Cierre | Impacto / Tareas Afectadas |
|---|---|:---:|---|:---:|---|
| **OP-10** | Topología de RabbitMQ, vhost, colas y credenciales del broker | **En curso** *(Retrasado de S2)* | Infra / Broker + Backend | Sprint 5 (TASK-38) / Prevención S6 | TASK-07, TASK-05, TASK-38, INT-01..12 |
| **OP-04** | Contrato de `ordenes.orden.expirada` y campo `motivo`/`items` | **Cerrado** *(Sprint 4)* | Backend + Inventario | Sprint 4 (TASK-06, TASK-34) | TASK-06, TASK-34 |
| **OP-13** | Canal/MFE de aviso de orden `DELIVERED` al mesero | **Pendiente** | Frontend + Sala | Sprint 6 (INT-03) | TASK-16, TASK-42 (condicional) |
| **OP-11** | Prioridad de cola KDS por antigüedad en cocina y `RESERVED` | **Cerrado** *(Sprint 4)* | Backend + Frontend + PO | Sprint 4 (TASK-12, TASK-15) | TASK-12, TASK-15 |
| **OP-12** | Rehacer platillo con orden matriz en `DELIVERED` | **Diferido** *(Diseño listo S5)* | Backend + Frontend + Inventario | Sprint 5 (TASK-23, TASK-32) | TASK-23, TASK-32 |

---

## 2. Detalle de Puntos Abiertos y Acciones Requeridas

### 2.1 OP-10: Topología de RabbitMQ y variables del broker

- **Estado:** En curso (Retrasado — no se pudo concretar la reunión con el dueño del broker durante el Sprint 2).
- **Dueño:** Infraestructura / Dueño del Broker + Backend Orders & KDS.
- **Hito de resolución:** Sprint 5 (previo al despliegue del entorno compartido de integración TASK-38) o antes de iniciar S6.
- **Contexto y problemática:**
  1. Durante el Sprint 2 se esperaba revisar el archivo comprimido `.zip` compartido por el equipo responsable del broker RabbitMQ central para alinear nombres de *exchanges*, *vhost*, convenciones de *routing keys*, políticas de Dead Letter Queue (DLQ / DLX) y TTL de reintentos.
  2. Al no haberse celebrado aún la reunión técnica de sincronización, el backend opera actualmente con topología local simulada (`orders.topic` / `amq.topic`) y variables de entorno estándar.
  3. Es crítico evitar llegar a Integración I (Sprint 6) sin tener pactado el acceso al broker compartido.
- **Acciones pendientes:**
  - [ ] Agendar y llevar a cabo la reunión técnica con el dueño del microservicio/infraestructura del broker RabbitMQ.
  - [ ] Inspeccionar el `.zip` oficial de configuración de RabbitMQ y validar:
    - Nombre del *exchange* unificado (¿`amq.topic` o exchange nombrado propio de la facultad?).
    - Nombre del *vhost* asignado al microservicio de Órdenes.
    - Nombres normalizados de colas de entrada y colas de descarte/error (`orders.dlq`).
  - [ ] Obtener las credenciales formales para el ambiente compartido de staging (TASK-38).
- **Regla estricta de seguridad:**
  - **Queda terminantemente prohibido almacenar credenciales o URLs de CloudAMQP/RabbitMQ con usuario y contraseña en texto plano dentro de la documentación o código del repositorio.**
  - La conexión debe inyectarse exclusivamente a través del secreto `RABBITMQ_URL` en las configuraciones seguras de ejecución (*GitHub Secrets* / *Deploy Environments*).

---

### 2.2 OP-04: Contrato de `ordenes.orden.expirada` y campo `motivo` con Inventario

- **Estado:** **Cerrado en Sprint 4** (Implementado y validado mediante contrato JSON Schema y pruebas automáticas).
- **Dueño:** Backend Orders & KDS + Microservicio de Inventario.
- **Hito de resolución:** Sprint 4 (TASK-06, TASK-34).
- **Resolución adoptada:**
  1. Se actualizó el esquema formal `contracts/events/ordenes.orden.expirada.v1.json` agregando los campos:
     - `reason: "timeout"` y `motivo: "timeout"` (compatibilidad bilingüe EN/ES con Inventario).
     - `items: [{ "offerId": string, "quantity": integer }]` con la lista consolidada de platillos y porciones activas de la orden vencida.
  2. En `app/messaging/events.py` se actualizó la clase `OrderExpiredEvent` con `for_order(...)` recibiendo los ítems consolidados y la causa del vencimiento.
  3. En `app/workers/saga_timeout_watcher.py`, al detectar una comanda vencida tras `SAGA_TIMEOUT_SECONDS`, se consolidan sus ítems (`_consolidate_order_items(order.items)`), se construye el evento enriquecido, se valida contra el esquema JSON y se encola en el Outbox transaccional para que Inventario libere cualquier reserva tardía.

---

### 2.3 OP-13: Recepción del aviso de orden `DELIVERED` para el mesero

- **Estado:** Pendiente.
- **Dueño:** Frontend Orders & KDS + Microservicio de Sala.
- **Hito de resolución:** Integración INT-03 (Sprint 6).
- **Contexto y problemática:**
  1. Conforme al ADR-0007, la cocina transita la orden a `DELIVERED` al terminar los platillos, y el backend notifica vía WebSocket en `/ws/waiter` (canales `floor` y `user:{userId}`).
  2. Falta acordar dónde observa el mesero dicha notificación:
     - **Opción A:** El microservicio de Sala consume el evento o WebSocket y muestra la comanda lista en su propio mapa de mesas.
     - **Opción B:** El microservicio de Orders debe proporcionar un componente/widget flotante de aviso propio para el mesero.
  3. Si se elige la Opción B, se activará en el backlog la tarea condicional **TASK-42** (*Aviso al mesero de órdenes DELIVERED*, 3 pts).
- **Acciones pendientes:**
  - [ ] Confirmar con el equipo de Sala en la sesión de INT-03 si ellos asumen la visualización de la alerta de entrega en su interfaz de salón.
  - [ ] Decidir la activación o descarte formal de TASK-42.

---

### 2.4 OP-11: Prioridad en cola KDS por tiempo en cocina (`in_preparation_at`)

- **Estado:** **Cerrado en Sprint 4** (Implementado en base de datos y memoria, integrado con estado `RESERVED`).
- **Dueño:** Backend + Frontend Orders & KDS.
- **Hito de resolución:** Sprint 4 (TASK-12, TASK-15).
- **Resolución adoptada:**
  1. La consulta de cola KDS (`build_kds_queue_query` y `get_kds_queue`) incorpora las comandas en estado `RESERVED` junto con `IN_PREPARATION` y `DELIVERED` con ítems pendientes.
  2. El ordenamiento FIFO por antigüedad en cocina se consolidó mediante:
     `COALESCE(in_preparation_at, stock_reserved_at, created_at) ASC`, desempatando por `created_at ASC` y `public_id ASC`.
  3. Esto asegura que una comanda en `RESERVED` mantenga su turno justo en la cola de cocina desde el instante en que Inventario confirmó sus insumos, incluso antes de que el chef pulse *Iniciar preparación*.

---

### 2.5 OP-12: Rehacer platillo con orden matriz en `DELIVERED`

- **Estado:** Propuesto (Pendiente de validación al construir el flujo alternativo en Sprint 5).
- **Dueño:** Backend + Frontend Orders & KDS.
- **Hito de resolución:** Sprint 5 (TASK-32 `PATCH …/items/{itemId}/redo` y TASK-23).
- **Contexto:**
  1. Cuando un platillo debe rehacerse por queja del cliente (RF-22), la orden matriz ya está en `DELIVERED`.
  2. Para evitar ciclos complejos o regresiones en la máquina de estados de la orden matriz, se propuso que la orden conserve `DELIVERED` y únicamente el ítem regrese a `PENDING` con su motivo obligatorio.
- **Acciones pendientes:**
  - [ ] Validar con la UI del chef y mesero que el platillo rehecho aparezca correctamente resaltado en el KDS sin alterar la cuenta global.



---

## 3. Decisiones Arquitectónicas Formalizadas en Sprint 4

### ADR-0012: Incorporación del Estado `RESERVED`, Consumo Definitivo en Cocina, Rechazo Físico del Chef y Reapertura Editable desde `REJECTED`

* **Contexto:** En el análisis del flujo con Inventario y Cocina, se detectó la necesidad de desacoplar la reserva de stock del inicio físico de la preparación, permitir el rechazo por ítem si faltan insumos físicos en cocina, y permitir al mesero reabrir y modificar la misma orden si Inventario devuelve stock insuficiente (Opción A).
* **Decisiones adoptadas:**
  1. **Nuevo Estado `OrderStatus.RESERVED`:**
     - Al recibir `inventario.stock.reservado`, la orden pasa a `RESERVED` con marca `stock_reserved_at`. No entra automáticamente a `IN_PREPARATION`.
  2. **Inicio Manual de Preparación (`PATCH /orders/{orderId}/start-preparation`):**
     - El Chef pasa la comanda de `RESERVED` a `IN_PREPARATION`. Se emite `ordenes.orden.en_preparacion` al Outbox. Al consumirlo, Inventario descuenta definitivamente los insumos y apaga el TTL de reserva.
  3. **Rechazo Manual por Ítem del Chef (`PATCH /orders/{orderId}/items/{itemId}/kitchen-reject`):**
     - Si falta el insumo físico en cocina, el Chef rechaza ese ítem en `RESERVED`. Se bloquea inmediatamente en el Redis local (`CatalogRepository.set_availability(dish_id, False)`), se emite `ordenes.platillo.rechazado_cocina` a Inventario y Menú, y la orden pasa a `REJECTED` devolviéndose al mesero con `rejectionDetails`.
  4. **Reapertura Editable desde `REJECTED` (`PATCH /orders/{orderId}/items`):**
     - Cuando el mesero edita una orden en `REJECTED`, la comanda reabre a `CREATED` (`order.reopen_as_draft()`), permitiendo ajustar cantidades o platillos y re-confirmar con el mismo `orderId` y un nuevo `eventId` determinista (`attempt_key=order.version`).
  5. **Cancelación Permitida en `CREATED`, `RESERVED` y `REJECTED`:**
     - Bloqueada únicamente en `IN_PREPARATION` (409 Conflict). Al cancelar emite `ordenes.orden.cancelada` con los ítems consolidados para liberar reservas.
