# Análisis de Integración Auth y Catálogo Oficial de Permisos PBAC (Backend)

> **Módulo:** Órdenes y Cocina (KDS) — `orders-backend`  
> **Destinatario principal:** Equipo de Auth & Seguridad (`Auth-Backend` / `Auth-Frontend`)  
> **Fecha:** 11 de Noviembre de 2026  
> **Estado:** Documentación y Propuesta de Contrato de Integración  

---

## 1. Respuesta al Líder del Equipo de Auth: Catálogo de Permisos PBAC

De acuerdo con las directrices de integración (formato fijo `modulo:recurso:accion`, recursos en inglés plural minúsculas, prefijos asignados en la guía de integración `orders:` y `kitchen:`), a continuación se presenta la lista completa y formal de permisos requeridos por el microservicio de **Órdenes y Cocina (KDS)**.

Cada permiso incluye los 4 datos requeridos:
1. **Formato PBAC:** `modulo:recurso:accion`
2. **Etiqueta UI:** Nombre amigable para el selector de roles en la interfaz administrativa de Auth.
3. **Descripción:** Alcance operativo y funcional de la acción.
4. **Endpoint(s) Asociado(s):** Método HTTP / Protocolo y ruta REST o WebSocket.

---

### Módulo `kitchen` (Pantalla de Cocina / KDS)

| # | Permiso PBAC (`modulo:recurso:accion`) | Etiqueta para UI | Descripción de Negocio | Endpoint(s) Asociado(s) |
|---|---|---|---|---|
| 1 | `kitchen:queues:view` | Ver cola KDS de preparación | Permite visualizar la cola de comandas en cocina ordenadas por tiempo de espera y filtrar por estación de preparación. | `GET /api/v1/orders/kds/queue`<br>`WS /api/v1/orders/ws/kds` |
| 2 | `kitchen:orders:start_preparation` | Iniciar preparación de orden | Pasa una comanda reservada a estado en preparación (`IN_PREPARATION`), consumiendo los insumos correspondientes en inventario. | `PATCH /api/v1/orders/orders/{orderId}/start-preparation`<br>`POST /api/v1/orders/orders/{orderId}/start-preparation` |
| 3 | `kitchen:items:ready` | Marcar platillo como listo | Marca un platillo individual como listo (`READY`), notificando sonoramente y en tiempo real al mesero asignado. | `PATCH /api/v1/orders/orders/{orderId}/items/{itemId}/ready` |
| 4 | `kitchen:orders:ready` | Marcar orden completa lista | Marca todos los platillos y la comanda global en estado entregado/listo (`DELIVERED`) para retiro de cocina hacia la mesa. | `PATCH /api/v1/orders/orders/{orderId}/ready` |
| 5 | `kitchen:items:reject` | Rechazar platillo por falta física | Rechaza un platillo en preparación por falta física de ingredientes en cocina, regresando la orden a `REJECTED` para que el mesero la ajuste. | `PATCH /api/v1/orders/orders/{orderId}/items/{itemId}/kitchen-reject` |
| 6 | `kitchen:items:cancel` | Cancelar platillo desde cocina | Permite al personal de cocina suprimir un platillo específico antes de comenzar su cocción. | `PATCH /api/v1/orders/orders/{orderId}/items/{itemId}/cancel` |
| 7 | `kitchen:orders:void_error` | Anular orden por error de cocina | Anula una comanda en preparación por desperfecto atribuible al chef/cocina (quemado, contaminación) sin cargo al comensal (estado `VOIDED`). | `POST /api/v1/orders/orders/{orderId}/void-kitchen-error` |

---

### Módulo `orders` (Gestión de Pedidos / Comandas / Salón)

| # | Permiso PBAC (`modulo:recurso:accion`) | Etiqueta para UI | Descripción de Negocio | Endpoint(s) Asociado(s) |
|---|---|---|---|---|
| 8 | `orders:orders:view` | Consultar órdenes y comandas | Consultar la lista de pedidos activos, historial de comandas y el detalle completo de un pedido específico. | `GET /api/v1/orders/orders`<br>`GET /api/v1/orders/orders/{orderId}`<br>`WS /api/v1/orders/ws/waiter/{waiterId}` |
| 9 | `orders:orders:create` | Crear nueva comanda | Abrir un nuevo pedido para mesa física o pedido para llevar y capturar los platillos iniciales en borrador (`CREATED`). | `POST /api/v1/orders/orders` |
| 10 | `orders:orders:update` | Modificar comanda en borrador | Agregar platillos, alterar cantidades, notas o modificadores en una comanda antes de enviarla o tras ser rechazada. | `PATCH /api/v1/orders/orders/{orderId}` |
| 11 | `orders:orders:confirm` | Confirmar comanda a cocina | Enviar la comanda formalmente a cocina, desencadenando la reserva automática de insumos en el microservicio de inventario (`RESERVED`). | `POST /api/v1/orders/orders/{orderId}/confirm` |
| 12 | `orders:orders:cancel` | Cancelar comanda por cliente | Cancelar una orden a petición del cliente o por desistimiento en mesa (permitido en `CREATED`, `RESERVED` o `REJECTED`). | `POST /api/v1/orders/orders/{orderId}/cancel` |
| 13 | `orders:orders:void_admin` | Anulación administrativa (Merma) | Anulación especial reservada a administradores para pedidos ya en preparación, registrando pérdida/merma cobrada o justificada (`WASTED`). | `POST /api/v1/orders/orders/{orderId}/void` |
| 14 | `orders:orders:transfer` | Transferir mesa o comensal | Reasignar una comanda en curso a otra mesa física o asociarla a una sesión de comensal distinta. | `POST /api/v1/orders/orders/{orderId}/transfer` |
| 15 | `orders:items:redo` | Solicitar rehacer platillo | Notificar a cocina que un platillo entregado tuvo un problema en mesa y debe cocinarse de nuevo sin cancelar la orden. | `POST /api/v1/orders/orders/{orderId}/items/{itemId}/redo` |
| 16 | `orders:orders:request_bill` | Solicitar cuenta / ticket | Notificar a Caja/Facturación que la mesa ha solicitado la cuenta, bloqueando adiciones a la comanda. | `POST /api/v1/orders/orders/{orderId}/bill-requested` |

---

## 2. Matriz Sugerida de Permisos por Rol en Auth-Backend

Para la configuración del catálogo de roles del sistema en `Auth-Backend` (`SYSTEM_PERMISSIONS_CATALOG`), se recomienda asignar estos permisos por defecto a los roles estándar:

```text
ADMINISTRADOR:
  - '*' (Acceso total automático a todos los permisos)

CHEF_MASTER (Jefe de Cocina):
  - kitchen:queues:view
  - kitchen:orders:start_preparation
  - kitchen:items:ready
  - kitchen:orders:ready
  - kitchen:items:reject
  - kitchen:items:cancel
  - kitchen:orders:void_error
  - orders:orders:view

COCINERO (Línea KDS):
  - kitchen:queues:view
  - kitchen:orders:start_preparation
  - kitchen:items:ready
  - kitchen:orders:ready
  - kitchen:items:reject

MESERO:
  - orders:orders:view
  - orders:orders:create
  - orders:orders:update
  - orders:orders:confirm
  - orders:orders:cancel
  - orders:orders:transfer
  - orders:items:redo
  - orders:orders:request_bill
  - menu:dishes:view
  - sala:tables:view
```

---

## 3. Diagnóstico del Estado Actual de Permisos y Roles en `orders-backend`

### 3.1. Cómo opera actualmente `orders-backend`
Actualmente, el backend de órdenes valida los permisos mediante **RBAC estricto** en `app/api/dependencies/auth.py` y `app/domain/roles.py`:
1. Extrae el JWT del header `Authorization: Bearer <token>`.
2. Lee **únicamente** los claims:
   - `sub` o `user_id` (identificador del usuario).
   - `role` (un string en minúsculas obligatorio).
3. Intenta mapear `Role(raw_role)` contra el enum interno:
   ```python
   class Role(StrEnum):
       WAITER = "waiter"
       KITCHEN = "kitchen"
       CHEF = "chef"
       ADMIN = "admin"
   ```
4. Agrupa las rutas con guardias `require_roles(*GROUP)`:
   - `KITCHEN_ROLES = frozenset({Role.KITCHEN, Role.CHEF, Role.ADMIN})`
   - `WAITER_ROLES = frozenset({Role.WAITER, Role.ADMIN})`
   - `ADMIN_ONLY_ROLES = frozenset({Role.ADMIN})`
   - `VOID_KITCHEN_ERROR_ROLES = frozenset({Role.ADMIN, Role.CHEF})`

### 3.2. Las 3 Discrepancias / Brechas con `Auth-Backend` y el API Gateway

1. **Estructura del Payload JWT emitido por `Auth-Backend`:**
   - En `Auth-Backend` (`auth.service.ts`), el token generado incluye:
     ```json
     {
       "sub": "uuid-del-usuario",
       "staffId": "M000001",
       "email": "mesero@restaurant.com",
       "roles": ["MESERO"],
       "permissions": ["orders:orders:view", "orders:orders:create", ...],
       "mustChangePassword": false
     }
     ```
   - **Problema:** `Auth-Backend` envía `roles: string[]` en **mayúsculas y en plural/array**, mientras que `orders-backend` busca `role` (singular) y en minúsculas en español/inglés (`waiter`, `kitchen`, etc.). Si se valida directamente ese JWT hoy en `orders-backend`, lanzará `UnauthenticatedError: Authentication required` al fallar `Role(raw_role)`.

2. **Inyección de Headers por Kong (Plugin `auth-perimeter` verificado en `API-Gateway`):**
   - El plugin `auth-perimeter` de Kong valida la firma JWT, sanitiza headers no confiables e inyecta aguas abajo exactamente:
     - `X-User-Id`: UUID del usuario (`jwt_claims.sub`).
     - `X-Staff-Id`: Identificador de empleado (`jwt_claims.staffId`).
     - `X-User-Roles`: Roles del usuario separados por comas (plural, ej. `"MESERO"` o `"ADMINISTRADOR,CHEF"`).
     - `X-User-Permissions`: Permisos PBAC separados por comas (plural, ej. `"orders:orders:view,orders:orders:create"`).
   - Bloqueo por contraseña: Kong intercepta automáticamente si `mustChangePassword: true` respondiendo con `403 PASSWORD_CHANGE_REQUIRED`.
   - `orders-backend` actualmente no lee `X-User-Permissions` ni `X-User-Roles` desde headers de Kong, sino que parsea el token Bearer directamente.

3. **Inexistencia de Validación PBAC en Backend:**
   - Si un administrador en `Auth-Frontend` le revoca a un mesero específico el permiso `orders:orders:cancel` o `orders:items:redo`, el backend de órdenes actualmente no se entera porque solo verifica si el rol es `Role.WAITER` o `Role.ADMIN`.

4. **Enrutamiento y `strip_path: false` en Kong:**
   - En `API-Gateway/config/kong.yml`, la ruta para `orders-service` está configurada como `paths: [ ~/api/v1/orders(/|$) ]` con `strip_path: false`.
   - Esto significa que Kong reenvía la petición completa conservando el prefijo `/api/v1/orders/...` hacia `orders-backend`.
   - Actualmente `orders-backend` monta sus rutas directamente en `/orders` y `/kds/queue` sin prefijo `/api/v1`. Para evitar errores 404 al pasar por el gateway, el backend debe montar sus routers bajo `/api/v1` (o un doble montaje `/` y `/api/v1` para retrocompatibilidad en desarrollo local).

---

## 4. Plan de Adaptación Técnica Sugerido para `orders-backend`

Para lograr una integración exitosa con Auth sin romper los tests unitarios existentes ni el modo standalone de desarrollo, se recomienda implementar una **estrategia híbrida no disruptiva** en `app/api/dependencies/auth.py`:

### Paso 1: Normalización Inteligente de Roles
Aceptar tanto los roles en minúsculas de desarrollo (`waiter`, `kitchen`, `chef`, `admin`) como los roles emitidos por `Auth-Backend` (`MESERO`, `COCINERO`, `CHEF_MASTER`, `ADMINISTRADOR`, `ADMIN`):

```python
_ROLE_ALIASES: Final[dict[str, Role]] = {
    "waiter": Role.WAITER,
    "mesero": Role.WAITER,
    "kitchen": Role.KITCHEN,
    "cocinero": Role.KITCHEN,
    "chef": Role.CHEF,
    "chef_master": Role.CHEF,
    "admin": Role.ADMIN,
    "administrador": Role.ADMIN,
}
```

### Paso 2: Extraer Permisos PBAC en `AuthContext`
Ampliar `AuthContext` para soportar la lista de permisos:

```python
class AuthContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    role: Role
    permissions: frozenset[str] = Field(default_factory=frozenset)
    exp: float | None = None

    def has_permission(self, permission: str) -> bool:
        if self.role == Role.ADMIN or "*" in self.permissions:
            return True
        return permission in self.permissions
```

### Paso 3: Soporte de Headers de Kong y Claims de Token
En `_context_from_claims` y `get_auth_context`:
- Si existen los headers inyectados por Kong (`X-User-Id`, `X-User-Roles`, `X-User-Permissions`), utilizarlos directamente (`X-User-Permissions.split(",")` y `X-User-Roles.split(",")`).
- Si no, decodificar el Bearer token leyendo `claims.get("permissions")` (lista) y `claims.get("roles")` o `claims.get("role")`.
- **Compatibilidad con `strip_path: false`:** Asegurar que `app/main.py` monte los routers con prefijo `/api/v1` para recibir el tráfico enrutado por Kong sin generar 404.

### Paso 4: Guardia `require_permissions`
Crear una dependencia `require_permissions("kitchen:orders:start_preparation")` que valide prioritariamente el permiso PBAC y mantenga fallback a los roles permitidos para retrocompatibilidad total.
