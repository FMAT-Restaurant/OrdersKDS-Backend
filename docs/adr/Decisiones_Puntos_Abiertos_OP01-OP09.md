# Orders & KDS — Registro Consolidado de Decisiones de Arquitectura (ADR) y Puntos Abiertos

> **Origen:** TASK-37 (S1) y TASK-C37 (S2) · **Fecha de actualización:** 12 de octubre de 2026  
> **Aplica a:** `ordenes-kds-backend` y `ordenes-kds-frontend`  
> **Ubicación:** `docs/Decisiones_Puntos_Abiertos_OP01-OP09.md`  
> **Estándar:** Formato Architecture Decision Record (ADR) según `CONTRIBUTING.md` §10  
>
> **Convención de este documento:**
> - Las decisiones aprobadas se registran con estado **Aceptado**.
> - Los acuerdos pendientes de validación técnica de campo o de otros equipos se marcan como **Propuesto**.
> - Los identificadores de código, rutas y campos JSON van en inglés (`DEVELOPMENT_GUIDELINES.md` §2); los nombres de eventos del broker se mantienen según los contratos pactados con otros microservicios.

---

## 1. Matriz de Estado de Puntos Abiertos (TASK-37 / TASK-C37)

| Elemento | Estado original (S1) | Estado consolidado (S2 / TASK-C37) | ADR / Referencia | Dueño | Fecha / Hito |
|---|---|---|---|---|---|
| **OP-01** `SAGA_TIMEOUT_SECONDS` | `[ ]` sin marcar | `[x]` **Aceptado**: 60 s como máximo desde `confirmed_at` | ADR-0001 | Backend | S1 / Cerrado |
| **OP-02** Canales WebSocket | `[ ]` sin marcar | `[x]` **Propuesto**: dos endpoints (`/ws/kds`, `/ws/waiter`) y canales por estación, piso y usuario | ADR-0002 | Backend + Frontend | S2 / Cerrado |
| **OP-03** Colores semánticos | `[ ]` sin marcar | `[x]` **Aceptado**: paleta estándar en guía visual; tokens `--color-success/warning/error/info` | ADR-0003 | Frontend | S1 / Cerrado |
| **OP-05** `confirm` repetido | `[ ]` sin marcar | `[x]` **Aceptado**: `confirm` idempotente con marcas `confirmed_at` y `confirmed_by` | ADR-0004 | Backend | S1 / Cerrado |
| **OP-06** Interacción KDS / Bump Bar | `[ ]` sin marcar | `[x]` **Aceptado**: KDS táctil como entrada principal, teclado/Bump Bar secundario con paridad total | ADR-0005 | Frontend | S1 / Cerrado |
| **OP-07** Rutas en inglés | `[ ]` sin marcar | `[x]` **Aceptado**: rutas, parámetros y campos REST en inglés; eventos broker sin cambio | ADR-0006 | Backend + Frontend | S1 / Cerrado |
| **OP-08** Quién marca "Entregado" | `[ ]` sin marcar | `[x]` **Aceptado**: cocina lo marca, sin endpoint `deliver`, mesero recoge sin registro en sistema | ADR-0007 | Backend + Frontend | S1 / Cerrado |
| **OP-09** Tiempo y estación catálogo | `[ ]` sin marcar | `[x]` **Aceptado**: ambos campos son texto (`string`) de referencia | ADR-0008 | Backend + Menú | S1 / Cerrado |
| **OP-10** Topología y credenciales broker | `[x]` con URL expuesta | `[ ]` **En curso**: falta revisar el `.zip` del broker y confirmar exchanges/colas. Credenciales retiradas de texto claro; referencia exclusiva a secretos (`RABBITMQ_URL`) | OP-10 (§3.2) | Infra / Broker + Backend | S2 / En curso |
| **OP-04** Contrato `orden.expirada` y `motivo` | No existía | `[ ]` **Pendiente**: contrato de evento con Inventario | OP-04 (§3.1) | Backend + Inventario | INT-01 (S6) |
| **OP-11** Prioridad en cola KDS | No existía | `[x]` **Propuesto**: mayor tiempo en cocina primero (`in_preparation_at`) | ADR-0009 | Backend + Frontend | S2 / Propuesto |
| **OP-12** Rehacer ítem con orden `DELIVERED` | No existía | `[x]` **Propuesto**: orden conserva `DELIVERED`, solo el ítem pasa a `PENDING` | ADR-0010 | Backend + Frontend | S2 / Propuesto |
| **OP-13** Aviso `DELIVERED` al mesero | No existía | `[ ]` **Pendiente**: monitor de Sala vs. componente propio en frontend | OP-13 (§3.3) | Frontend + Sala | INT-03 (S6) |
| **TASK-27** Platillos intermedios (chef) | `[ ]` sin marcar | `[x]` **Aceptado (NO-GO)**: excluido del horizonte de 7 sprints (`INTERMEDIATE_DISHES_ENABLED=false`) | ADR-0011 | Arquitectura | S1 / Cerrado |

---

## 2. Registro de Decisiones de Arquitectura (ADR)

### ADR-0001: Valor y gobernanza de `SAGA_TIMEOUT_SECONDS` (OP-01)

- **Estado:** Aceptado
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-35, RNF-01, RNF-10

#### Contexto
Si el microservicio de Inventario no responde al evento `ordenes.orden.creada`, la comanda queda bloqueada indefinidamente en `CREATED`. El documento ERS define el estado `EXPIRED` para resolver este escenario, pero omitió fijar el tiempo límite (ERS §10, punto 7).

#### Decisión
Se fija un tiempo límite estricto de **60 segundos como máximo**:
1. `SAGA_TIMEOUT_SECONDS=60` como valor obligatorio acordado.
2. La regla de expiración se evalúa contando **≥ 60 segundos transcurridos desde `confirmed_at`** *(propuesto)*. El temporizador inicia en el momento de la confirmación por el mesero y no en la creación del borrador.
3. Se define una frecuencia para el Saga Timeout Watcher de `SAGA_WATCHER_INTERVAL_SECONDS=5` *(propuesto)*.
4. **Sin valor por defecto en código:** el microservicio aborta su arranque si la variable de entorno no está configurada, obligando a su definición explícita en cada despliegue.

#### Consecuencias
- Inventario debe suscribirse a `ordenes.orden.expirada` para liberar stock reservado tardíamente.
- Las pruebas unitarias e integración (UT-BE-50) validan límites en 59, 60 y 61 segundos mediante inyección de reloj.
- A nivel de usuario, la interfaz muestra "pendiente de confirmación de stock"; si expira, la orden queda en estado terminal `EXPIRED`.

---

### ADR-0002: Alcance y autenticación de canales WebSocket (OP-02)

- **Estado:** Propuesto
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RNF-04, RNF-06, RNF-12

#### Contexto
La Arquitectura §3.3 estipula una conexión bidireccional persistente por sesión autenticada con JWT, pero no detalla la taxonomía de canales ni las restricciones por rol, lo que causaba incertidumbre sobre el tráfico que recibe cada cliente.

#### Decisión
Se establecen **dos endpoints** WebSocket segregados por rol y cuatro canales normalizados:

| Canal | Endpoint | Roles autorizados | Modo de suscripción | Tráfico recibido |
|---|---|---|---|---|
| `kds:station:{station}` | `/ws/kds` | `kitchen`, `chef`, `admin` | Solicitado por el cliente (cambio sin reconectar) | Cambios de estado de órdenes con ítems de la estación e ítems asociados |
| `kds:all` | `/ws/kds` | `kitchen`, `chef`, `admin` | Solicitado por el cliente | Todas las órdenes activas en cocina sin filtro |
| `floor` | `/ws/waiter` | `waiter`, `admin` | Automático al conectar | Cambios de estado de todas las órdenes activas del restaurante |
| `user:{userId}` | `/ws/waiter` | `waiter`, `admin` | Automático desde el claim del JWT | Alertas de órdenes bajo su responsabilidad (`REJECTED`, `EXPIRED`, `DELIVERED`) |

Reglas clave:
- El handshake rechaza tokens ausentes o inválidos con código `4401`, y roles no permitidos con código `4403`.
- Intentar suscribirse a un canal ajeno al rol responde con `{ "error": "FORBIDDEN_CHANNEL" }`.
- Las cargas útiles son señales mínimas en formato camelCase: `{ type, eventId, occurredAt, orderId, tableId?, itemId?, status, version }`.

#### Consecuencias
- Desacopla la vista de cocina del tráfico del piso del comedor.
- Pruebas IT-17 y SEC-09 verifican el aislamiento entre canales y la matriz de autorización por rol.
- Si el backend se escala a múltiples instancias, se empleará Redis pub/sub para sincronizar los canales.

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
- `--color-info`: `#2563EB` (Mensajes neutros, sincronización de red)
- `--color-primary-soft` / `--color-primary`: `#FFF7ED` / `#C2410C` (Selección activa)

Queda prohibido el uso de valores hexadecimales sueltos en componentes. Todo estado se comunica con color acompañado de texto descriptivo o icono para garantizar accesibilidad (contraste AA ≥ 4.5:1).

#### Consecuencias
- Habilita la finalización de `StatusBadge` para los nueve estados del ciclo de vida.
- Los tokens quedan declarados en la hoja de estilos raíz y en la configuración de Tailwind del frontend.

---

### ADR-0004: Semántica e idempotencia de `confirm` (OP-05)

- **Estado:** Aceptado
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-32, RF-33, RNF-02, RNF-09

#### Contexto
El estado `CREATED` abarca tanto el borrador inicial como el periodo en que la orden ya fue enviada y espera la reserva de Inventario. Un doble toque en la interfaz o un reintento de red en `POST /orders/{id}/confirm` podía provocar publicaciones duplicadas de `ordenes.orden.creada`.

#### Decisión
El endpoint `POST /orders/{id}/confirm` es estrictamente **idempotente**:
1. Si la orden está en `CREATED` sin marca `confirmed_at`: fija `confirmed_at`, `confirmed_by`, guarda en base de datos y publica `ordenes.orden.creada` por outbox transaccional una sola vez.
2. Si la orden ya tiene `confirmed_at` o se encuentra en `IN_PREPARATION`, `DELIVERED`, `WASTED` o `PAID`: responde `200 OK` con el estado actual de la comanda sin duplicar el evento.
3. Si la orden está en `CANCELLED`, `REJECTED`, `EXPIRED` o `VOIDED`: rechaza la operación con `409 Conflict` (`INVALID_STATE_TRANSITION`).
4. **Congelación de ítems:** cualquier llamada a `PATCH /orders/{id}/items` sobre una orden con `confirmed_at` responde `409 Conflict`.
5. La cancelación (`POST /orders/{id}/cancel`) sigue permitida mientras permanezca en `CREATED`.

#### Consecuencias
- Requiere añadir las columnas `confirmed_at` y `confirmed_by` al modelo relacional de órdenes (TASK-C03).
- Las pruebas de integración validan confirmaciones simultáneas mediante bloqueo pesimista `SELECT FOR UPDATE` y control de concurrencia optimista (`version`).

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
- Acciones frecuentes ("Marcar ítem listo" y "Orden lista") se resuelven en un solo toque sin arrastre, doble toque ni pulsaciones largas.
- Ventana de gracia de 3 segundos *(propuesto)* antes de emitir la confirmación irreversible a cocina.

Mapeo de teclado unificado (`KeyboardEvent.code`):

| Tecla | Acción | Equivalente táctil |
|---|---|---|
| `ArrowLeft` / `ArrowRight` | `FOCUS_PREVIOUS_ORDER` / `FOCUS_NEXT_ORDER` | Botones de paginación o toque a la tarjeta |
| `ArrowUp` / `ArrowDown` | `FOCUS_PREVIOUS_ITEM` / `FOCUS_NEXT_ITEM` | Toque sobre el renglón del platillo |
| `Enter` | `MARK_FOCUSED_ITEM_READY` | Toque sobre el ítem |
| `NumpadEnter` | `MARK_FOCUSED_ORDER_READY` | Botón "Orden lista" |
| `Backspace` | `UNDO_LAST_READY` (en ventana de gracia) | Botón "Deshacer" |
| `PageUp` / `PageDown` | `PREVIOUS_STATION` / `NEXT_STATION` | Selector de estación |
| `Digit1` … `Digit9` | Selección directa de la tarjeta visible 1 a 9 | Toque directo a la tarjeta |

#### Consecuencias
- Se reformula el requisito RNF-07.
- La matriz de pruebas de V&V incluye variantes táctiles con Playwright (`locator.tap()`) y variantes de teclado (`page.keyboard`).
- La prueba de aceptación UAT-01 se realiza en hardware táctil real con manos sucias.

---

### ADR-0006: Convención de nombres en inglés para rutas y contratos REST (OP-07)

- **Estado:** Aceptado
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-08, RF-28, RNF-10

#### Contexto
La Arquitectura §3.2 contenía rutas y parámetros con palabras en español (`/void-error-cocina`, `?estado=`), contradiciendo la regla del proyecto de mantener el código y la API REST en inglés técnico (`DEVELOPMENT_GUIDELINES.md` §2).

#### Decisión
Se formalizan todas las rutas, query parameters y cuerpos JSON en **inglés**:
- `GET /orders?status=` (en vez de `?estado=`)
- `GET /kds/queue?station=` (en vez de `?estacion=`)
- `PATCH /orders/{orderId}/void-kitchen-error` (en vez de `/void-error-cocina`)
- `POST /intermediate-dishes` (en vez de `/platillos-intermedios`, gobernado por flag)
- Cuerpo JSON `{ "note": "..." }` (en vez de `"nota"`)
- Endpoints en tiempo real: `/ws/kds` y `/ws/waiter`

**Aislamiento de contratos:** los nombres de eventos de RabbitMQ (`ordenes.orden.creada`, `inventario.stock.reservado`) son contratos ya pactados con otros equipos de la facultad y **se conservan en español**, aislados en `app/messaging/events.py`.

#### Consecuencias
- Alineación total de colecciones Newman/Postman, esquemas Pydantic y esquemas Zod en inglés.
- Se previene la dispersión terminológica en revisiones de código.

---

### ADR-0007: Semántica de orden "Entregada" y rol de cocina (OP-08)

- **Estado:** Aceptado
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-11, RF-18, RF-19, RNF-10

#### Contexto
El ERS §1.1 definía `Entregada` como "todos los platillos marcados Listo en cocina y el mesero los entregó en mesa", pero el sistema no disponía ni contemplaba ningún endpoint para que el mesero confirmara la entrega física en mesa.

#### Decisión
**El mesero no interactúa con ningún endpoint de entrega.** Su responsabilidad física es recoger los platillos al observar la notificación:
1. La transición a `DELIVERED` la produce exclusivamente la cocina al marcar el último platillo listo (`PATCH /orders/{id}/items/{itemId}/ready` o `PATCH /orders/{id}/ready`).
2. En la misma transacción de base de datos, la orden transita `IN_PREPARATION → DELIVERED` y sus platillos pasan a `DELIVERED` *(propuesto)*.
3. El sistema emite la notificación WebSocket por los canales `floor` y `user:{userId}`, informando al mesero que la comanda está lista para ser recogida.
4. El traslado de la charola a la mesa de los comensales no se registra en el software por carecer de valor transaccional.
5. Se prohíbe cualquier endpoint `deliver`. Si un usuario con rol `waiter` intenta llamar a los endpoints de cocina, el backend responde `403 FORBIDDEN_ROLE`.

#### Consecuencias
- Se actualiza la definición de `Entregada` en el ERS §1.1.
- Se libera la holgura de desarrollo (+2 pts) de tareas que contemplaban un flujo de entrega por el mesero.
- La solicitud de cuenta (`request-bill`) se autoriza estando la orden en `DELIVERED`.

---

### ADR-0008: Estación y tiempo de preparación en catálogo como texto de referencia (OP-09)

- **Estado:** Aceptado
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-02, RF-08, RNF-05, RNF-13

#### Contexto
El KDS requiere conocer la estación asignada a cada platillo para filtrar comandas (RF-08) y mostrar un tiempo estimado de elaboración. No obstante, el contrato del microservicio de Menú (`menu.catalogo.actualizado`) no exponía estos campos de forma estructurada ni numérica.

#### Decisión
Ambos campos se definen como **texto libre (`string`) de referencia**:
- `station` (`estacion`): texto libre normalizado (minúsculas, sin acentos). Permite agrupar en filtros y suscribir al canal `kds:station:{station}`.
- `preparation_time_text` (`tiempoPreparacion`): texto puramente informativo para el personal de cocina (por ejemplo: `"15-20 min"`). **No se parsea a enteros ni se utiliza para cálculos matemáticos.**

Reglas complementarias:
- Si un platillo carece de estación en el catálogo, se muestra bajo la estación general y en la vista global `kds:all`.
- Al ser texto libre procedente de un servicio externo, el frontend debe renderizarlo sanitizado y escapado para mitigar riesgos de inyección XSS.

#### Consecuencias
- El cálculo de prioridad de RF-02 no puede depender de una fórmula matemática sobre el tiempo de receta (ver ADR-0009).
- Se solicita al equipo de Menú la inclusión de ambos campos como strings opcionales en el payload del evento y en `GET /menu/catalogo/completo`.

---

### ADR-0009: Prioridad en cola KDS por tiempo en cocina (OP-11)

- **Estado:** Propuesto
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-02, RF-03, RNF-06

#### Contexto
Al determinarse en el ADR-0008 que el tiempo de preparación del catálogo es un texto informativo no numérico, la regla original de RF-02 ("ordenar la cola por tiempo acumulado de preparación de los platillos") queda inoperante técnicamente.

#### Decisión
Se reformula la prioridad de la cola del KDS ordenando por el **tiempo transcurrido en cocina**:
1. El orden primario se calcula con mayor tiempo en cocina primero, medido desde el instante en que la orden entró a preparación (`in_preparation_at`).
2. El desempate cronológico (RF-03) se resuelve por `created_at` y posteriormente por `id`.
3. Semáforo visual en tarjetas KDS mediante los umbrales configurables:
   - `KDS_WARNING_AFTER_SECONDS` (por defecto 600 s / 10 min): comanda "Por atrasarse" (`--color-warning`).
   - `KDS_LATE_AFTER_SECONDS` (por defecto 900 s / 15 min): comanda "Atrasada" (`--color-error`).
4. El cronómetro visible de cada tarjeta mantiene su conteo desde la creación original de la orden.

#### Consecuencias
- Requiere agregar la columna `in_preparation_at` en la tabla `orders` (TASK-C03), la cual es estampada por el consumer que procesa `inventario.stock.reservado`.
- La cola pasa a operar como un modelo FIFO ordenado por antigüedad real en cocina.

---

### ADR-0010: Rehacer platillo con orden en estado `DELIVERED` (OP-12)

- **Estado:** Propuesto
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-22, RF-18, RNF-10

#### Contexto
Cuando un comensal solicita rehacer un platillo (RF-22) porque no cumplió con sus expectativas, la orden matriz ya se encuentra en `DELIVERED`. Sin embargo, la máquina de estados de la orden prohíbe la transición en reversa `DELIVERED → IN_PREPARATION`.

#### Decisión
**El estado de la orden matriz no retrocede**:
1. La orden permanece en `DELIVERED`.
2. Únicamente el platillo afectado cambia de estado a `PENDING`, asociando la nota obligatoria del motivo del rehacer (`PATCH /orders/{id}/items/{itemId}/redo`).
3. El KDS resalta visualmente la tarjeta de la orden con un distintivo de ítem rehecho pendiente.
4. Cuando la estación de cocina finaliza la nueva preparación, marca el platillo nuevamente como `READY`, pasando de inmediato a `DELIVERED`.
5. La máquina de estados principal de órdenes conserva su invariante unidireccional sin ciclos complejos.

#### Consecuencias
- Clarifica el ciclo de vida de los ítems frente al ciclo de vida de la comanda.
- Pruebas IT-07 y AT-07 validan que la orden conserve `DELIVERED` mientras el platillo transita a `PENDING`.

---

### ADR-0011: Descarte de funcionalidad opcional de platillos intermedios (TASK-27 NO-GO)

- **Estado:** Aceptado (NO-GO)
- **Fecha:** 2026-10-05
- **Requisitos relacionados:** RF-36, RF-37, RF-38

#### Contexto
El alcance del sistema contemplaba una funcionalidad opcional de 8 puntos de historia (TASK-27a backend y TASK-27b frontend) para permitir al chef registrar lotes de preparación intermedia (`POST /intermediate-dishes`). El equipo evaluó la viabilidad dentro del horizonte de 7 sprints del curso escolar.

#### Decisión
Se declara **NO-GO formal** para la funcionalidad de platillos intermedios:
1. No se destinará esfuerzo de desarrollo en el backend ni en el frontend para este módulo durante los 7 sprints.
2. De acuerdo con la regla de gobierno de V&V §13.5, el código y las pruebas viven o mueren juntos:
   - Se fija `INTERMEDIATE_DISHES_ENABLED: 'false'` en el bloque de variables globales (`env:`) de `.github/workflows/ci.yml`, evitando versionar archivos `.env` sueltos en el repositorio y garantizando trazabilidad nativa en GitHub Actions.
   - Las pruebas etiquetadas con `@optional-intermediate-dishes` quedan excluidas del pipeline de integración sin penalizar el Quality Gate de SonarCloud.

#### Consecuencias
- Se enfoca el esfuerzo en la estabilidad de las transacciones principales, la saga transaccional y la cola KDS durante los 7 sprints del curso escolar.
- **Apertura explícita a fase futura:** Queda formal y plenamente abierta la posibilidad de que la funcionalidad de platillos intermedios sea implementada en una iteración futura o release posterior. Toda la arquitectura base (desacoplamiento asíncrono vía Transactional Outbox, ruta REST `POST /intermediate-dishes`, evento `ordenes.platillo_intermedio.preparado` / `INTERMEDIATE_DISH_PREPARED`, y control por feature toggle `INTERMEDIATE_DISHES_ENABLED`) se mantiene documentada y compatible, de modo que su incorporación futura no requerirá rediseño arquitectónico ni modificaciones disruptivas al modelo de dominio.
- Cualquier reactivación requerirá un nuevo ADR de reactivación y cambiar el valor del feature flag a `true`.

---

## 3. Puntos Abiertos Pendientes y en Curso

### 3.1 OP-04: Contrato de `ordenes.orden.expirada` y campo `motivo`
- **Estado:** Pendiente
- **Dueño:** Backend + Inventario
- **Hito de resolución:** Integración INT-01 (Sprint 6)
- **Descripción:** Confirmar con el equipo de Inventario el esquema definitivo del payload del evento `ordenes.orden.expirada` y la inclusión del campo `motivo` (por timeout de saga vs. rechazo explícito).
- **Supuesto temporal:** Se mantienen los esquemas propuestos en `contracts/events/ordenes.orden.expirada.json` sin alterar el modelo de datos.

### 3.2 OP-10: Topología de RabbitMQ y variables del broker
- **Estado:** En curso
- **Dueño:** Infraestructura / Broker + Backend
- **Hito de resolución:** Sprint 2
- **Descripción:** El equipo dueño del broker compartió la topología base. Falta revisar la estructura de exchanges y colas en el archivo de prueba `.zip` para homologar dead-letter exchanges (DLX) y reintentos.
- **Regla estricta de seguridad:** **Queda terminantemente prohibido incluir URLs completas con credenciales en texto plano en la documentación o código del repositorio.** Todas las referencias deben apuntar exclusivamente a la variable `RABBITMQ_URL` inyectada a través de secretos seguros (*GitHub Secrets* / *Environments*).

### 3.3 OP-13: Canal de notificación de órdenes listas para el mesero
- **Estado:** Pendiente
- **Dueño:** Frontend + Sala
- **Hito de resolución:** Integración INT-03 (Sprint 6)
- **Descripción:** Definir si la alerta visual de que una orden pasó a `DELIVERED` en cocina se consume directamente en el monitor de mesas administrado por el equipo de Sala o si requiere una vista/widget flotante dedicado en el cliente del mesero.
- **Supuesto temporal:** El backend publica el cambio a través de los canales `/ws/waiter` (`floor` y `user:{userId}`) según lo convenido en el ADR-0002.
