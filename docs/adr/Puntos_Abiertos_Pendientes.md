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
| **OP-04** | Contrato de `ordenes.orden.expirada` y campo `motivo` | **Pendiente** | Backend + Inventario | Sprint 6 (INT-01) | TASK-06, TASK-18/19, TASK-34 |
| **OP-13** | Canal/MFE de aviso de orden `DELIVERED` al mesero | **Pendiente** | Frontend + Sala | Sprint 6 (INT-03) | TASK-16, TASK-42 (condicional) |
| **OP-11** | Prioridad de cola KDS por tiempo en cocina (`in_preparation_at`) | **Propuesto** *(Por validar)* | Backend + Frontend + PO | Sprint 3 / Sprint 4 | TASK-12, TASK-15 |
| **OP-12** | Rehacer platillo con orden matriz en `DELIVERED` | **Propuesto** *(Por validar)* | Backend + Frontend + PO | Sprint 5 | TASK-23, TASK-32 |

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

- **Estado:** Pendiente.
- **Dueño:** Backend Orders & KDS + Microservicio de Inventario.
- **Hito de resolución:** Integración INT-01 (Sprint 6).
- **Contexto y problemática:**
  1. Cuando expira el tiempo límite de confirmación de stock (`SAGA_TIMEOUT_SECONDS=60`, ADR-0001), el worker `SagaTimeoutWatcher` transita la orden a `EXPIRED` y publica el evento `ordenes.orden.expirada`.
  2. Falta acordar con el equipo de Inventario el JSON Schema definitivo y si el evento debe incluir el campo `motivo`:
     - `"timeout"`: la reserva tardía debe liberarse si llega posterior al vencimiento.
     - `"rejected"`: rechazo por falta de stock.
- **Acciones pendientes:**
  - [ ] Validar con el equipo de Inventario el esquema ubicado en `contracts/events/ordenes.orden.expirada.json`.
  - [ ] Confirmar que Inventario consume `ordenes.orden.expirada` y `ordenes.orden.cancelada` para liberar bloqueos de stock tardíos (cierre formal en INT-01).

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

- **Estado:** Propuesto (Implementado técnicamente, pendiente de ratificación final del equipo/PO).
- **Dueño:** Backend + Frontend Orders & KDS.
- **Hito de resolución:** Cierre funcional de TASK-12 y TASK-15 (Sprint 4).
- **Contexto:**
  1. Debido a que el catálogo de Menú entrega el tiempo de receta como texto no procesable numéricamente (ADR-0008), la prioridad de la cola KDS se formuló como FIFO basado en el tiempo transcurrido en cocina (`in_preparation_at`).
  2. Se configuraron los umbrales de semáforo:
     - `KDS_WARNING_AFTER_SECONDS` (10 min por defecto) → Alerta amarilla.
     - `KDS_LATE_AFTER_SECONDS` (15 min por defecto) → Alerta roja.
- **Acciones pendientes:**
  - [ ] Ratificar formalmente en Sprint Review la aceptación de esta prioridad ante el cliente/profesor.

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

