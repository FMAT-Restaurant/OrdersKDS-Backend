# Suite de Rendimiento y Punto de Ruptura (k6) — TASK-24

Suite modular en Grafana k6 para validar **RNF-06** en `orders-backend`, dimensionada con **mesetas operativas de 300 VUs** y **picos máximos de 600 VUs**, alineada con FastAPI + SQLAlchemy `asyncpg` + Redis + RabbitMQ.

## Estructura

```text
tests/k6/
├── .gitignore                 # Ignora out/*.json, out/*.html, out/*.csv
├── Dockerfile                 # Imagen reproducible basada en grafana/k6:2.3.0 (USER 12345)
├── README.md                  # Guía de ejecución local, Docker y CI
├── config/
│   ├── env.js                 # URLs HTTP/WS (directo :8000 o Kong :8082/:8086) y variables
│   └── options.js             # Perfiles PT-01..08, mesetas 300 VUs, picos 600 VUs y thresholds
├── lib/
│   ├── auth.js                # SharedArray WAITERS/KITCHEN_USERS + JWT dev dinámico o headers Kong
│   ├── http.js                # Wrappers REST con URL Tag Grouping y responseType selectivo
│   ├── metrics.js             # Rate/Trend/Counter custom (5xx, kds_read, order_write, ws, stock_race)
│   ├── setup.js               # Catálogo PLATE-* / OFFER-* en SharedArray y descubrimiento KDS
│   ├── summary.js             # handleSummary autónomo (JSON + HTML + consola) sin red externa
│   ├── workloads.js           # Hash determinista (sin Math.random) y flujos reutilizables
│   └── ws.js                  # Cliente moderno k6/websockets (subprotocol ['bearer', token])
├── out/
│   └── .gitkeep
└── scenarios/
    ├── smoke.js               # PT-08: Gate CI (10 VUs · 30 s)
    ├── load.js                # PT-01 / PT-02: Carga nominal (0 -> 50 VUs · 10 min)
    ├── stress.js              # PT-03: Estrés escalonado hasta meseta 300 VUs y pico 600 VUs
    ├── spike.js               # Picos abruptos 300 <-> 600 VUs
    ├── soak.js                # PT-06: Resistencia (75 VUs · 45 min)
    ├── websocket-fanout.js    # PT-05: 20 WS KDS + 30 WS Mesero + 8 mutaciones/s
    └── stock-race.js          # PT-07: Carrera concurrente de confirmación (20 VUs)
```

## Matriz de escenarios (`PT-01..08`)

| ID | Archivo | Executor k6 | Perfil / VUs | Thresholds clave |
|---|---|---|---|---|
| **PT-08** | `scenarios/smoke.js` | `constant-vus` | 10 VUs · 30 s | `p(95) < 100 ms`, `http_req_failed < 0.1 %`, `checks > 99.9 %` |
| **PT-01 / 02** | `scenarios/load.js` | `ramping-vus` | 0 → 50 VUs · 10 min | `p(95) < 200 ms`, `p(99) < 500 ms`, `http_req_failed < 1 %`, `5xx < 0.1 %` |
| **PT-03** | `scenarios/stress.js` | `ramping-vus` | 50 → 150 → **300 VUs (5 m)** → 450 → **600 VUs (3 m)** → **300 VUs (2 m)** | `p(95) < 2000 ms`, `http_req_failed < 1 %`, `abortOnFail` si `5xx >= 10 %` |
| **Spike** | `scenarios/spike.js` | `ramping-vus` | **300 VUs** → salto 15 s a **600 VUs** (×2) → recuperación **300 VUs** | `5xx < 2 %`, `http_req_failed < 5 %`, `p(95) < 1500 ms` |
| **PT-06** | `scenarios/soak.js` | `constant-vus` | 75 VUs · 45 min | `http_req_failed < 0.5 %`, `p(95) < 250 ms`, `p(99) < 600 ms`, `5xx < 0.1 %` |
| **PT-05** | `scenarios/websocket-fanout.js` | `constant-vus` + `constant-arrival-rate` | 20 WS KDS + 30 WS Mesero + 8 req/s | `ws_delivery_duration p(95) < 500 ms`, `ws_version_regressions == 0` |
| **PT-07** | `scenarios/stock-race.js` | `per-vu-iterations` | 20 VUs sincronizados | `stock_race_pending_timeout == 0`, `http_req_failed < 1 %` |

## Preparación del backend para mesetas de 300 VUs y picos de 600 VUs

Antes de ejecutar `stress.js` o `spike.js`, evita medir un falso límite de 1 proceso / 15 conexiones:

1. **Escalar workers Uvicorn y mantener Redis Pub/Sub activo:**
   ```bash
   export REDIS_URL="redis://127.0.0.1:6379/0"
   export DB_POOL_SIZE="25"
   export DB_MAX_OVERFLOW="35"
   export DB_POOL_TIMEOUT="15"
   export DB_POOL_PRE_PING="true"
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
   ```
2. **Sembrar catálogo y órdenes base alineadas con `PLATE-*` / `OFFER-*`:**
   ```bash
   alembic upgrade head
   python scripts/seed_demo.py --reset
   python -m app.workers.run_all
   ```
3. **Ajustar límites de descriptores en Linux / Docker para 600 VUs:**
   ```bash
   ulimit -n 65535
   ```

## Comandos reproducibles

### Ejecución local directa (`k6` CLI)

```bash
# Validar sintaxis y opciones de cada escenario
k6 inspect tests/k6/scenarios/smoke.js

# PT-08: Smoke rápido (gate CI)
k6 run tests/k6/scenarios/smoke.js

# PT-01 / PT-02: Carga nominal 50 VUs
k6 run tests/k6/scenarios/load.js

# PT-03: Estrés escalonado 300 -> 600 VUs
k6 run tests/k6/scenarios/stress.js

# Picos abruptos 300 <-> 600 VUs
k6 run tests/k6/scenarios/spike.js

# PT-05: Fan-out WebSocket con reloj local sincronizado
k6 run tests/k6/scenarios/websocket-fanout.js

# PT-06: Soak 75 VUs · 45 min
k6 run tests/k6/scenarios/soak.js

# PT-07: Carrera de stock concurrente
k6 run -e K6_SCARCE_DISH_ID=PLATE-BURGER-01 -e K6_SCARCE_OFFER_ID=OFFER-BURGER-01 tests/k6/scenarios/stock-race.js
```

### Ejecución vía Kong API Gateway (`:8082` orders / `:8086` kitchen)

```bash
k6 run \
  -e K6_AUTH_MODE=gateway \
  -e K6_ORDERS_BASE_URL=http://127.0.0.1:8082/api/v1/orders \
  -e K6_KITCHEN_BASE_URL=http://127.0.0.1:8086/api/v1/kitchen \
  -e K6_ORDERS_WS_URL=ws://127.0.0.1:8082/api/v1/orders/ws/waiter \
  -e K6_KITCHEN_WS_URL=ws://127.0.0.1:8086/api/v1/kitchen/ws/kds \
  tests/k6/scenarios/smoke.js
```

### Ejecución con Docker (`tests/k6/Dockerfile`)

```bash
docker build -f tests/k6/Dockerfile -t orders-k6:local .

docker run --rm -i --ulimit nofile=65535:65535 \
  --add-host=host.docker.internal:host-gateway \
  -e BASE_URL=http://host.docker.internal:8000 \
  -v "${PWD}/tests/k6/out:/scripts/out" \
  -e K6_SUMMARY_DIR=/scripts/out \
  orders-k6:local run /scripts/scenarios/stress.js
```

## Variables de entorno soportadas (`tests/k6/config/env.js`)

| Variable | Valor por defecto | Uso |
|---|---|---|
| `BASE_URL` | `http://127.0.0.1:8000` | URL HTTP base en ejecución directa |
| `WS_BASE_URL` | Derivado de `BASE_URL` | URL WebSocket base (`ws://` / `wss://`) |
| `K6_ORDERS_BASE_URL` | `${BASE_URL}/api/v1/orders` | Prefijo REST mesero (`:8082` detrás de Kong) |
| `K6_KITCHEN_BASE_URL` | `${BASE_URL}/api/v1/kitchen` | Prefijo REST cocina (`:8086` detrás de Kong) |
| `K6_ORDERS_WS_URL` | `${WS_BASE_URL}/api/v1/orders/ws/waiter` | WebSocket mesero |
| `K6_KITCHEN_WS_URL` | `${WS_BASE_URL}/api/v1/kitchen/ws/kds` | WebSocket KDS |
| `K6_AUTH_MODE` | `jwt` | `jwt` (Bearer dinámico/env) o `gateway` (headers `x-user-*`) |
| `K6_WAITER_TOKEN` / `K6_KITCHEN_TOKEN` / `K6_ADMIN_TOKEN` | `""` | Tokens reales inyectados en staging/producción |
| `K6_DEV_JWT_SIGNING_MATERIAL` | `""` | Firma efímera local opcional para JWTs dev |
| `K6_SUMMARY_DIR` | `tests/k6/out` | Directorio destino de `*-summary.json` y `*-report.html` |
| `K6_SMOKE_VUS` / `K6_SMOKE_DURATION` | `10` / `30s` | Override opcional del smoke test |
| `K6_SOAK_VUS` / `K6_SOAK_DURATION` | `75` / `45m` | Override opcional del soak test |
| `K6_WS_HOLD_MS` | `60000` | Tiempo de retención de cada socket en `PT-05` |
| `K6_SCARCE_DISH_ID` / `K6_SCARCE_OFFER_ID` | `PLATE-BURGER-01` / `OFFER-BURGER-01` | Platillo objetivo de `PT-07` |
