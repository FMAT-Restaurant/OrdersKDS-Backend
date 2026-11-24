# ADR-0019: Alcance de pruebas de aceptación BDD y cobertura de escenarios AT-08, AT-09, AT-11 y AT-12 (TASK-37)

- **Estado:** Aceptado
- **Fecha:** 2026-11-18
- **Requisitos relacionados:** RF-01 a RF-15, RF-16, RF-17, RF-18, RF-20 a RF-35, RF-36 a RF-38, RF-39 a RF-41, RNF-01 a RNF-04, RNF-07 (TASK-26, TASK-37)

## Contexto

En el marco del plan de Verificación y Validación (V&V) para el sistema de Órdenes y Cocina (`orders-backend`), el catálogo de pruebas de aceptación (AT-01 a AT-12, definido en `docs/VyV_OrdenesKDS.md` §9.1) establece especificaciones ejecutables en lenguaje Gherkin/BDD respaldadas por pruebas automatizadas con `pytest-bdd`.

Con el ajuste del alcance para la entrega consolidada en Sprint 6 (S6) y el cierre de proyecto en 7 sprints, la issue espejo de BDD en `orders-frontend` fue cerrada para evitar duplicaciones innecesarias, centralizando la documentación viva de reglas de negocio en `orders-backend/tests/bdd/`.

En este contexto, la suite BDD de `orders-backend` implementa y ejecuta en CI los escenarios centrales del ciclo de vida y reglas de negocio del servicio:
- **AT-01:** Ciclo de vida completo (`order_lifecycle.feature` / `test_order_lifecycle_bdd.py`).
- **AT-02:** Cancelación de comanda (`cancellation.feature` / `test_cancellation_bdd.py`).
- **AT-03:** Saga de inventario y reservas (`inventory_saga.feature` / `test_inventory_saga_bdd.py`).
- **AT-04:** Anulación administrativa con cobro (`admin_void.feature` / `test_admin_void_bdd.py`).
- **AT-05:** Anulación por error de cocina sin cobro (`kitchen_error_void.feature` / `test_kitchen_error_bdd.py`).
- **AT-06:** Gestión y ordenamiento de cola KDS (`kds_queue.feature` / `test_kds_queue_bdd.py`).
- **AT-07:** Rehacer platillo y restauración de notas (`redo_dish.feature` / `test_redo_dish_bdd.py`).
- **AT-10:** Solicitud de cuenta y cobro con precios congelados (`bill_request.feature` / `test_bill_request_bdd.py`).

Se requirió formalizar mediante este Architecture Decision Record (ADR) la justificación técnica de cómo quedan cubiertos o descartados los escenarios restantes (AT-08, AT-09, AT-11 y AT-12) en la pirámide de pruebas del microservicio.

## Decisión

Se determina la siguiente distribución de cobertura y tratamiento para los escenarios AT-08, AT-09, AT-11 y AT-12:

### 1. AT-08: Transferencia de mesa (`table_transfer.feature`, RF-16)
- **Tratamiento:** Cubierto al 100 % en pruebas unitarias y de contrato de API REST.
- **Justificación técnica:** La transferencia de comanda entre mesas (`PATCH /orders/{orderId}/transfer`) es una operación administrativa de reasignación de ubicación (`table_number`). No modifica el estado de la comanda en la máquina de estados (`OrderStatus`), no interactúa con la saga de Inventario ni altera la cola de cocina del KDS. Su comportamiento, validaciones de mesa ocupada/activa y control de acceso PBAC están cubiertos rigurosamente en:
  - `tests/contract/test_rest_contracts.py` (CT-08).
  - `tests/unit/api/test_orders_transfer_api.py` (`test_transfer_order_*`).
- **Conclusión:** No se requiere duplicar un archivo `.feature` adicional en la suite BDD para este endpoint puntual.

### 2. AT-09: Platillo no disponible (`unavailable_dish.feature`, RF-17, RF-40, RF-41)
- **Tratamiento:** Cubierto al 100 % en pruebas de contrato de eventos, reconciliación e integración de caché Redis.
- **Justificación técnica:** La regla de disponibilidad congela el catálogo en Redis (`CatalogRepository`) ante eventos consumidos desde Menú e Inventario (`menu.platillo.precio_actualizado`, `menu.catalogo.actualizado`, `inventory.recipe.availability-changed`, `inventory.source.availability.updated`). Cuando un platillo no está disponible, el backend responde inmediatamente en el borde con `422 Unprocessable Entity` (`DISH_UNAVAILABLE`), evitando llamadas a la base de datos o transiciones de comanda. Esta lógica está cubierta en:
  - `tests/unit/test_catalog.py`, `tests/unit/api/test_orders_items_api.py` y `tests/unit/test_catalog_reconciliation.py` (validación de borde y excepciones `DishUnavailableError`).
  - `tests/contract/test_consumed_event_contracts.py` (CT-24).
  - `tests/integration/test_it13_catalog_projection.py` (sincronización y consistencia eventual).
- **Conclusión:** La validación de disponibilidad es una salvaguarda de borde reactiva a eventos asíncronos externos, verificada integralmente sin necesidad de un escenario BDD redundante.

### 3. AT-11: Platillos intermedios del chef (`intermediate_dishes.feature`, RF-36 a RF-38)
- **Tratamiento:** Descartado formalmente como **NO-GO** para el horizonte del proyecto.
- **Justificación técnica:** Los requisitos RF-36 a RF-38 fueron clasificados como opcionales en el ERS v4. Conforme a las decisiones del equipo (ADR-0011, TASK-27 y variable de entorno en pipeline `INTERMEDIATE_DISHES_ENABLED=false` en `.github/workflows/ci.yml`), el registro de lotes de preparaciones intermedias fue excluido para asegurar el cierre de los 7 sprints. La regla de gobierno establece que código y pruebas viven y mueren juntos ("code and tests live or die together").
- **Conclusión:** Al no existir endpoints de negocio activos para platillos intermedios, AT-11 no se implementa en BDD ni en producción.

### 4. AT-12: Actualizaciones en tiempo real y reconexión (`realtime_updates.feature`, RF-18, RNF-04)
- **Tratamiento:** Cubierto en pruebas de integración de WebSockets y pruebas E2E de Playwright en frontend.
- **Justificación técnica:** La reactividad en vivo de las pantallas KDS y del mesero frente a eventos de estado y la reconexión resiliente con backoff ante desconexión de red son comportamientos dependientes del ciclo de vida del cliente navegador y de la capa de transporte WebSockets. En el backend se garantiza la emisión y suscripción correcta mediante:
  - `tests/contract/test_websocket_contracts.py` (CT-01).
  - `tests/integration/test_websocket_channels_and_auth_matrix.py` (IT-17, canales PBAC y difusión de eventos) y pruebas unitarias en `tests/unit/test_realtime_router.py` y `tests/unit/test_stage3_realtime_bridge.py`.
  - En frontend, las pruebas E2E de Playwright (`ST-07`, `ST-13`, TASK-25) verifican la renderización dinámica en el DOM y la reconexión automática en navegadores reales.
- **Conclusión:** Simular el ciclo de renderizado del navegador o la pérdida de sockets TCP en pruebas BDD backend basadas en `TestClient(app)` generaría pruebas frágiles o falsos positivos. Su verificación pertenece al nivel E2E y a las pruebas de integración del Hub.

## Consecuencias

- **Trazabilidad completa:** La matriz de requisitos y la suite BDD reflejan con total precisión el alcance real del backend.
- **Documentación viva sin redundancia:** Los 8 pares BDD (`.feature` + `test_*_bdd.py`) cubren el 100 % de los flujos de negocio críticos end-to-end de `orders-backend`.
- **Ejecución eficiente en CI:** Las pruebas se ejecutan de manera determinística y rápida en el job `api-and-contract`, generando artefactos legibles XML y HTML autogenerados (`reports/bdd-report.html`) sin redacción manual de informes.
