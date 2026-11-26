# Guía de uso de Docker — Orders & KDS Backend

Documentación básica para el equipo. Explica cómo funciona Docker **en tu máquina local** dentro de este proyecto y qué parte se resolverá en la nube (TASK-38, Sprint 5).

> Documentos relacionados: [QUICKSTART.md](../QUICKSTART.md) (§5–§7: requerimientos, arranque y cliente de BD) · [README.md](../README.md) (instalación completa).

---

## 1. ¿Para qué usamos Docker?

El microservicio necesita tres servicios externos que **no se instalan en tu computadora** y, opcionalmente en modo full-Docker (`docker-compose.ci.yml`), tres contenedores de aplicación construidos desde el `Dockerfile`:

| Servicio Compose | Imagen | Contenedor | Puerto en host | Para qué sirve |
|---|---|---|---|---|
| `postgres` | `postgres:16-alpine` | `ordenes-postgres` | `${POSTGRES_PORT:-5432}:5432` | Base de datos principal y tabla *outbox* |
| `redis` | `redis:7-alpine` | `ordenes-redis` | `6379:6379` | Vista local de catálogo y puente WebSocket |
| `rabbitmq` | `rabbitmq:3-management-alpine` | `ordenes-rabbitmq` | `5672:5672` (AMQP), `15672:15672` (UI) | Broker de eventos |
| `api` *(Modo B)* | `orders-backend-api` | `ordenes-api-ci` | `8000:8000` | API FastAPI REST + WebSockets |
| `workers` *(Modo B)* | `orders-backend-workers` | `ordenes-workers-ci` | — | Outbox, Saga Watcher y Consumer RabbitMQ |
| `stubs` *(Modo B)* | `orders-backend-stubs` | `ordenes-stubs-ci` | — | Stubs de Inventario y Pagos en RabbitMQ |

Ventajas: todos usamos **las mismas versiones**, no se "ensucia" el sistema operativo, no hay choque con otros programas y todo se puede borrar y recrear en segundos.

> **No instales PostgreSQL, Redis ni RabbitMQ directamente en tu sistema.** Solo necesitas Docker Desktop (y, si ya existe un PostgreSQL local en `5432`, define `POSTGRES_PORT=5433` en `.env`).

Durante el desarrollo normal (Modo A), la API (`uvicorn`) y los *workers* corren directamente en tu `.venv` local para tener recarga automática (`--reload`); si quieres correr los 6 contenedores juntos (Modo B), consulta §7.4 y [QUICKSTART.md §8](../QUICKSTART.md).

---

## 2. Conceptos básicos

| Concepto | Qué es | Analogía |
|---|---|---|
| **Imagen** | Paquete de solo lectura con el software ya instalado (ej. `postgres:16-alpine`) | La receta/instalador |
| **Contenedor** | Una imagen en ejecución | El plato ya preparado |
| **Volumen** | Carpeta persistente donde el contenedor guarda sus datos | El disco duro del contenedor |
| **Puerto (`5432:5432`)** | `puerto-en-tu-máquina:puerto-del-contenedor` | Un túnel hacia el contenedor |
| **`docker-compose.yml`** | Archivo que describe todos los contenedores del proyecto | La lista completa de recetas |

Gracias al mapeo de puertos, desde tu máquina todo se ve como si estuviera instalado normalmente: te conectas a `localhost:5432`.

Los volúmenes (`postgres_data`, `redis_data`, `rabbitmq_data`) hacen que **los datos sobrevivan** aunque apagues o elimines los contenedores. Solo se borran con `docker compose down -v`.

---

## 3. Antes de empezar

1. Instala **Docker Desktop** (en Windows requiere WSL 2). Ver [QUICKSTART §5](../QUICKSTART.md).
2. Abre Docker Desktop y espera a que indique **Engine running**. Sin esto, cualquier comando `docker` fallará.
3. Verifica la instalación:

```powershell
docker --version
docker compose version
docker run --rm hello-world
```

---

## 4. Uso diario

**Todos los comandos se ejecutan en la raíz del repositorio `orders-backend`** (donde está `docker-compose.yml`).

| Objetivo | Comando |
|---|---|
| Encender la infraestructura (en segundo plano) | `docker compose up -d postgres redis rabbitmq` |
| Ver el estado y la salud de los contenedores | `docker compose ps` |
| Ver logs en vivo de un servicio | `docker compose logs -f rabbitmq` |
| Apagar conservando los datos | `docker compose stop` |
| Eliminar contenedores conservando los datos | `docker compose down` |
| **Reiniciar desde cero** (borra BD, caché y colas) | `docker compose down -v` y luego `docker compose up -d` + `alembic upgrade head` |
| Aplicar migraciones tras un `git pull` | `alembic upgrade head` |

### ¿Debo encenderlo cada vez que abro la terminal?

**No.** La opción `-d` (*detached*) deja los contenedores corriendo en segundo plano aunque cierres la terminal o el editor. Solo necesitas volver a ejecutar `docker compose up -d ...` si:

- los apagaste tú con `stop` o `down`, o
- Docker Desktop se cerró o reiniciaste el equipo y los contenedores no se levantaron solos.

Ejecutar `up -d` cuando ya están corriendo es seguro: Docker no hace cambios.

### Verificar que todo está sano

```powershell
docker compose ps
# postgres, redis y rabbitmq deben mostrar "Up ... (healthy)"

docker exec ordenes-redis redis-cli ping    # responde PONG
```

RabbitMQ puede tardar hasta ~30 s en estar *healthy* la primera vez.

---

## 5. Monitorear y administrar los contenedores

### Con Docker Desktop (visual)

En la pestaña **Containers** aparecen `ordenes-postgres`, `ordenes-redis` y `ordenes-rabbitmq`. Desde ahí puedes iniciar, pausar o eliminar cada uno, y abrir sus **logs** con un clic.

### Con la terminal

`docker compose ps`, `docker compose logs -f <servicio>` y `docker stats` (consumo de CPU y memoria).

### Paneles de cada servicio

| Servicio | Cómo inspeccionarlo |
|---|---|
| PostgreSQL | Cliente visual (pgAdmin o DBeaver) en `localhost:5432`, usuario/contraseña/base `ordenes`. Detalle en [QUICKSTART §7](../QUICKSTART.md) |
| RabbitMQ | Panel web `http://localhost:15672` (usuario `ordenes`, contraseña `ordenes`): exchanges, colas y mensajes |
| Redis | `docker exec -it ordenes-redis redis-cli` |

### Conectar pgAdmin al PostgreSQL de Docker

*Servers → Register → Server…* y en **Connection** usa: Host `localhost`, Port `5432`, Maintenance database `ordenes`, Username `ordenes`, Password `ordenes`. El contenedor debe estar encendido.

---

## 6. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `cannot connect to the Docker daemon` | Docker Desktop no está abierto | Abrirlo y esperar *Engine running* |
| `port is already allocated` | Otro programa usa 5432, 6379, 5672 o 15672 | Cerrar ese programa o cambiar el lado **izquierdo** del puerto en `docker-compose.yml` y ajustar `.env` |
| `connection refused` en `localhost:5432` | El contenedor aún no está *healthy* | `docker compose ps` y esperar |
| `password authentication failed for user "ordenes"` | `.env` distinto al compose o volumen creado con otras credenciales | Alinear `.env` o ejecutar `docker compose down -v` |
| Las pruebas de integración no arrancan | Testcontainers necesita Docker activo | Abrir Docker Desktop |
| `401 Unauthorized` (`jwt rejected` / `ValueError` o `InvalidSignatureError`) | Falta `JWT_SECRET` en `.env` o el token fue firmado con otro secreto | Definir `JWT_SECRET` (≥32 bytes) en `.env`, recrear `api` y `workers` y regenerar el token con `python scripts/dev_token.py --role admin --user-id 00000000-0000-0000-0000-000000000001` |
| `500` o bloqueo aparente de CORS en el navegador (`Connect call failed ('127.0.0.1', 5433)` o `6379`) | Se levantó `api`/`workers` sin sobrescribir `$env:DATABASE_URL` y `$env:REDIS_URL` a `postgres:5432` y `redis:6379` en la sesión de PowerShell | Definir `$env:DATABASE_URL` y `$env:REDIS_URL` hacia la red interna de Docker y recrear con `docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --no-deps api workers` |

---

## 7. Alcance: local vs. nube

### 7.1 Entorno local (esta guía)

- **Cada integrante tiene sus propios contenedores**, con su propia base de datos. Es intencional: puedes borrar datos o probar migraciones sin afectar a nadie.
- Las credenciales de `docker-compose.yml` son débiles y **solo para desarrollo local**. Nunca se usan en otro ambiente.
- Las pruebas automáticas usan contenedores efímeros (Testcontainers) que se crean y destruyen solos; no comparten datos con tu base local.

### 7.2 Imagen en la nube (CI/CD, TASK-01)

El workflow `.github/workflows/ci.yml` incluye el job `build-and-push`, que construye la imagen de la API y la publica en **GitHub Container Registry** (`ghcr.io`) con la etiqueta de rama y el SHA del commit. Un *push* a `develop` publica el candidato de staging; un *push* a `main` publica el release.

### 7.3 Ambiente compartido en la nube — TASK-38 (ya implementado)

El ambiente compartido se hospeda en una VM Linux administrada por el equipo,
con Docker Engine y firewall restringido. La configuración reproducible,
los roles de PostgreSQL, el procedimiento de migraciones, el túnel de
DBeaver/pgAdmin y el smoke están en [STAGING.md](STAGING.md).

La imagen de staging es `ghcr.io/fmat-restaurant/orderskds-backend:develop`.
El archivo [`docker-compose.staging.yml`](../docker-compose.staging.yml) usa únicamente variables del archivo
privado `.env.staging` (plantilla en [`.env.staging.example`](../.env.staging.example)); nunca contiene credenciales.

| Ambiente | Dónde corre | Quién lo usa |
|---|---|---|
| Local | Tus contenedores (`docker compose`) | Solo tú |
| Pruebas automáticas | Testcontainers efímeros / CI | El código de pruebas |
| Staging compartido | VM Linux con Docker, definida en [STAGING.md](STAGING.md) | Todo el equipo y otros microservicios |
| Versión final | Imagen publicada desde `main` | El sistema liberado |

### 7.4 Imágenes de la aplicación (`Dockerfile`) y stack completo de 6 contenedores (`docker-compose.ci.yml`)

Si deseas levantar **todo el backend dentro de contenedores Docker** (API + *workers* + *stubs* + infraestructura, sin usar `uvicorn` local):

1. El [`Dockerfile`](../Dockerfile) parte de `python:3.12-slim`, instala [`requirements/runtime.txt`](../requirements/runtime.txt) (que ya incluye `websockets==15.0.1` para `/ws/kds` y `/ws/waiter`) y copia `app/`, `alembic.ini`, `contracts/events/` y `ops/staging/staging-smoke.py` (no copia `scripts/`).
2. [`docker-compose.ci.yml`](../docker-compose.ci.yml) extiende `docker-compose.yml` creando 3 imágenes (`orders-backend-api`, `orders-backend-workers`, `orders-backend-stubs`) y levantando los **6 contenedores**: `ordenes-postgres`, `ordenes-redis`, `ordenes-rabbitmq`, `ordenes-api-ci`, `ordenes-workers-ci` y `ordenes-stubs-ci`.
3. El `healthcheck` de `api` usa Python estándar (`python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health')"`), por lo que **no requiere `curl`** dentro de `python:3.12-slim` para pasar a `healthy` y desbloquear el arranque de `workers`.

```powershell
# Asegura que .env tenga JWT_SECRET (32+ bytes) antes de levantar la API
# Levantar los 6 contenedores con variables internas de la red Docker
$env:DATABASE_URL="postgresql+asyncpg://ordenes:ordenes@postgres:5432/ordenes"
$env:REDIS_URL="redis://redis:6379/0"
$env:RABBITMQ_URL="amqp://ordenes:ordenes@rabbitmq:5672/"
$env:RABBITMQ_VHOST="/"
$env:SAGA_TIMEOUT_SECONDS="60"
# Si tienes PostgreSQL local en 5432:
# $env:POSTGRES_PORT="5433"

docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.ci.yml ps

# Aplicar migraciones dentro del contenedor de la API
docker exec ordenes-api-ci alembic upgrade head

# Cargar datos demo (UUIDv4 aleatorios) desde tu .venv en el host (Dockerfile no copia scripts/)
.\.venv\Scripts\Activate.ps1
python scripts/seed_demo.py --reset
```

Para probar flujos con **RabbitMQ local** (automáticos con `stubs` o publicando mensajes JSON manuales desde `http://localhost:15672`), sigue [QUICKSTART.md §8 — Paso 6](../QUICKSTART.md).

#### Variante contra RabbitMQ real/externo (ej. CloudAMQP, sin `rabbitmq` ni `stubs` locales)

```powershell
# 1. Verifica que tu .env ya tenga RABBITMQ_URL, RABBITMQ_VHOST, RABBITMQ_EXCHANGE y JWT_SECRET
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d postgres redis

# 2. Sobrescribe SOLO los hosts internos de BD y Redis en esta sesión de PowerShell
$env:DATABASE_URL="postgresql+asyncpg://ordenes:ordenes@postgres:5432/ordenes"
$env:REDIS_URL="redis://redis:6379/0"

# 3. Levanta api y workers sin iniciar rabbitmq ni stubs locales, y corre migraciones
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build --no-deps api workers
docker exec ordenes-api-ci alembic upgrade head
```

> **Recordatorio de cierre de sprint:** antes de cerrar cualquier sprint o entregar a Auth/Staging, realiza siempre una verificación completa corriendo sobre los contenedores Docker finales (`orders-backend` y `orders-frontend`).

### 7.5 Usar contenedores de otros equipos

Cuando exista su imagen publicada en un registro, se puede agregar a un compose local con una línea `image: ghcr.io/<org>/<servicio>:latest`; `docker compose up -d` la descargará. Hasta entonces, las pruebas se hacen contra `ordenes-stubs-ci` / los *stubs* de contrato (TASK-33).

---

## 8. Resumen rápido

```powershell
# Opción A: Infraestructura en Docker + API/workers locales (recomendado para desarrollo)
docker compose up -d postgres redis rabbitmq
docker compose ps
alembic upgrade head
python scripts/seed_demo.py --reset

# Opción B: Todos los 6 contenedores (ver §7.4 y QUICKSTART.md §8)
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build
docker exec ordenes-api-ci alembic upgrade head
python scripts/seed_demo.py --reset

# Opción C: Backend en Docker hacia RabbitMQ real (CloudAMQP en .env, sin stubs)
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d postgres redis
$env:DATABASE_URL="postgresql+asyncpg://ordenes:ordenes@postgres:5432/ordenes"; $env:REDIS_URL="redis://redis:6379/0"
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build --no-deps api workers
docker exec ordenes-api-ci alembic upgrade head

# Apagar al terminar (conserva datos)
docker compose -f docker-compose.yml -f docker-compose.ci.yml stop

# Empezar de cero (BORRA datos locales)
docker compose -f docker-compose.yml -f docker-compose.ci.yml down -v
```
