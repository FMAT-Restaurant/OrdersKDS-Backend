# Catálogo de Funcionalidades Implementadas — Órdenes y Cocina (Orders & KDS)

> **Estado:** Sincronizado con el código fuente vigente (`orders-backend` y `orders-frontend`).  
> **Nota:** Para la especificación formal bajo estándar IEEE 830 / ISO/IEC/IEEE 29148 consulte [ERS_IEEE_OrdenesKDS.md](./ERS_IEEE_OrdenesKDS.md).

---

## 1. Resumen de Capacidades Activas

El sistema **Orders & KDS** cubre el ciclo operativo completo desde la apertura de la comanda por el mesero hasta su preparación en cocina y liquidación final en caja, desacoplado mediante mensajería asíncrona, WebSockets y Module Federation dual (`ordenesApp:5177` y `cocinaApp:5179`).

---

## 2. Capacidades por Módulo

### 2.1 Terminal de Mesero (`ordenesApp` / `/api/v1/orders`)
1. **Apertura de comandas:** creación en borrador (`CREATED`) para mesa física, para llevar (`isTakeaway`) o vinculada a sesión de comensales (`diningSessionId`, relación 1:N).
2. **Armado y edición de ítems:** captura de platillos, cantidades, modificadores y notas en estado `CREATED` (sin confirmar) o al modificar una orden `REJECTED` (que reabre automáticamente a `CREATED`). Validación preventiva contra caché local de disponibilidad en Redis y soporte dual de identificadores (`offerId` / `sourceId` / `dishId`) y precios congelados.
3. **Confirmación y Saga de Inventario:** confirmación atómica con bloqueo de fila (`confirmed_at`, evento `ordenes.orden.creada` en Outbox), recibiendo en tiempo real el paso a `RESERVED` o el rechazo desglosado (`REJECTED` con `rejectedItems` y `reservedItems`).
4. **Transferencia de mesa:** reasignación entre mesas con control de concurrencia optimista (`version` / `If-Match`).
5. **Rehechura de platillos (`redo`):** devolución de un ítem entregado (`DELIVERED`) a cocina (`PENDING`) mediante nota justificativa obligatoria, preservando las notas previas de preparación hasta completar la rehechura.
6. **Cancelación de comandas:** cancelación libre (`CANCELLED`, evento `ordenes.orden.cancelada`) permitida en `CREATED`, `RESERVED` o `REJECTED`.
7. **Solicitud de cuenta:** emisión de `ordenes.cuenta.solicitada` (`request-bill`) para órdenes en `DELIVERED` con precios unitarios congelados e identificadores `orderId` y `sessionId`.

### 2.2 Pantalla de Cocina KDS (`cocinaApp` / `/api/v1/kitchen`)
1. **Cola y pestañas operativas:** visualización de comandas agrupadas en *Pendientes* (`RESERVED`), *En preparación* (`IN_PREPARATION`), *Activas*, *Para rehacer*, *Entregadas* (`DELIVERED`) y *Caducados*, ordenadas por `COALESCE(in_preparation_at, stock_reserved_at, created_at)`.
2. **Vigencia de pre-reserva (TTL KDS):** control visual de 15 minutos desde `stock_reserved_at` más una ventana de gracia de 10 minutos antes de trasladar la orden a la pestaña *Caducados*.
3. **Filtrado por estación:** segmentación normalizada por estación culinaria (`parrilla`, `barra`, `fria`, `general`, etc.) tanto en REST como en canales WebSocket (`kds:station:{station}`, `kds:all`).
4. **Inicio de preparación:** transición explícita `RESERVED → IN_PREPARATION` (`start-preparation`) registrando `in_preparation_at` y emitiendo `ordenes.orden.en_preparacion` para consumo FEFO en Inventario.
5. **Despacho por platillo o por orden completa:** marcado individual (`PENDING → READY`) emitiendo `ordenes.platillo.preparado` (con bandera `isRedo` cuando corresponde) y transición automática o masiva a `DELIVERED`.
6. **Operabilidad táctil e industrial:** objetivos táctiles mínimos de 64 px en acciones primarias, ventana de deshacer de **15 segundos** antes de confirmar cambios al backend, supresión anti-doble clic de 400 ms y navegación completa por teclado industrial (*Bump Bar*).
7. **Rechazo físico en cocina y cancelación de ítem:** rechazo por falta física de ingrediente en cocina (`kitchen-reject`, con `availableQuantity` parcial o 0 y bloqueo automático en caché Redis si llega a 0) y cancelación individual de ítems (`ItemStatus.CANCELLED`).
8. **Anulaciones desde cocina / administración:**
   - **Error de cocina (`VOIDED`):** exclusiva de `ADMIN` o `CHEF_MASTER` en `IN_PREPARATION` con nota obligatoria; publica `ordenes.comanda.mermada` sin cobro al cliente.
   - **Anulación administrativa (`WASTED` / `VOIDED`):** exclusiva de `ADMIN` en `IN_PREPARATION` con bandera `chargeCustomer` (`true` por defecto transiciona a `WASTED` y emite conjuntamente `ordenes.comanda.mermada` + `ordenes.cobro.solicitado`; `false` transiciona a `VOIDED` y emite únicamente `ordenes.comanda.mermada` sin cobro).
9. **Retiro automático al pagar:** al consumirse `pagos.pago.completado`, la comanda pasa a `PAID` y desaparece en tiempo real del KDS.

### 2.3 Seguridad, Mensajería y Tiempo Real Transversal
1. **Autorización PBAC + RBAC:** 16 políticas granulares en `AccessPolicy` (`orders:*`, `kitchen:*`) consumiendo encabezados de Kong (`x-user-id`, `x-staff-id`, `x-user-roles`, `x-user-role`, `x-user-permissions`, `x-user-type`) o claims JWT, con soporte para `ADMIN`/`ADMINISTRADOR`, `CHEF_MASTER` (incluyendo `orders:orders:view` en solo lectura), `COCINERO` y `MESERO`.
2. **WebSockets y Redis Pub/Sub:** cierre estandarizado `4401` (no autenticado) y `4403` (rol/permiso denegado), y puente `orders:realtime:events` entre workers asíncronos y API.
3. **Confiabilidad de mensajería:** patrón *Transactional Outbox*, confirmación del broker (`publisher_confirms`), consumidores idempotentes (`processed_events`), DLQ por cola ante mensajes inválidos, vigilancia automática de expiración de Saga (`60s` por defecto) y sincronización dual con Menú/Sala/Inventario/Pagos.
