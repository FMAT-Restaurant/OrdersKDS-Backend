# Breakpoint Report — Orders & KDS Backend (`TASK-24` / `RNF-06`)

> **Alcance:** Documento de análisis de capacidad, punto de ruptura (`PT-03`, `SPIKE_300_600`) y criterios de dimensionamiento para `orders-backend`, calibrado para **mesetas operativas de 300 VUs** y **picos máximos de 600 VUs**.

---

## 1. Arquitectura bajo prueba y ruta crítica

1. **Plano HTTP / WebSocket:** `FastAPI` (`app/main.py`) montado directo en `:8000` o detrás de **Kong API Gateway** (`:8082` para `/api/v1/orders/*` + `/ws/waiter`, `:8086` para `/api/v1/kitchen/*` + `/ws/kds`).
2. **Persistencia transaccional:** `SQLAlchemy` + `asyncpg` contra **PostgreSQL** (`app/db/session.py`), con control de concurrencia optimista (`version`), bloqueos `FOR UPDATE` al reemplazar ítems y tabla `outbox`.
3. **Catálogo y tiempo real:** **Redis** para proyección de disponibilidad (`catalog:platillo:<dish_id>`) y **Pub/Sub** multi-worker (`orders:realtime:events` vía `RedisRealtimeBridge`).
4. **Workers asíncronos:** `app.workers.run_all` (Outbox publisher -> RabbitMQ `ordenes.topic`, consumidor de inventario y `SagaTimeoutWatcher`).

---

## 2. Metodología y perfiles ejecutados en k6

| Escenario | Executor | Mesetas / Picos | Duración total | Objetivo |
|---|---|---|---|---|
| **PT-08** (`smoke.js`) | `constant-vus` | 10 VUs | 30 s | Gate continuo en CI (`p(95) < 100 ms`, errores `< 0.1 %`) |
| **PT-01 / 02** (`load.js`) | `ramping-vus` | 0 → 50 VUs | 10 min | Carga nominal 70 % lecturas / 20 % escrituras / 10 % cocina |
| **PT-03** (`stress.js`) | `ramping-vus` | 50 → 150 → **300 (5 m)** → 450 → **600 (3 m)** → **300 (2 m)** | 24 min | Identificar punto de quiebre (`p(95) > 2000 ms` o `http_req_failed > 1 %`) |
| **Spike** (`spike.js`) | `ramping-vus` | **300 VUs** ↔ **600 VUs** (rampas de 15 s) | 18.5 min | Validar recuperación tras ráfagas sin `5xx` sostenidos |
| **PT-06** (`soak.js`) | `constant-vus` | 75 VUs | 45 min | Estabilidad de memoria RSS, fuga de conexiones y lag de Outbox |
| **PT-05** (`websocket-fanout.js`) | `constant-vus` + `constant-arrival-rate` | 20 WS KDS + 30 WS Mesero + 8 req/s | 2 min | Fan-out `< 500 ms` (`p(95)`), 0 regresiones de `version` |
| **PT-07** (`stock-race.js`) | `per-vu-iterations` | 20 VUs sincronizados | <= 90 s | Cero sobreventa al resolver reservas concurrentes |

---

## 3. Estado de ejecución y mediciones verificadas

> [!NOTE]
> Cualquier corrida en entorno local sin infraestructura staging completa debe distinguirse explícitamente de la corrida de certificación en staging. Las cifras observadas localmente se registran en la tabla de verificación; las métricas de meseta 300 / pico 600 VUs en staging permanecen listadas para su captura tras el despliegue del clúster.

| Verificación | Alcance | Estado | Observaciones / Evidencia |
|---|---|---|---|
| Validación estática y de opciones (`k6 inspect`) | 7 escenarios (`smoke`, `load`, `stress`, `spike`, `soak`, `websocket-fanout`, `stock-race`) | Aprobado (k6 v2.3.0) | 7/7 escenarios validados con 0 errores de sintaxis, opciones e importaciones |
| Suite contractual Python (`tests/unit/test_k6_suite.py`) | Estructura, permisos PBAC, catálogo `PLATE-*`, rutas FastAPI, reglas Sonar/Gitleaks | Aprobado (11/11) | Cobertura de `app/db/session.py` y contratos HTTP/WS/PBAC verificados |
| Gate CI (`PT-08` `k6-smoke`) | 10 VUs · 30 s contra FastAPI + PostgreSQL 16 + Redis 7 | Automatizado en `.github/workflows/ci.yml` | Exporta `smoke-summary.json` y `smoke-report.html` como artefacto |
| Corrida `PT-03` (300/600 VUs) en Staging | `uvicorn --workers 4` + PostgreSQL + Redis + RabbitMQ + Workers | Pendiente de ejecución en ambiente Staging | Redis no activo localmente (`6379` cerrado); registrar VU exacto de inflexión al correr en Staging |

### Plantilla de captura en Staging (300 VUs / 600 VUs)

| Escalón VUs | RPS medido | `p(50)` HTTP | `p(95)` HTTP | `p(99)` HTTP | Error `http_req_failed` | Errores `5xx` | Conexiones PG activas | CPU Workers |
|---|---|---|---|---|---|---|---|---|
| 50 VUs (`load`) | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* |
| 150 VUs (`stress`) | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* |
| **300 VUs (meseta)** | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* |
| 450 VUs (`stress`) | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* |
| **600 VUs (pico)** | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* |
| **300 VUs (recuperación)** | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* | *Por capturar* |

---

## 4. Análisis de cuellos de botella identificados en la arquitectura actual

1. **Concurrencia de proceso ASGI (`Dockerfile`):**
   - El contenedor ejecuta por defecto `uvicorn app.main:app --host 0.0.0.0 --port 8000` (1 solo worker). Un único *event loop* de Python se satura por CPU (serialización JSON/Pydantic + lectura síncrona/compresión) antes de alcanzar 300–600 VUs.
   - **Remediación aplicada/recomendada:** Escalar a `--workers 4` (o `2 * CPU`) con `REDIS_URL` activa para conservar broadcast WebSocket transversal entre workers mediante `RedisRealtimeBridge`.
2. **Pool de conexiones PostgreSQL (`app/db/session.py`):**
   - Previamente usaba exclusivamente los valores por omisión de SQLAlchemy (`pool_size=5`, `max_overflow=10` = 15 conexiones máximo por worker), propenso a `QueuePool limit reached` / `TimeoutError` bajo picos de 600 VUs en escrituras (`POST /orders`, `PATCH /items`, `POST /confirm`).
   - **Remediación implementada en `TASK-24`:** `app/db/session.py` ahora parametriza opcionalmente `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT` y `DB_POOL_PRE_PING` mediante variables de entorno con validación temprana, manteniendo retrocompatibilidad total cuando no están definidas.
3. **Lectura de cola KDS (`GET /kds/queue`) y filtrado en memoria:**
   - `app/api/routers/kds.py` filtra estaciones y consulta información de catálogo por ítem; bajo cientos de órdenes activas conviene monitorear tiempo de consulta en PostgreSQL (`orders(status, created_at)`) y cardinalidad de ítems activos.
4. **Cola Outbox y RabbitMQ:**
   - Ráfagas sostenidas de `POST /orders/{orderId}/confirm` generan filas en `outbox`; si el batch (`OUTBOX_BATCH_SIZE=100` cada `OUTBOX_POLL_INTERVAL_SECONDS=1`) queda atrás a 600 VUs, aumentará el tiempo de transición `CREATED -> RESERVED` en `PT-07` (`stock_race_pending_timeout`).

---

## 5. Comandos de diagnóstico durante pruebas de estrés

```bash
# (a) Conexiones activas/esperando en PostgreSQL
psql "$DATABASE_URL" -c "SELECT state, wait_event_type, count(*) FROM pg_stat_activity GROUP BY state, wait_event_type;"

# (b) Uso CPU / Memoria de contenedores
docker stats --no-stream

# (c) Suscriptores Redis Pub/Sub y latencia
redis-cli PUBSUB NUMSUB orders:realtime:events
redis-cli --latency-history -i 5

# (d) Backlog de Outbox sin publicar y colas RabbitMQ
psql "$DATABASE_URL" -c "SELECT count(*) AS pending_outbox FROM outbox WHERE published_at IS NULL;"
rabbitmqctl list_queues name messages_ready messages_unacknowledged
```

---

## 6. Recomendaciones de dimensionamiento (Meseta 300 VUs / Pico 600 VUs)

| Componente | Parámetro | Valor recomendado para 300/600 VUs | Impacto |
|---|---|---|---|
| **Uvicorn** | `--workers` | `4` (ajustar a núcleos vCPU del host) | Distribuye parsing Pydantic y conexiones TLS/HTTP |
| **SQLAlchemy Pool** | `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `25` / `35` por worker (`DB_POOL_TIMEOUT=15`, `DB_POOL_PRE_PING=true`) | Evita timeout de pool (máx. ~240 conexiones con `PG max_connections >= 300`) |
| **PostgreSQL** | `max_connections` | `>= 300` | Absorbe 4 workers + `worker_run_all` + overhead administrativo |
| **Outbox Worker** | `OUTBOX_POLL_INTERVAL_SECONDS` / `OUTBOX_BATCH_SIZE` | `0.25` / `250` | Reduce lag de confirmación Saga bajo picos de escritura |
| **Generador k6** | `--ulimit nofile` | `65535:65535` | Evita agotamiento de file descriptors y sockets efímeros a 600 VUs |
