# Guía de uso de Docker — Orders & KDS Backend

Documentación básica para el equipo. Explica cómo funciona Docker **en tu máquina local** dentro de este proyecto y qué parte se resolverá en la nube (TASK-38, Sprint 5).

> Documentos relacionados: [QUICKSTART.md](../QUICKSTART.md) (§5–§7: requerimientos, arranque y cliente de BD) · [README.md](../README.md) (instalación completa).

---

## 1. ¿Para qué usamos Docker?

El microservicio necesita tres servicios externos que **no se instalan en tu computadora**; corren dentro de contenedores:

| Servicio | Imagen | Contenedor | Puerto en tu máquina | Para qué sirve |
|---|---|---|---|---|
| PostgreSQL 16 | `postgres:16-alpine` | `ordenes-postgres` | `5432` | Base de datos principal y tabla *outbox* |
| Redis 7 | `redis:7-alpine` | `ordenes-redis` | `6379` | Vista local de catálogo y mesas |
| RabbitMQ 3 | `rabbitmq:3-management-alpine` | `ordenes-rabbitmq` | `5672` (AMQP) y `15672` (panel web) | Broker de eventos |

Ventajas: todos usamos **las mismas versiones**, no se "ensucia" el sistema operativo, no hay choque con otros programas y todo se puede borrar y recrear en segundos.

> **No instales PostgreSQL, Redis ni RabbitMQ directamente en tu sistema.** Solo necesitas Docker Desktop (y, opcionalmente, un cliente visual como pgAdmin o DBeaver).

La API (`uvicorn`) y los *workers* **no** corren en Docker durante el desarrollo: se ejecutan directamente en tu máquina para tener recarga automática (`--reload`).

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

---

## 7. Alcance: local vs. nube

### 7.1 Entorno local (esta guía)

- **Cada integrante tiene sus propios contenedores**, con su propia base de datos. Es intencional: puedes borrar datos o probar migraciones sin afectar a nadie.
- Las credenciales de `docker-compose.yml` son débiles y **solo para desarrollo local**. Nunca se usan en otro ambiente.
- Las pruebas automáticas usan contenedores efímeros (Testcontainers) que se crean y destruyen solos; no comparten datos con tu base local.

### 7.2 Imagen en la nube (CI/CD, TASK-01)

El workflow `.github/workflows/ci.yml` incluye el job `build-and-push`, que construye la imagen de la API y la publica en **GitHub Container Registry** (`ghcr.io`) con las etiquetas `latest` y el SHA del commit. Se ejecuta **solo** cuando hay un *push* a `main`, es decir, al promover una versión `develop → main`. No se hace a mano.

### 7.3 Ambiente compartido en la nube — TASK-38 (Sprint 5)

Lo que no se puede resolver con contenedores locales (que todos usemos **la misma** base de datos, y que otros equipos alcancen nuestra API) está cubierto por la **TASK-38 — Ambiente compartido de integración y staging**, planificada en el **Sprint 5** (27 oct – 2 nov). Incluye:

- Definir y documentar el **hospedaje** y cómo se despliega la imagen `ghcr.io/<org>/ordenes-kds-backend`.
- **PostgreSQL compartido de staging** (`ordenes_staging`), con usuario de la app de permisos mínimos y usuario de solo lectura para clientes visuales, separado de la base de la versión final.
- Conexión al **RabbitMQ y Redis** del ambiente con los parámetros acordados en OP-10, y un `docker-compose.staging.yml` (o equivalente) con variables por ambiente.
- Secretos por ambiente en *GitHub Environments* (`staging`, `production`), nunca en el repositorio.
- Prueba de conectividad (*smoke*): `/health`, un evento publicado y consumido, y conexión de un integrante con su cliente visual.

Mientras esa tarea no esté lista, **trabaja siempre con tus contenedores locales**. Cuando exista, la guía se ampliará con los datos de conexión del ambiente compartido.

| Ambiente | Dónde corre | Quién lo usa |
|---|---|---|
| Local | Tus contenedores (`docker compose`) | Solo tú |
| Pruebas automáticas | Testcontainers efímeros / CI | El código de pruebas |
| Staging compartido | Nube, definido en TASK-38 (S5) | Todo el equipo y otros microservicios |
| Versión final | Imagen publicada desde `main` | El sistema liberado |

### 7.4 Usar contenedores de otros equipos

Cuando exista su imagen publicada en un registro, se puede agregar a un compose local con una línea `image: ghcr.io/<org>/<servicio>:latest`; `docker compose up -d` la descargará. Hasta entonces, las pruebas se hacen contra los *stubs* de contrato (TASK-33).

---

## 8. Resumen rápido

```powershell
# Primera vez / cada día que lo necesites (desde orders-backend)
docker compose up -d postgres redis rabbitmq
docker compose ps
alembic upgrade head

# Apagar al terminar (conserva datos)
docker compose stop

# Empezar de cero (BORRA datos locales)
docker compose down -v
```
