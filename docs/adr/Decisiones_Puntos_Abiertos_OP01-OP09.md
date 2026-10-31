# Orders & KDS — Registro Consolidado de Decisiones de Arquitectura (ADR) y Puntos Abiertos

> **Origen:** TASK-37 (S1), TASK-C37 (S2) y Refactorización de Sprint 4 · **Fecha de actualización:** 31 de octubre de 2026  
> **Aplica a:** `ordenes-kds-backend` y `ordenes-kds-frontend`  
> **Ubicación:** `docs/adr/Decisiones_Puntos_Abiertos_OP01-OP09.md`  
> **Complementado por:** `docs/adr/Decisiones_Puntos_Abiertos_Sprint4_Reunion_Inventario.md` (ADRs 0012–0018 cerrados en la reunión con Inventario y Menú) y `docs/adr/Puntos_Abiertos_Pendientes.md`  
> **Estándar:** Formato Architecture Decision Record (ADR) según `CONTRIBUTING.md` §10  
>
> **Convención de este documento:**
> - Las decisiones aprobadas e implementadas se registran con estado **Aceptado**.
> - Los acuerdos pendientes de validación técnica de campo o de otros equipos se marcan como **Propuesto / Pendiente**.
> - Los identificadores de código, rutas y campos JSON van en inglés (`DEVELOPMENT_GUIDELINES.md` §2); los nombres de eventos del broker se mantienen según los contratos pactados con otros microservicios.

---

## 1. Matriz de Estado de Puntos Abiertos (TASK-37 / TASK-C37 / Sprint 4)

| Elemento | Estado original (S1) | Estado consolidado actual (S4) | ADR / Referencia | Dueño | Fecha / Hito |
|---|---|---|---|---|---|
| **OP-01** `SAGA_TIMEOUT_SECONDS` | `[ ]` sin marcar | `[x]` **Aceptado**: 60 s como máximo desde `confirmed_at` | ADR-0001 | Backend | S1 / Cerrado |
| **OP-02** Canales WebSocket | `[ ]` sin marcar | `[x]` **Aceptado**: dos endpoints (`/ws/kds`, `/ws/waiter`) y canales por estación, piso y usuario | ADR-0002 | Backend + Frontend | S2–S4 / Cerrado |
| **OP-03** Colores semánticos | `[ ]` sin marcar | `[x]` **Aceptado**: paleta estándar en guía visual; tokens `--color-success/warning/error/info` | ADR-0003 | Frontend | S1 / Cerrado |
| **OP-04** Contrato `orden.expirada` y `motivo` | No existía | `[x]` **Aceptado**: incluye `elapsedSeconds`, `reason: "timeout"`, `motivo: "timeout"` e `items` consolidados | ADR-0012 (`Decisiones_Puntos_Abiertos_Sprint4_Reunion_Inventario.md`) | Backend + Inventario | S4 / Cerrado |
| **OP-05** `confirm` repetido y reapertura | `[ ]` sin marcar | `[x]` **Aceptado**: `confirm` idempotente con `confirmed_at`, reapertura editable `REJECTED -> CREATED` y `attempt_key=order.version` | ADR-0004 y ADR-0016 | Backend | S1–S4 / Cerrado |
| **OP-06** Interacción KDS / Bump Bar | `[ ]` sin marcar | `[x]` **Aceptado**: KDS táctil como entrada principal, teclado/Bump Bar secundario con paridad total | ADR-0005 | Frontend | S1 / Cerrado |
| **OP-07** Rutas en inglés | `[ ]` sin marcar | `[x]` **Aceptado**: rutas, parámetros y campos REST en inglés; eventos broker sin cambio | ADR-0006 | Backend + Frontend | S1 / Cerrado |
| **OP-08** Quién marca "Entregado" | `[ ]` sin marcar | `[x]` **Aceptado**: cocina lo marca, sin endpoint `deliver`, mesero recoge sin registro en sistema | ADR-0007 | Backend + Frontend | S1 / Cerrado |
| **OP-09** Tiempo y estación catálogo | `[ ]` sin marcar | `[x]` **Aceptado**: ambos campos son texto (`string`) de referencia | ADR-0008 | Backend + Menú | S1 / Cerrado |
| **OP-10** Topología y credenciales broker | `[x]` con URL expuesta | `[ ]` **En curso**: homologar exchange/colas en staging; credenciales exclusivas vía secretos (`RABBITMQ_URL`) | OP-10 (§3.2) | Infra / Broker + Backend | S5 (`TASK-38`) |
| **OP-11** Prioridad en cola KDS y `RESERVED` | No existía | `[x]` **Aceptado**: incluye `RESERVED` e `IN_PREPARATION`, ordena por `COALESCE(in_preparation_at, stock_reserved_at, created_at) ASC` | ADR-0009 y ADR-0013 | Backend + Frontend | S4 / Cerrado |
| **OP-12** Rehacer ítem con orden `DELIVERED` | No existía | `[ ]` **Pendiente S5**: orden conserva `DELIVERED`, ítem pasa a `PENDING` (falta cerrar evento extra con Inventario) | ADR-0010 (`Puntos_Abiertos_Pendientes.md`) | Backend + Frontend + Inventario | S5 (`TASK-32`) |
| **OP-13** Aviso `DELIVERED` al mesero | No existía | `[ ]` **Pendiente**: monitor de Sala vs. componente propio en frontend | OP-13 (§3.3) | Frontend + Sala | INT-03 (S6) |
| **TASK-27** Platillos intermedios (chef) | `[ ]` sin marcar | `[x]` **Aceptado (NO-GO)**: excluido del horizonte de 7 sprints (`INTERMEDIATE_DISHES_ENABLED=false`) | ADR-0011 | Arquitectura | S1 / Cerrado |

---

## 2. Registro de Decisiones de Arquitectura (ADR-0001 a ADR-0011)

> Para las decisiones **ADR-0012 a ADR-0018** acordadas en la reunión de alineación con Inventario y Menú durante el Sprint 4 (estado `RESERVED`, consumo definitivo `ordenes.orden.en_preparacion`, rechazo físico en cocina `ordenes.platillo.rechazado_cocina`, Opción A de stock insuficiente parcial, reapertura editable `REJECTED -> CREATED` y relación `1:N` en `DiningSession`), consúltese [`docs/adr/Decisiones_Puntos_Abiertos_Sprint4_Reunion_Inventario.md`](Decisiones_Puntos_Abiertos_Sprint4_Reunion_Inventario.md).

### ADR-0001: Valor y gobernanza de `SAGA_TIMEOUT_SECONDS` (OP-01)

- **Estado:** Aceptado
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-35, RNF-01, RNF-10

#### Contexto
Si el microservicio de Inventario no responde al evento `ordenes.orden.creada`, la comanda queda bloqueada indefinidamente en `CREATED`. El documento ERS define el estado `EXPIRED` para resolver este escenario, pero omitió fijar el tiempo límite (ERS §10, punto 7).

#### Decisión
Se fija un tiempo límite estricto de **60 segundos como máximo**:
1. `SAGA_TIMEOUT_SECONDS=60` como valor obligatorio acordado.
2. La regla de expiración se evalúa contando **≥ 60 segundos transcurridos desde `confirmed_at`**. El temporizador inicia en el momento de la confirmación por el mesero y no en la creación del borrador.
3. Se define una frecuencia para el Saga Timeout Watcher de `SAGA_WATCHER_INTERVAL_SECONDS=5`.
4. **Sin valor por defecto en código:** el microservicio aborta su arranque si la variable de entorno no está configurada, obligando a su definición explícita en cada despliegue.

#### Consecuencias
- Inventario consume `ordenes.orden.expirada` (enriquecido en Sprint 4 con `reason: "timeout"`, `motivo: "timeout"` e `items` según ADR-0012) para liberar stock reservado tardíamente.
- Las pruebas unitarias e integración (UT-BE-50, IT-07) validan límites en 59, 60 y 61 segundos mediante inyección de reloj.
- A nivel de usuario, la interfaz muestra "pendiente de confirmación de stock"; si expira, la orden queda en estado terminal `EXPIRED`.

---

### ADR-0002: Alcance y autenticación de canales WebSocket (OP-02)

- **Estado:** Aceptado (Implementado en Sprint 4)
- **Fecha:** 2026-10-05 (Actualizado: 2026-10-31)
- **Requisitos relacionados:** RNF-04, RNF-06, RNF-12

#### Contexto
La Arquitectura §3.3 estipula una conexión bidireccional persistente por sesión autenticada con JWT, pero no detallaba la taxonomía de canales ni las restricciones por rol, lo que causaba incertidumbre sobre el tráfico que recibe cada cliente.

#### Decisión
Se establecen **dos endpoints** WebSocket segregados por rol y cuatro canales normalizados:

| Canal | Endpoint | Roles autorizados | Modo de suscripción | Tráfico recibido |
|---|---|---|---|---|
| `kds:station:{station}` | `/ws/kds` | `kitchen`, `chef`, `admin` | Solicitado por el cliente (cambio sin reconectar) | Cambios de estado de órdenes con ítems de la estación e ítems asociados |
| `kds:all` | `/ws/kds` | `kitchen`, `chef`, `admin` | Solicitado por el cliente | Todas las órdenes activas en cocina sin filtro |
| `floor` | `/ws/waiter` | `waiter`, `admin` | Automático al conectar | Cambios de estado de todas las órdenes activas del restaurante |
| `user:{userId}` | `/ws/waiter` | `waiter`, `admin` | Automático desde el claim del JWT | Alertas de órdenes bajo su responsabilidad (`RESERVED`, `REJECTED` con `rejectionDetails`, `EXPIRED`, `DELIVERED`) |

Reglas clave:
- El handshake rechaza tokens ausentes o inválidos con código `4401`, y roles no permitidos con código `4403`.
- Intentar suscribirse a un canal ajeno al rol responde con `{ "error": "FORBIDDEN_CHANNEL" }`.
- Las cargas útiles son señales tipadas en camelCase: `{ type, eventId, occurredAt, orderId, tableId?, itemId?, status, version, rejectionDetails? }`.

#### Consecuencias
- Desacopla la vista de cocina del tráfico del piso del comedor.
- Pruebas IT-17 y SEC-09 verifican el aislamiento entre canales y la matriz de autorización por rol.

---

### ADR-0003: Paleta de colores semánticos y tokens de UI (OP-03)

- **Estado:** Aceptado
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RNF-07, RNF-08, RNF-11

#### Contexto
La guía visual FMAT-RESTAURANT v2.0 presentaba los estados Éxito, Advertencia, Error e Información en capturas de pantalla sin códigos hexadecimales definidos, provocando inconsistencias en componentes compartidos como `StatusBadge`.

#### Decisión
Se adopta formalmente la paleta estándar mediante tokens semánticos definidos bajo el ámbito `.orders-kds-root`:
- `--color-success`: `#16A34A` (Operación exitosa, "En stock", orden "Pagada")
- `--color-warning`: `#D97706` ("Bajo stock", orden "Pendiente", tarjeta por atrasarse)
- `--color-error`: `#DC2626` (Fallos, "Agotado", orden "Rechazada", anulación)
- `--color-info`: `#2563EB` (Mensajes neutros, sincronización de red, orden "Reservada")
- `--color-primary-soft` / `--color-primary`: `#FFF7ED` / `#C2410C` (Selección activa)

Queda prohibido el uso de valores hexadecimales sueltos en componentes. Todo estado se comunica con color acompañado de texto descriptivo o icono para garantizar accesibilidad (contraste AA ≥ 4.5:1).

#### Consecuencias
- Habilita `StatusBadge` para los 10 estados del ciclo de vida (incluyendo `RESERVED`).
- Los tokens quedan declarados en la hoja de estilos raíz y en la configuración de Tailwind del frontend.

---

### ADR-0004: Semántica, idempotencia de `confirm` y reapertura desde `REJECTED` (OP-05)

- **Estado:** Aceptado (Ampliado en Sprint 4 mediante ADR-0016)
- **Fecha:** 2026-10-05 (Actualizado: 2026-10-31)
- **Requisitos relacionados:** RF-32, RF-33, RF-34, RNF-02, RNF-09

#### Contexto
El estado `CREATED` abarca tanto el borrador inicial como el periodo en que la orden ya fue enviada y espera la reserva de Inventario. Un doble toque en la interfaz o un reintento de red en `POST /orders/{id}/confirm` podía provocar publicaciones duplicadas de `ordenes.orden.creada`. Adicionalmente, en Sprint 4 se acordó permitir reabrir y editar una orden `REJECTED` sin perder su `orderId`.

#### Decisión
El endpoint `POST /orders/{id}/confirm` es estrictamente **idempotente**:
1. Si la orden está en `CREATED` sin marca `confirmed_at`: fija `confirmed_at`, `confirmed_by`, consolida cantidades por `offerId`, guarda en base de datos y publica `ordenes.orden.creada` por outbox transaccional con un `eventId` determinista por intento (`attempt_key=order.version`).
2. Si la orden ya tiene `confirmed_at` o se encuentra en `RESERVED`, `IN_PREPARATION`, `DELIVERED`, `WASTED` o `PAID`: responde `200 OK` con el estado actual de la comanda sin duplicar el evento.
3. Si la orden está en `CANCELLED`, `REJECTED`, `EXPIRED` o `VOIDED`: rechaza la operación directa con `409 Conflict` (`INVALID_STATE_TRANSITION`).
4. **Reapertura editable desde `REJECTED` (ADR-0016):** cuando el mesero ejecuta `PATCH /orders/{id}/items` sobre una orden en `REJECTED`, la orden reabre automáticamente a `CREATED` (`order.reopen_as_draft()`), limpia `confirmed_at` y permite volver a llamar a `POST /orders/{id}/confirm` sobre la misma orden.
5. **Cancelación antes de cocción (ADR-0013):** `PATCH /orders/{id}/cancel` está permitido en `CREATED`, `RESERVED` y `REJECTED`, y bloqueado únicamente al entrar a `IN_PREPARATION`.

---

### ADR-0005: Interacción dual táctil principal y Bump Bar en KDS (OP-06)

- **Estado:** Aceptado
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-09, RF-11, RNF-07, RNF-11

#### Contexto
La especificación original obligaba a una interacción de cocina exclusiva mediante teclado físico (Bump Bar sellado) prohibiendo el puntero. En la operativa real de cocina, los monitores son táctiles de pared en formato landscape y el personal requiere operar directamente sobre la pantalla incluso con manos manchadas o guantes, manteniendo el teclado como respaldo.

#### Decisión
El KDS opera en **monitor táctil grande de pared (landscape 1920×1080) con interacción táctil como método principal**, y soporte de teclado/Bump Bar como **método secundario con 100 % de paridad funcional**.

Reglas de diseño táctil:
- Objetivos táctiles mínimos de **64 px** con espaciado de al menos 16 px.
- Acciones frecuentes ("Iniciar preparación" en `RESERVED`, "Marcar ítem listo" y "Orden lista" en `IN_PREPARATION`) se resuelven en un solo toque sin arrastre, doble toque ni pulsaciones largas.

Mapeo de teclado unificado (`KeyboardEvent.code`):

| Tecla | Acción | Equivalente táctil |
|---|---|---|
| `ArrowLeft` / `ArrowRight` | `FOCUS_PREVIOUS_ORDER` / `FOCUS_NEXT_ORDER` | Botones de paginación o toque a la tarjeta |
| `ArrowUp` / `ArrowDown` | `FOCUS_PREVIOUS_ITEM` / `FOCUS_NEXT_ITEM` | Toque sobre el renglón del platillo |
| `Enter` | `MARK_FOCUSED_ITEM_READY` (o `START_PREPARATION` si la orden está en `RESERVED`) | Toque sobre el ítem / botón "Iniciar preparación" |
| `NumpadEnter` | `MARK_FOCUSED_ORDER_READY` (o `START_PREPARATION` en `RESERVED`) | Botón "Orden lista" / "Iniciar preparación" |
| `Backspace` | `UNDO_LAST_READY` (en ventana de gracia) | Botón "Deshacer" |
| `PageUp` / `PageDown` | `PREVIOUS_STATION` / `NEXT_STATION` | Selector de estación |
| `Digit1` … `Digit9` | Selección directa de la tarjeta visible 1 a 9 | Toque directo a la tarjeta |

---

### ADR-0006: Convención de nombres en inglés para rutas y contratos REST (OP-07)

- **Estado:** Aceptado
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-08, RF-28, RNF-10

#### Decisión
Se formalizan todas las rutas, query parameters y cuerpos JSON en **inglés**:
- `GET /orders?status=&diningSessionId=`
- `GET /kds/queue?station=&view=`
- `PATCH /orders/{orderId}/start-preparation`
- `PATCH /orders/{orderId}/items/{itemId}/kitchen-reject`
- `PATCH /orders/{orderId}/void-kitchen-error`
- Cuerpo JSON `{ "note": "..." }` / `{ "reason": "..." }`
- Endpoints en tiempo real: `/ws/kds` y `/ws/waiter`

---

### ADR-0007: Semántica de orden "Entregada" y rol de cocina (OP-08)

- **Estado:** Aceptado
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-11, RF-18, RF-19, RNF-10

#### Decisión
**El mesero no interactúa con ningún endpoint de entrega.** Su responsabilidad física es recoger los platillos al observar la notificación:
1. La transición a `DELIVERED` la produce exclusivamente la cocina al marcar el último platillo listo (`PATCH /orders/{id}/items/{itemId}/ready` o `PATCH /orders/{id}/ready`).
2. En la misma transacción de base de datos, la orden transita `IN_PREPARATION → DELIVERED` y sus platillos activos pasan a `DELIVERED`.
3. El sistema emite la notificación WebSocket por los canales `floor` y `user:{userId}`, informando al mesero que la comanda está lista para ser recogida.
4. Si un usuario con rol `waiter` intenta llamar a los endpoints de cocina, el backend responde `403 FORBIDDEN_ROLE`.

---

### ADR-0008: Estación y tiempo de preparación en catálogo como texto de referencia (OP-09)

- **Estado:** Aceptado
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-02, RF-08, RNF-05, RNF-13

#### Decisión
Ambos campos se definen como **texto libre (`string`) de referencia** almacenados en `CatalogRepository` (Redis):
- `station` (`estacion`): texto libre normalizado (minúsculas, sin acentos). Permite agrupar en filtros y suscribir al canal `kds:station:{station}`.
- `preparation_time_text` (`tiempoPreparacion`): texto puramente informativo para el personal de cocina (por ejemplo: `"15-20 min"`). **No se parsea a enteros ni se utiliza para cálculos matemáticos.**

---

### ADR-0009: Prioridad en cola KDS por antigüedad en cocina e inclusión de `RESERVED` (OP-11)

- **Estado:** Aceptado (Cerrado e implementado en Sprint 4 · ver ADR-0013)
- **Fecha:** 2026-10-05 (Cerrado: 2026-10-31)
- **Requisitos relacionados:** RF-01..04, RF-09, RNF-06, OP-11

#### Decisión
Se establece la prioridad de la cola del KDS ordenando por la **antigüedad real en cocina**:
1. La vista activa del KDS (`view=active`) incluye las órdenes en `RESERVED`, `IN_PREPARATION` y aquellas `DELIVERED` con algún ítem `PENDING`.
2. El orden primario se calcula mediante `COALESCE(in_preparation_at, stock_reserved_at, created_at) ASC`, desempatando por `created_at ASC` y `public_id ASC`.
3. Esto garantiza que una orden en `RESERVED` mantenga su turno justo en la fila desde el instante en que Inventario reservó sus insumos (`stock_reserved_at`).
4. Semáforo visual en tarjetas KDS mediante los umbrales configurables `KDS_WARNING_AFTER_SECONDS` (600 s) y `KDS_LATE_AFTER_SECONDS` (900 s).

---

### ADR-0010: Rehacer platillo con orden en estado `DELIVERED` (OP-12)

- **Estado:** Propuesto (En el dominio de Órdenes; pendiente de cerrar notificación de descuento extra con Inventario en Sprint 5 · `TASK-32`)
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-22, RF-18, RNF-10

#### Decisión
**El estado de la orden matriz no retrocede**:
1. La orden permanece en `DELIVERED`.
2. Únicamente el platillo afectado cambia de estado a `PENDING`, asociando la nota obligatoria del motivo del rehacer (`PATCH /orders/{id}/items/{itemId}/redo`).
3. El KDS resalta visualmente la tarjeta de la orden con un distintivo de ítem rehecho pendiente.
4. En Sprint 5 (`TASK-32`) se cerrará con Inventario la notificación del descuento extraordinario de insumos al volver a preparar dicho platillo (ver `docs/adr/Puntos_Abiertos_Pendientes.md` §2.1).

---

### ADR-0011: Descarte de funcionalidad opcional de platillos intermedios (TASK-27 NO-GO)

- **Estado:** Aceptado (NO-GO)
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-36, RF-37, RF-38

#### Decisión
Se declara **NO-GO formal** para la funcionalidad de platillos intermedios durante el horizonte de 7 sprints (`INTERMEDIATE_DISHES_ENABLED: 'false'`), manteniendo la arquitectura base abierta para una iteración futura.
