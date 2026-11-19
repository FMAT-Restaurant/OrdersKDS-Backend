# Hallazgos y Pendientes de Integración INT-06 para API-Gateway y Auth-Backend

> **Módulo emisor:** Órdenes y Cocina (KDS) — `orders-backend`  
> **Equipos destinatarios:** Equipo de `API-Gateway` (Kong) y Equipo de `Auth-Backend`  
> **Fecha:** 18 de Noviembre de 2026  
> **Referencia:** Tarea `INT-06` (`Planificacion_Sprints_Orders_KDSV4.md`), `ADR-0011`

Este documento registra únicamente los ajustes pendientes que corresponden a repositorios externos (`API-Gateway` y `Auth-Backend`) detectados durante la auditoría de `INT-06`. Las correcciones del lado de `orders-backend` y `orders-frontend` ya fueron aplicadas en sus respectivos repositorios.

---

## 1. Gestión de `JWT_SECRET` (Política de Seguridad y ADR-0011)

* El secreto `JWT_SECRET` compartido por el líder de Auth **debe configurarse exclusivamente en el archivo `.env` local/staging de `Auth-Backend` y `API-Gateway` (Kong)** (sin versionarse nunca en Git, `.env.example`, `docker-compose.yml` ni documentación).
* De acuerdo con [ADR-0011](./adr/0011-jwt-claims-without-local-signature-verification.md) y `DEVELOPMENT_GUIDELINES §5.7`, **`orders-backend` no almacena ni valida `JWT_SECRET`**: la verificación criptográfica de firma HS256 pertenece únicamente al perímetro de Kong (`API-Gateway`), y `orders-backend` valida expiración (`exp`), algoritmo (`alg != none`), encabezados de perímetro y políticas PBAC.
* Ningún frontend (`orders-frontend`, `Auth-Frontend`) debe recibir ni contener `JWT_SECRET`.

---

## 2. Pendientes para el Equipo de `API-Gateway` (Kong)

### 2.1. Bloqueante: Falta la ruta `/api/v1/kitchen` en `config/kong.yml`
* **Estado observado (`API-Gateway` commit `f16a8b8`):**
  * `config/kong.yml` y `docker-compose.yml` solo exponen `orders-service` con `paths: [~/api/v1/orders(/|$)]` apuntando a `${ORDERS_SERVICE_URL}` (`http://orders-backend:8082`).
  * No existe ruta para `~/api/v1/kitchen(/|$)` (ni variable `${KITCHEN_SERVICE_URL}` si se separa puerto `8086`).
* **Impacto:** Toda petición de `cocinaApp` (`5179`) hacia `http://localhost:8000/api/v1/kitchen/...` recibe `404` de Kong.
* **Acción requerida en `API-Gateway`:**
  1. Declarar en `config/kong.yml` la ruta `kitchen-routes` con `paths: [~/api/v1/kitchen(/|$)]` y `strip_path: false`, con plugins `jwt` + `auth-perimeter`, apuntando a `${KITCHEN_SERVICE_URL}` (por defecto `${ORDERS_SERVICE_URL}` / `http://orders-backend:8082`, o `http://orders-backend:8086` si se despliega instancia separada).
  2. Agregar `KITCHEN_SERVICE_URL` en `docker-compose.yml` y `.env.example`, y cubrir el enrutamiento en `tests/test_gateway.py`.
  3. Confirmación: `orders-backend` (`app/main.py`) ya tiene montados `/api/v1/kitchen/kds/queue`, `/api/v1/kitchen/orders/*`, `/api/v1/kitchen/ws/kds`, `/api/v1/kitchen/ws/waiter` y `/api/v1/kitchen/health`.

### 2.2. Bloqueante: Autenticación de WebSockets (`/ws/kds` y `/ws/waiter`) a través de Kong
* **Estado observado (`config/kong.yml`):**
  * El plugin `jwt` en las rutas protegidas configura `header_names: [Authorization]` y `uri_param_names: []`.
* **Impacto:** La API estándar `new WebSocket(url, protocols)` de los navegadores no permite enviar encabezados HTTP `Authorization` personalizados. `orders-frontend` envía el JWT tanto por query param (`?token=<jwt>`) como por subprotocolo (`Sec-WebSocket-Protocol: bearer, <jwt>`), y `orders-backend` acepta ambos. Sin embargo, Kong rechazará el handshake con `401` antes de llegar al upstream.
* **Acción requerida en `API-Gateway`:**
  * Configurar rutas dedicadas para los endpoints WebSocket (`~/api/v1/orders/ws/(waiter|kds)$` y `~/api/v1/kitchen/ws/(waiter|kds)$`) que permitan validar el JWT en el handshake mediante alguna de estas estrategias:
    * **Opción A (más rápida en Kong 3.4):** Habilitar `uri_param_names: [token]` únicamente en las rutas WebSocket (manteniendo `uri_param_names: []` en todas las rutas REST) y omitir el token en logs de acceso.
    * **Opción B (recomendada por seguridad, sin token en URL):** Extraer el JWT desde `Sec-WebSocket-Protocol` (`bearer, <jwt>`) en un plugin Lua previo al plugin `jwt` (inyectando `Authorization: Bearer <jwt>` internamente para que Kong valide la firma HS256).

### 2.3. Seguridad: Sanitizar `X-User-Type` y `X-User-Role` en `plugins/auth-perimeter/handler.lua`
* **Estado observado (`plugins/auth-perimeter/handler.lua:30-33`):**
  * `AuthPerimeterHandler:access` limpia únicamente `X-User-Id`, `X-Staff-Id`, `X-User-Roles` y `X-User-Permissions`.
  * No limpia `X-User-Type` ni `X-User-Role` (singular).
* **Impacto:** Un cliente autenticado con rol operativo (ej. `MESERO`) podría enviar manualmente `X-User-Type: ADMINISTRADOR` y Kong lo reenviaría intacto a cualquier microservicio que lea dicho header.
* **Mitigación ya aplicada en `orders-backend`:** `orders-backend` ahora ignora `x-user-type` si existen roles o permisos explícitos (`x-user-roles` / `x-user-permissions`).
* **Acción requerida en `API-Gateway`:**
  * Agregar en `plugins/auth-perimeter/handler.lua` al inicio de `access()`:
    ```lua
    kong.service.request.clear_header("X-User-Type")
    kong.service.request.clear_header("X-User-Role")
    ```
  * Agregar prueba de regresión en `tests/test_gateway.py` verificando que ambos headers forjados se eliminan tanto en rutas públicas como protegidas.

### 2.4. Configuración de `CORS_ORIGINS` para los 6 módulos
* Verificar en el `.env` de `API-Gateway` que `CORS_ORIGINS` incluya los orígenes activos del Shell (`Auth-Frontend`, p. ej. `http://localhost:5175` o el puerto configurado) y, en pruebas directas, `http://localhost:5177` (`ordenesApp`) y `http://localhost:5179` (`cocinaApp`).

---

## 3. Estado y Notas para el Equipo de `Auth-Backend`

### 3.1. Validación PBAC (Compatible 1:1)
* Confirmado contra `Auth-Backend` commit `86a7e8d` (`src/roles/catalog/permissions-catalog.ts` y `src/roles/roles.service.ts`):
  * Los 9 permisos `orders:*` y 7 permisos `kitchen:*` coinciden exactamente con `orders-backend/app/domain/access.py`.
  * Los cambios recientes en `MESERO` (`sala:*`, `menu:entries:view`) y `CHEF_MASTER` (`orders:orders:view`) son totalmente compatibles con `orders-backend`.
  * `ADMINISTRADOR` con `roles: ['ADMINISTRADOR']` y `permissions: ['*']` resuelve correctamente a `Role.ADMIN`.

### 3.2. Nota informativa sobre `userType` (no bloqueante)
* `Auth-Backend` devuelve `userType` (`'ADMIN' | 'STAFF'`) en `GET /api/v1/auth/me`, pero no en el objeto `user` de `POST /api/v1/auth/login` ni en el payload del access token JWT (`generateTokens`).
* Esto **no bloquea** a `orders-backend` porque `roles` (`ADMINISTRADOR`) y `permissions` (`*`) ya permiten identificar al administrador. Si en el futuro `Auth-Backend` decide incluir `userType` en el JWT y `API-Gateway` lo reenvía en `X-User-Type`, `orders-backend` ya acepta tanto `ADMIN` como `ADMINISTRADOR` como respaldo cuando no hay roles/permisos explícitos.
