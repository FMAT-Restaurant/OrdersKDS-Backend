# DEF-E5-010 — Validación Estricta de Catálogo en Redis, IDs Duales y Enriquecimiento KDS (`INT-04`)

**Módulo contraparte:** Menú (`Menu-Backend` y `Menu-Frontend`)  
**Responsable contraparte:** Líder Técnico de Menú  
**Referencia de auditoría:** Auditoría Equipo 6 — `DEF-E5-009`, `DEF-E5-010` (`CP-023`), `DEF-E5-013` (`CP-032`) / `TASK-45` + `INT-04`  
**Estado en `orders-backend`:** Implementado y adaptado en `INT-04` (`CatalogRepository` + `CatalogConsumer` + `CatalogReconciliationWorker`)

---

## 1. Contexto y Comportamiento Actual en `orders-backend`

A raíz de los hallazgos `DEF-E5-009`, `DEF-E5-010`, `DEF-E5-013` y la integración `INT-04`, `orders-backend` ejecuta el siguiente flujo sobre Redis (`catalog:platillo:{id}`):

1. **Validación estricta de existencia y disponibilidad (`DEF-E5-010` / `CP-023`):**
   - En `PATCH /api/v1/orders/{orderId}/items` y `PATCH /api/v1/orders/{orderId}/items/{itemId}/redo`, `orders-backend` exige que tanto `dishId`/`sourceId` como `offerId` (si es distinto) **existan en Redis y tengan `available = true`**.
   - Si el hash de Redis no contiene el identificador o está marcado `available = false`, responde **`422 Unprocessable Entity` con `error.code = "DISH_UNAVAILABLE"`**.
2. **Precio autoritativo desde Redis (`DEF-E5-009`):**
   - Al agregar ítems, `orders-backend` consulta `catalog_prices.get(offerId) or catalog_prices.get(dishId)` en Redis.
   - Si existe precio autoritativo en catálogo (`price > 0`), **ignora el `unitPrice` enviado por el cliente** y congela el precio del catálogo en BD y en eventos hacia Pagos.
3. **Estación y tiempo de preparación en KDS (`DEF-E5-013` / `CP-032` + `INT-04`):**
   - `GET /api/v1/kds/queue?station=...` lee `station` y `preparationTimeText` directamente desde `CatalogRepository` en Redis (clave `dishId` con fallback `offerId`), sin mocks estáticos en producción.
   - `CatalogConsumer.infer_station_from_menu_item` resuelve `station`/`estacion` explícitos o, en su defecto, infiere la estación (`parrilla`, `barra`, `fria`, `general`) desde `composition.slots[].station|course|name` o `categoryName`.
4. **Adaptación a Menú v1 y propagación fuente → oferta (`INT-04`):**
   - `CatalogReconciliationWorker` soporta de forma nativa respuestas paginadas anidadas de `Menu-Backend` (`GET /api/v1/menu/catalog` con `meta.totalPages`, consulta complementaria a `/catalog/categories` y `/offers/{offerId}`), así como resúmenes en `/menu/offers`.
   - Expande tanto la **oferta comercial** (`offerId`, guardando `_source_ids`) como sus **fuentes físicas** (`recipeId`, `inventoryItemId`, `optionId` activos en `composition.slots[].options[]`, guardando `_linked_offers`).
   - Autenticación hacia Menú configurada vía `CATALOG_SYNC_URL` + `CATALOG_SYNC_TOKEN` (o JWT de servicio efímero firmado con `JWT_SECRET` y permisos `menu:catalog:read`, `menu:offers:read`, `menu:categories:read`).
   - `CatalogConsumer` proyecta eventos asíncronos de fuentes y ofertas (`menu.source.*`, `menu.offer.*`, `menu.item.*`, `menu.catalogo.actualizado`, además de eventos de disponibilidad desde Inventario), propaga cambios de disponibilidad de fuente física hacia sus ofertas vinculadas (`propagate_source_availability`) y `InboundConsumerWorker` soporta fallback de encabezado `X-Event-Type` cuando `routing_key` viene vacío.

---

## 2. Brechas Restantes con `Menu-Backend` y `Menu-Frontend`

Con la adaptación `INT-04` en `orders-backend`, la brecha ya **no** requiere un endpoint plano exclusivo ni `sourceId` en raíz en `GET /api/v1/menu/catalog`. Los puntos pendientes por confirmar/alinear con Menú son:

### A. Campo `preparationTimeText` y estación explícita
- `GET /api/v1/menu/catalog` y `/offers/{offerId}` en `Menu-Backend` actualmente **no exponen `preparationTimeText` (`tiempoPreparacion`)** ni un campo `station` explícito por oferta/slot; `orders-backend` infiere la estación por palabras clave de `course`/`name`/`categoryName` y deja `preparationTimeText = null` si Menú no lo incluye.

### B. Emisión en vivo de eventos RabbitMQ desde `Menu-Backend`
- Verificar en ambiente integrado que `Menu-Backend` publique en `menu.topic` (o `ordenes.topic`) los eventos `menu.source.created|updated|deleted` y/o `menu.offer.*` con `occurredOn` / `updatedAt` (e Idealmente `X-Event-Type` o `routing_key` estándar) para mantener Redis sincronizado en tiempo real entre reconciliaciones.

### C. Envío de IDs duales desde `Menu-Frontend` / `Orders-Frontend`
- Al llamar `PATCH /api/v1/orders/{orderId}/items`, el cliente debe enviar `offerId` (ID de la oferta comercial) y `dishId`/`sourceId` consistentes con las claves proyectadas en Redis (`recipeId` / `inventoryItemId` / `optionId` de la opción seleccionada, u `offerId` cuando aplique), para que:
  1. `validate_dish_available` encuentre ambos IDs disponibles en Redis y no responda `422 DISH_UNAVAILABLE`.
  2. `orders-backend` envíe el `sourceId`/`plateId` físico correcto a Inventario en `ordenes.orden.creada`.

---

## 3. Contratos Soportados Actualmente por `orders-backend`

### 3.1 Reconciliación REST (`CATALOG_SYNC_URL`)
`CatalogReconciliationWorker` acepta ambos formatos:
- **Formato real de `Menu-Backend`:** `GET /api/v1/menu/catalog` (paginado con `data[]` y `meta.totalPages`), complementado automáticamente con `GET /api/v1/menu/catalog/categories` y `GET /api/v1/menu/offers/{offerId}`.
- **Formato plano legado/alternativo:** `{ "items": [{ "offerId": "...", "sourceId": "...", "basePrice": 165.00, "isAvailable": true, "station": "parrilla", "preparationTimeText": "12 min" }] }`.

### 3.2 Eventos RabbitMQ consumidos (`menu.topic` / `ordenes.topic` con `menu.#`)
```json
{
  "eventId": "33333333-4444-4555-8666-777777777777",
  "sourceId": "RECIPE-OR-INVENTORY-UUID",
  "offerId": "OFFER-UUID",
  "basePrice": 165.00,
  "isAvailable": true,
  "station": "parrilla",
  "tiempoPreparacion": "12 min",
  "occurredOn": "2026-11-25T18:00:00Z"
}
```
> También soporta los eventos individuales de fuente de `Menu-Backend`: `menu.source.created`, `menu.source.updated` y `menu.source.deleted` (propagando automáticamente disponibilidad/agotamiento a las ofertas vinculadas).

---

## 4. Checklist de Acuerdos a Cerrar con el Líder de Menú

- [ ] Confirmar URL de despliegue para `CATALOG_SYNC_URL` (p. ej. `http://menu-backend:8080/api/v1/menu/catalog`), `CATALOG_RECONCILIATION_INTERVAL_SECONDS` y esquema de token/permisos (`menu:catalog:read`, `menu:offers:read`, `menu:categories:read`).
- [ ] Confirmar si `Menu-Backend` agregará `preparationTimeText` (`tiempoPreparacion`) y `station` explícito en `/offers/{offerId}` o si se mantiene la inferencia por `course`/`categoryName` acordando los nombres canónicos (`parrilla`, `barra`, `fria`, `general`).
- [ ] Validar en pruebas E2E la publicación en RabbitMQ (`menu.topic`) de `menu.source.created|updated|deleted` y cambios de disponibilidad de ofertas.
- [ ] Verificar con `Menu-Frontend` / `Orders-Frontend` que al añadir ítems envíen `offerId` y `dishId`/`sourceId` alineados con la composición de cada oferta.
