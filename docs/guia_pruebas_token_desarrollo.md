# Guía de pruebas con token JWT firmado en desarrollo

## Propósito y contexto de seguridad

- En el contenedor de `orders-frontend` (`http://localhost:5177`, Nginx), abrir la app de forma aislada muestra la pantalla **`Sesión requerida`** (`DEF-E5-018`) cuando no existe una sesión activa.
- En `orders-backend` (`http://localhost:8000`), toda petición HTTP o WebSocket verifica criptográficamente la firma `HS256` y la expiración (`exp`) del JWT (`DEF-E5-002`), rechazando tokens sin firma válida con `401 Unauthorized`.
- En el entorno integrado con el Shell de autenticación (`Auth-Frontend`, PR #26 `feat/token-en-memoria`), el portal mantiene la sesión solo en memoria y entrega `token` y `currentUser` por **props** al microfrontend federado (sin depender de `localStorage`).
- El procedimiento de esta guía es únicamente para **pruebas locales aisladas** del frontend y backend de Órdenes/KDS sin levantar todo el stack de Auth.

---

## Prerrequisitos

1. Contenedores de `orders-backend` activos en `http://localhost:8000` (por ejemplo `ordenes-api-ci`, `ordenes-workers-ci`, `ordenes-rabbitmq`, `ordenes-postgres`, `ordenes-redis`) levantados con una clave `JWT_SECRET` de al menos 32 bytes.
2. Contenedor de `orders-frontend` activo en `http://localhost:5177` (o `cocinaApp` en `http://localhost:5179`).

---

## Paso 1 — Generar un JWT firmado real (en `orders-backend`)

Ejecuta `scripts/dev_token.py` usando exactamente el **mismo `JWT_SECRET`** con el que levantaste `ordenes-api-ci` (puedes cambiar `--role` por `admin`, `chef`, `kitchen` o `waiter`, y ajustar `--ttl` en segundos si lo necesitas):

```powershell
.\.venv\Scripts\python.exe scripts/dev_token.py --role admin --secret "<TU_JWT_SECRET_LOCAL>"
```

> Si ya tienes la variable cargada en tu sesión de PowerShell o en `.env` de `orders-backend`, también puedes pasar `--secret "$env:JWT_SECRET"` u omitir `--secret` para que el script lea `JWT_SECRET` desde `.env`.

Copia únicamente el token JWT resultante (el texto después de `Bearer `, con formato `xxxxx.yyyyy.zzzzz`).

---

## Paso 2 — Cargar la sesión en el navegador (`http://localhost:5177`)

1. Abre `http://localhost:5177` en el navegador.
2. Abre **DevTools (`F12`) → Console**.
3. Pega el siguiente bloque reemplazando `<TU_TOKEN_FIRMADO>` por el token generado en el Paso 1 (sin el prefijo `Bearer `) y presiona `Enter`:

```javascript
localStorage.setItem('fmat_restaurant_auth_session', JSON.stringify({
  accessToken: '<TU_TOKEN_FIRMADO>',
  user: {
    id: '00000000-0000-0000-0000-000000000001',
    email: 'admin@fmat.local',
    displayName: 'Admin Local',
    roles: ['admin'],
    permissions: ['*']
  }
}));
location.reload();
```

### Valores sugeridos en `user` según el `--role` generado

El backend autoriza usando el JWT firmado (`--role` del Paso 1), mientras que el objeto `user` controla qué vistas y botones muestra inicialmente la interfaz:

| `--role` en Paso 1 | `roles` sugerido en `user` | `permissions` sugerido en `user` |
| --- | --- | --- |
| `admin` | `['admin']` | `['*']` |
| `chef` | `['chef']` | `['kitchen:queues:view', 'kitchen:orders:start_preparation', 'kitchen:items:ready', 'kitchen:orders:ready', 'kitchen:items:reject', 'kitchen:items:cancel', 'kitchen:orders:void_error', 'orders:orders:view']` |
| `kitchen` | `['kitchen']` | `['kitchen:queues:view', 'kitchen:orders:start_preparation', 'kitchen:items:ready', 'kitchen:orders:ready', 'kitchen:items:reject']` |
| `waiter` | `['waiter']` | `['orders:orders:view', 'orders:orders:create', 'orders:orders:update', 'orders:orders:confirm', 'orders:orders:cancel', 'orders:orders:transfer', 'orders:items:redo', 'orders:orders:request_bill']` |

*(También puedes usar los nombres en español que emite Auth: `'ADMINISTRADOR'`, `'CHEF_MASTER'`, `'COCINERO'`, `'MESERO'`.)*

---

## Extras útiles durante la prueba

### Probar modo oscuro / contraste (`DEF-E5-020`)

Ejecuta en **DevTools → Console**:

```javascript
document.documentElement.setAttribute('data-theme', 'dark');   // activar modo oscuro
document.documentElement.removeAttribute('data-theme');        // volver a modo claro
```

### Cerrar sesión (volver a verificar el bloqueo `DEF-E5-018`)

Ejecuta en **DevTools → Console**:

```javascript
localStorage.removeItem('fmat_restaurant_auth_session');
location.reload();
```

### Nota sobre `pnpm dev` (`DevSandboxApp`)

En desarrollo con `pnpm dev`, los tokens autogenerados en el navegador por `generateLocalDevToken()` llevan firma ficticia (`.devsignature`), por lo que `orders-backend` los rechaza con `401` (`DEF-E5-002`). Usar un token firmado con `scripts/dev_token.py` sobre el contenedor `:5177` (o iniciar sesión desde el flujo completo con Auth) permite probar todo el flujo real contra backend, WebSockets y RabbitMQ.

### Prueba de integración completa con Auth

Para probar el comportamiento exacto de producción sin usar `localStorage`:

1. Levanta `Auth-Backend`, `API-Gateway` y `orders-backend` compartiendo exactamente el mismo `JWT_SECRET`.
2. Levanta `Auth-Frontend` (`:3000`) apuntando al remoto de `orders-frontend` (`:5177` / `:5179`).
3. Inicia sesión desde `Auth-Frontend` y abre el módulo de **Órdenes** o **Cocina**.

---

## Advertencia de seguridad

- **Nunca** uses el `JWT_SECRET` de staging o producción para generar tokens locales.
- **Nunca** agregues secretos reales, tokens generados ni archivos `.env.local` al repositorio.
