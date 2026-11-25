# Especificación de Requisitos de Software (ERS) — Lista de Requisitos (Orders & KDS)
## Microservicio de Órdenes y Pantalla de Cocina (Orders & KDS Service)

Documento de referencia rápida alineado al estado real del código fuente (`orders-backend` y `orders-frontend`). Para la especificación formal completa bajo estándar IEEE 830 / ISO/IEC/IEEE 29148 véase [ERS_IEEE_OrdenesKDS.md](./ERS_IEEE_OrdenesKDS.md).

---

# 1. Requisitos Funcionales (RF)

### Módulo A: Visualización y Cola de Cocina (KDS)

- **RF-01 · Agrupación visual de comandas por estado y vigencia**
  - **Descripción:** La pantalla de cocina (KDS) clasifica y renderiza las órdenes en pestañas operativas según su estado y vigencia: *Pendientes* (`RESERVED` vigente), *En preparación* (`IN_PREPARATION`), *Activas*, *Para rehacer* (`DELIVERED` con ítems `PENDING`), *Entregadas* (`DELIVERED`) y *Caducados* (`EXPIRED` o `RESERVED` cuyo TTL de 15 min + 10 min de gracia haya vencido).

- **RF-02 · Ordenamiento por antigüedad en cocina y reserva (FIFO)**
  - **Descripción:** La cola KDS ordena las comandas de forma ascendente según `COALESCE(in_preparation_at, stock_reserved_at, created_at)`, manteniendo al frente las órdenes con mayor tiempo de espera.

- **RF-03 · Criterio de desempate cronológico**
  - **Descripción:** Cuando dos o más órdenes compartan la misma marca de tiempo prioritaria, el sistema desempata deterministamente por `created_at` ascendente y posteriormente por `public_id` ascendente.

- **RF-04 · Retiro automático de comandas pagadas**
  - **Descripción:** En cuanto una comanda transita al estado terminal `PAID` (tras consumirse `pagos.pago.completado`), el sistema excluye la orden de la consulta KDS y actualiza la UI en tiempo real.

- **RF-05 · Visualización integral del platillo (modificadores y notas)**
  - **Descripción:** Cada tarjeta de comanda en el KDS expone cantidad, nombre del platillo, modificadores seleccionados, notas especiales de preparación, estación y tiempo de preparación de referencia.

- **RF-06 · Identificador visible de mesa o pedido para llevar**
  - **Descripción:** Cada tarjeta muestra de forma destacada el número de mesa física asignada o la etiqueta "Para llevar" (`isTakeaway`), junto con el identificador numérico legible de la orden (`orderNumber`) o respaldo hexadecimal corto.

- **RF-07 · Cronómetro, semáforo de atraso y alerta de TTL de reserva**
  - **Descripción:** Cada tarjeta muestra el tiempo transcurrido con indicadores visuales (`on_time`, `warning`, `late` configurables por `KDS_WARNING_AFTER_SECONDS` / `KDS_LATE_AFTER_SECONDS`) y un conteo regresivo de gracia de 10 minutos cuando una orden `RESERVED` excede el TTL de 15 minutos desde `stock_reserved_at`.

- **RF-08 · Filtrado operativo por estación culinaria**
  - **Descripción:** Permite filtrar los ítems visibles por estación culinaria normalizada (`GET /kds/queue?station=...`) sin perder el contexto de la comanda cuando al menos un ítem coincide con la estación.

---

### Módulo B: Control de Elaboración, Despacho y Rechazo en Cocina

- **RF-09 · Inicio explícito de preparación en cocina (`RESERVED → IN_PREPARATION`)**
  - **Descripción:** Permite al personal de cocina o chef iniciar la preparación de una comanda en `RESERVED` (`PATCH/POST /orders/{orderId}/start-preparation`), fijando `in_preparation_at` y publicando `ordenes.orden.en_preparacion` (`ordenes.order.served`) para el descuento FEFO en Inventario.

- **RF-10 · Marcado de preparación por platillo individual y evento `platillo.preparado`**
  - **Descripción:** Permite marcar un ítem `PENDING` como `READY` (`PATCH /orders/{orderId}/items/{itemId}/ready`), publicando `ordenes.platillo.preparado` con el indicador `isRedo` y transicionando la orden a `DELIVERED` cuando todos los ítems activos han sido completados.

- **RF-11 · Marcado masivo de comanda completa ("Orden lista")**
  - **Descripción:** Permite marcar todos los ítems pendientes de una orden `IN_PREPARATION` en una sola acción (`PATCH /orders/{orderId}/ready`), publicando los eventos pendientes y transicionando la orden e ítems a `DELIVERED`. En la UI KDS ambas acciones de despacho cuentan con una ventana de cancelación (**Deshacer**) de 15 segundos.

- **RF-11B · Rechazo físico por falta de insumos y cancelación individual de ítem**
  - **Descripción:** Permite a cocina rechazar un ítem en `RESERVED` o en rehechura (`PATCH /orders/{orderId}/items/{itemId}/kitchen-reject`) indicando motivo y `availableQuantity`; si es `0` inhabilita el platillo en Redis, publica `ordenes.platillo.rechazado_cocina` y devuelve órdenes `RESERVED` a `REJECTED` (con `rejectedItems`/`reservedItems`). También permite cancelar un ítem individual (`ItemStatus.CANCELLED`) mediante `PATCH /orders/{orderId}/items/{itemId}/cancel`.

---

### Módulo C: Gestión de Comandas y Terminal del Mesero

- **RF-12 · Apertura de comanda vinculada a mesa física y sesión**
  - **Descripción:** El mesero puede crear comandas en `CREATED` asociadas a una mesa física (`tableNumber`) y opcionalmente a `diningSessionId` (1:N órdenes por sesión).

- **RF-13 · Apertura de comanda "Para Llevar"**
  - **Descripción:** Permite crear comandas con `isTakeaway=true` sin número de mesa obligatorio.

- **RF-14 · Captura de renglones, modificadores y notas con doble identificador**
  - **Descripción:** Permite armar y reemplazar líneas de platillo en borrador con soporte dual (`dishId`, `sourceId`, `offerId`), congelando `unitPrice` en confirmación/edición y preservando historial de ítems.

- **RF-15 · Trazabilidad y autorización PBAC + RBAC por identidad Auth/Gateway**
  - **Descripción:** Extrae identidad, roles y permisos desde encabezados del API Gateway (`x-user-id`, `x-staff-id`, `x-user-roles`, `x-user-role`, `x-user-permissions`, `x-user-type`) o JWT (`ADR-0011`), evaluando las 16 políticas `AccessPolicy` (`orders:*`, `kitchen:*`) y trazando `created_by` / `responsible_waiter_id` / `confirmed_by`.

- **RF-16 · Reasignación o transferencia entre mesas**
  - **Descripción:** Permite transferir una comanda no terminal a otra mesa (`PATCH /orders/{orderId}/transfer`) validando concurrencia optimista mediante `version` o cabecera `If-Match`.

- **RF-17 · Bloqueo preventivo de platillos no disponibles**
  - **Descripción:** Valida cada platillo agregado contra la vista local materializada en Redis (`CatalogRepository`), rechazando con `422 DISH_UNAVAILABLE` aquellos marcados como `available=false`.

---

### Módulo D: Sincronización, Ciclo de Vida y Cobro

- **RF-18 · Actualización reactiva en tiempo real (WebSockets + Redis Pub/Sub)**
  - **Descripción:** Refleja transiciones de orden e ítems sobre `/ws/waiter` y `/ws/kds` mediante difusión local y puente `orders:realtime:events` sin necesidad de *polling*.

- **RF-19 · Emisión confiable de eventos de dominio (Outbox + dual routing)**
  - **Descripción:** Etapa todos los eventos de integración (`ordenes.orden.*`, `ordenes.platillo.*`, `ordenes.comanda.mermada`, `ordenes.cobro.solicitado`, `ordenes.cuenta.solicitada`) en la tabla `outbox` y los publica al broker con `publisher_confirms`.

- **RF-20 · Solicitud de cuenta con precios congelados**
  - **Descripción:** Mediante `POST/PATCH /orders/{orderId}/request-bill` sobre órdenes en `DELIVERED`, publica `ordenes.cuenta.solicitada` incluyendo precios congelados, `orderId` y `sessionId`.

- **RF-21 · Cierre terminal exclusivo por confirmación de pago**
  - **Descripción:** Una orden únicamente pasa a `PAID` al consumir `pagos.pago.completado` estando en estado `DELIVERED` o `WASTED`.

- **RF-22 · Retorno de platillo entregado para rehechura (`redo`)**
  - **Descripción:** Permite devolver un ítem de una orden `DELIVERED` a `PENDING` (`PATCH /orders/{orderId}/items/{itemId}/redo`) exigiendo `note` no vacía, preservando las notas previas de preparación para restaurarlas al finalizar la rehechura.

---

### Módulo E: Modificaciones, Cancelaciones y Anulaciones (Mermas)

- **RF-23 · Edición de contenido y reapertura desde `REJECTED`**
  - **Descripción:** La edición de ítems (`PATCH /orders/{orderId}/items`) está permitida mientras la orden esté en `CREATED` sin `confirmed_at`, o en `REJECTED` (reabriéndola automáticamente a `CREATED` y limpiando `confirmed_at`).

- **RF-24 · Cancelación libre en `CREATED`, `RESERVED` o `REJECTED`**
  - **Descripción:** Permite cancelar la comanda completa (`PATCH/POST /orders/{orderId}/cancel`) cuando se encuentre en `CREATED`, `RESERVED` o `REJECTED`, transicionando a `CANCELLED`.

- **RF-25 · Emisión de cancelación y liberación de reservas**
  - **Descripción:** Al cancelar una orden se almacena atómicamente `ordenes.orden.cancelada` (`itemsCancelados`) para liberar stock en Inventario sin generar cobro al cliente.

- **RF-26 · Anulación administrativa exclusiva de administrador (`WASTED` / `VOIDED`)**
  - **Descripción:** Permite únicamente al rol/permiso administrativo (`orders:orders:void_admin`) anular una orden en `IN_PREPARATION` (`PATCH/POST /orders/{orderId}/void`), emitiendo siempre `ordenes.comanda.mermada` (`reason="ADMINISTRATIVE"`) hacia Inventario.

- **RF-27 · Cobro configurable en anulación administrativa (`chargeCustomer`)**
  - **Descripción:** Cuando `chargeCustomer=true` (por defecto), transiciona a `WASTED` y publica conjuntamente `ordenes.comanda.mermada` + `ordenes.cobro.solicitado` con precios congelados en la misma transacción; cuando `chargeCustomer=false`, transiciona a `VOIDED` y publica únicamente `ordenes.comanda.mermada` sin cobro.

- **RF-28 · Anulación por error de cocina (`VOIDED`)**
  - **Descripción:** Permite únicamente a administrador o chef (`kitchen:orders:void_error`) anular toda una orden en `IN_PREPARATION` (`PATCH/POST /orders/{orderId}/void-kitchen-error`), transicionando a `VOIDED`.

- **RF-29 · Justificación obligatoria en error de cocina**
  - **Descripción:** La anulación por error de cocina rechaza con `422 NOTE_REQUIRED` si `note` (o alias `reason`/`motivo`) falta o está vacía.

- **RF-30 · Registro de pérdida de insumos en error de cocina**
  - **Descripción:** Al transicionar a `VOIDED` publica `ordenes.comanda.mermada` con `reason="KITCHEN_ERROR"`, `motivo="error_cocina"` y la nota explicativa.

- **RF-31 · Exención total de cobro al cliente en error de cocina**
  - **Descripción:** Una orden `VOIDED` nunca publica eventos de cobro (`ordenes.cobro.solicitado` / `ordenes.cuenta.solicitada`).

---

### Módulo F: Coordinación Transaccional (Saga) e Integración Externa

- **RF-32 · Confirmación y solicitud de reserva a Inventario**
  - **Descripción:** `POST /orders/{orderId}/confirm` fija `confirmed_at` de forma idempotente bajo bloqueo `FOR UPDATE` y etapa `ordenes.orden.creada` con ítems activos consolidados.

- **RF-33 · Reserva confirmada por Inventario (`CREATED → RESERVED`)**
  - **Descripción:** Al consumir `inventario.stock.reservado` / `inventory.reservation.confirmed`, transiciona la orden a `RESERVED` (fijando `stock_reserved_at`) y etapa `ordenes.order.confirmed`. Ignora respuestas tardías si la orden ya está `EXPIRED` o `CANCELLED`.

- **RF-34 · Stock insuficiente (`CREATED → REJECTED`) y alerta al mesero**
  - **Descripción:** Al consumir `inventario.stock.insuficiente` / `inventory.reservation.rejected`, transiciona la orden a `REJECTED`, almacena `rejection_details` (`rejectedItems`, `reservedItems`, `missingIngredients`) y notifica por WebSocket.

- **RF-35 · Expiración automática de Saga (`CREATED → EXPIRED`)**
  - **Descripción:** `SagaTimeoutWatcher` evalúa cada `SAGA_WATCHER_INTERVAL_SECONDS` (5 s) las órdenes `CREATED` con `confirmed_at` mayor a `SAGA_TIMEOUT_SECONDS` (60 s), marcándolas `EXPIRED` y publicando `ordenes.orden.expirada`.

- **RF-36..RF-38 · Lotes de platillos intermedios [Opcional / Fase Futura]**
  - **Descripción:** Funcionalidad deshabilitada en el alcance actual (`INTERMEDIATE_DISHES_ENABLED=false` / `VITE_FEATURE_INTERMEDIATE_DISHES=false`), reservada para fase futura.

- **RF-39 · Consumo de inicio de sesión de salón (`sala.dining_session.iniciada`)**
  - **Descripción:** Crea de forma idempotente la orden inicial `CREATED` vinculando `dining_session_id` y `responsible_waiter_id` (`meseroId`).

- **RF-40 · Proyección y actualización de catálogo en Redis por eventos**
  - **Descripción:** `CatalogConsumer` procesa eventos de Menú (`menu.catalogo.*`, `menu.platillo.*`, `menu.oferta.*`, `menu.source.*`) e Inventario (`inventory.recipe.*`), protegiendo contra mensajes obsoletos mediante `updated_at` y `menu_version`.

- **RF-41 · Reconciliación de catálogo al arranque / periódica**
  - **Descripción:** Cuando se configura `CATALOG_SYNC_URL`, `CatalogReconciliationWorker` sincroniza el catálogo desde Menú al arranque y opcionalmente en el intervalo configurado (`CATALOG_RECONCILIATION_INTERVAL_SECONDS`).

---

# 2. Requisitos No Funcionales (RNF)

- **RNF-01 · Atomicidad transaccional (*Transactional Outbox*):** Todo cambio de estado con evento de integración se persiste en la misma transacción ACID junto con la fila `outbox`.
- **RNF-02 · Idempotencia (*At-Least-Once*):** Los consumidores registran `(order_id, event_id)` en `processed_events` y los eventos deterministas emplean UUIDv5 por recurso y tipo.
- **RNF-03 · Seguridad, PBAC/RBAC y aislamiento de errores:** Autenticación por gateway headers o JWT (`exp`, rechazo de `alg: none`), evaluación PBAC vía `AccessPolicy`, sobres de error estandarizados sin trazas internas y WebSockets con cierre `4401`/`4403`.
- **RNF-04 · Comunicación en tiempo real bidireccional:** Canales WebSocket segregados (`/ws/kds`, `/ws/waiter`), respaldo de reconexión exponencial con invalidación completa de caché y seguimiento monotónico de versiones por orden.
- **RNF-05 · Tolerancia a fallos de Menú:** La validación de disponibilidad en creación/edición opera contra la vista local en Redis sin llamadas síncronas bloqueantes al servicio de Menú.
- **RNF-06 · Rendimiento y latencia (k6):** Respuesta API `p95 < 500 ms` con tasa de error `< 1 %` bajo carga concurrente y entrega WebSocket `p95 < 1 s`.
- **RNF-07 · Ergonomía táctil y Bump Bar en KDS:** Botones táctiles primarios `>= 64 px`, debouncing de 400 ms en eventos pointer/click y atajos completos por teclado industrial.
- **RNF-08 · Modularidad dual en Module Federation:** Construcción independiente de los remotos `ordenesApp` (`5177`, `dist/`) y `cocinaApp` (`5179`, `dist-cocina/`) sobre React `^18.3.1` con aislamiento CSS `.orders-kds-root`.
- **RNF-09 · Control de concurrencia optimista:** Versionamiento en `orders` y `order_items` (`version` / `If-Match`) rechazando escrituras obsoletas.
- **RNF-10 · Mantenibilidad y calidad de código:** Cobertura unitaria mínima del 85 % configurada en `pyproject.toml`, tipado estricto (`mypy --strict`, `tsc`) y reglas de linting/calidad (`ruff`, `eslint`, SonarCloud).
- **RNF-11 · Accesibilidad WCAG 2.1 AA:** Contraste adecuado, soporte de teclado, foco visible, regiones `aria-live` y codificación multimodal (color + texto/icono).
- **RNF-12 · Observabilidad distribuida:** Logs estructurados con propagación de `correlation_id` entre HTTP, Outbox, Broker y Workers.
- **RNF-13 · Validación estricta de contratos:** Validación contra esquemas JSON Schema Draft-07 (`contracts/events/*.json`), Pydantic (`extra="forbid"` en entradas) y esquemas Zod en frontend.
