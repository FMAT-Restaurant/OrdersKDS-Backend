# DEF-E5-010 — Validación Estricta de Catálogo en Redis, IDs Duales y Enriquecimiento KDS

**Módulo contraparte:** Menú (`Menu-Backend` y `Menu-Frontend`)  
**Responsable contraparte:** Líder Técnico de Menú  
**Referencia de auditoría:** Auditoría Equipo 6 — `DEF-E5-009`, `DEF-E5-010` (`CP-023`), `DEF-E5-013` (`CP-032`) / `TASK-45`  
**Estado en `orders-backend`:** Implementado y activo (`CatalogRepository` en Redis + `CatalogConsumer` + `CatalogSyncWorker`)

---

## 1. Contexto y Comportamiento Actual en `orders-backend`

A raíz de los hallazgos `DEF-E5-009`, `DEF-E5-010` y `DEF-E5-013`, `orders-backend` endureció tres reglas críticas que dependen directamente de los datos sincronizados desde Menú hacia la caché Redis (`orders:catalog`):

1. **Validación estricta de existencia y disponibilidad (`DEF-E5-010` / `CP-023`):**
   - En `PATCH /api/v1/orders/{orderId}/items` y `PATCH /api/v1/orders/{orderId}/items/{itemId}/redo`, `orders-backend` exige que tanto `dishId`/`sourceId` como `offerId` (si es distinto) **existan en Redis y tengan `available = true`**.
   - Si el hash de Redis no contiene el identificador o está marcado `available = false`, la petición responde **`422 Unprocessable Entity` con `error.code = "DISH_UNAVAILABLE"`** (ya no permite pasar platillos desconocidos ni usa mocks en producción).
2. **Precio autoritativo desde Redis (`DEF-E5-009`):**
   - Al agregar ítems, `orders-backend` consulta `catalog_prices.get(offerId) or catalog_prices.get(dishId)` en Redis.
   - Si existe precio autoritativo en catálogo (`price > 0`), **ignora el `unitPrice` enviado por el cliente** y congela el precio del catálogo en BD y en eventos hacia Pagos.
3. **Estación y tiempo de preparación en KDS (`DEF-E5-013` / `CP-032`):**
   - `GET /api/v1/kds/queue?station=...` lee `station` y `preparationTimeText` directamente de `CatalogRepository` en Redis (clave `dishId` con fallback `offerId`). El mock estático de estaciones dejó de usarse en producción.

---

## 2. Brechas Detectadas entre `orders-backend` y `Menu-Backend` / `Menu-Frontend`

Al inspeccionar `Menu-Backend` y `Menu-Frontend`, se detectaron las siguientes diferencias de contrato:

### A. Mensajería RabbitMQ (`Menu-Backend/docs/messaging.md`)
- `Menu-Backend` documenta únicamente los eventos `menu.source.created`, `menu.source.updated` y `menu.source.deleted` sobre el exchange `menu.topic` con payload reducido:
  ```json
  { "sourceId": "...", "name": "...", "items": [{ "id": "...", "quantity": 1 }] }
  ```
- **Faltan campos esenciales en esos eventos (o en un evento de catálogo):** `offerId`, `basePrice` / `price`, `isAvailable` / `available`, `station` (estación de cocina) y `preparationTimeText` (tiempo estimado).

### B. Endpoint REST de sincronización inicial (`CatalogController.java`)
- `CatalogSyncWorker` en `orders-backend` intenta sincronizar catálogo inicial desde REST.
- Actualmente `Menu-Backend` expone `GET /api/v1/menu/catalog` con estructura anidada por producto/marca y ofertas:
  ```json
  {
    "data": [
      {
        "id": "brand-or-product-id",
        "brandName": "...",
        "isAvailable": true,
        "offers": [
          {
            "id": "offer-id",
            "presentationTag": "...",
            "basePrice": 165.00,
            "isAvailable": true,
            "composition": { "slots": [...] }
          }
        ]
      }
    ],
    "meta": { ... }
  }
  ```
- **Faltantes en el JSON REST:** no expone `sourceId` (el ID físico que necesita Inventario para reserva FEFO), ni `station` ni `preparationTimeText` (que necesita Cocina/KDS), y no existe un endpoint plano `/menu/catalogo/completo`.

### C. Integración con `Menu-Frontend` / `Orders-Frontend`
- `orders-backend` soporta identificadores duales (`dishId` / `sourceId` para el insumo/receta física de Inventario + `offerId` para la oferta comercial/precio), y ahora valida ambos en Redis. Si el frontend envía solo `offerId` sin `sourceId`, o envía un `sourceId` que nunca fue proyectado a Redis, `orders-backend` rechazará la orden con `422 DISH_UNAVAILABLE`.

---

## 3. Riesgo en Integración Real (End-to-End)

Sin alinear este contrato antes de pruebas integrales:
1. Toda creación/edición de ítems vía `PATCH /orders/{id}/items` fallará con **`422 DISH_UNAVAILABLE`** porque el Redis de Órdenes no recibirá las claves de `offerId` y `sourceId`.
2. Aunque se precargue un ID parcialmente, faltarán `basePrice` (precio autoritativo), `station` (filtro por estación en KDS `/kds/queue?station=...`) y `preparationTimeText`.
3. Inventario necesita el `sourceId` físico en `ordenes.pedido.creado` para reservar stock correctamente.

---

## 4. Contrato Propuesto para Alinear con Menú

### Opción recomendada: enriquecer evento de catálogo + endpoint REST alineado

#### 4.1 Evento RabbitMQ (exchange `menu.topic`)
Publicar en `menu.catalogo.actualizado` (o incluir en los eventos de catálogo/oferta de `Menu-Backend`):

```json
{
  "eventId": "33333333-4444-4555-8666-777777777777",
  "eventType": "menu.catalogo.actualizado",
  "sourceId": "PLATE-BURGER-01",
  "offerId": "OFFER-BURGER-01",
  "name": "Hamburguesa Clásica",
  "basePrice": 165.00,
  "isAvailable": true,
  "station": "parrilla",
  "preparationTimeText": "12 min",
  "timestamp": "2026-11-25T18:00:00Z"
}
```

> Nota: `orders-backend` (`CatalogConsumer`) ya soporta también formato por lotes (`items` / `offers` / `platillos`) e indexa tanto por `sourceId`/`plateId` como por `offerId`.

#### 4.2 Snapshot inicial REST (`GET /api/v1/menu/catalog` o `/menu/catalogo/completo`)
Incluir por cada oferta visible o registro de catálogo:
- `sourceId` (UUID/ID físico vinculado con Inventario)
- `offerId` (UUID/ID de la oferta comercial)
- `basePrice` (decimal positivo)
- `isAvailable` (boolean)
- `station` (string, ej. `"parrilla"`, `"cocina_caliente"`, `"barra"`, `"postres"`)
- `preparationTimeText` (string, ej. `"12 min"`)

---

## 5. Checklist de Acuerdos a Cerrar con el Líder de Menú

- [ ] Definir el routing key exacto que emitirá `Menu-Backend` (`menu.catalogo.actualizado` vs. enriquecer `menu.source.*` / `menu.offer.*`).
- [ ] Confirmar inclusión de `sourceId` (para Inventario), `offerId`, `basePrice`, `isAvailable`, `station` y `preparationTimeText` tanto en el evento RabbitMQ como en el endpoint REST de catálogo.
- [ ] Validar el formato exacto de `GET /api/v1/menu/catalog` (o ruta dedicada para sync interno) para ajustar el parser de `CatalogSyncWorker` en `orders-backend` si permanece anidado.
- [ ] Confirmar con `Menu-Frontend` y `Orders-Frontend` que al agregar ítems a una orden se envíen `dishId` / `sourceId` y `offerId` coherentes con el catálogo publicado.
- [ ] Acordar el set canónico de nombres de estación (`station`) entre Menú y Cocina (KDS).
