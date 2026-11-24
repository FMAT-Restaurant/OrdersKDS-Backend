# QUICKSTART — Orders & KDS Backend

- [English Version](#english-version)
- [Versión en Español](#versión-en-español)

---

## English Version

Quick reference for the CI/CD standard required by the course.
Full setup instructions are in [README.md](README.md); the contributing
workflow is in [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md). A basic guide
(in Spanish) on how Docker works locally and the cloud scope (TASK-38) is in
[docs/GUIA_DOCKER.md](docs/GUIA_DOCKER.md).
The shared staging runbook is in [docs/STAGING.md](docs/STAGING.md).

### Shared staging quick reference

| Service | Staging endpoint/port | Access |
|---|---|---|
| Orders API | `https://staging.<team-domain>` / VM `8000` (Gateway ports `8082` / `8086`) | Team and integration services |
| PostgreSQL | SSH tunnel `127.0.0.1:15432` → VM `5432` | `POSTGRES_READONLY_USER` only |
| RabbitMQ AMQP | VM `5672` | Integration network |
| RabbitMQ management | SSH tunnel `127.0.0.1:15672` → VM `15672` | Maintainers |
| Redis | Docker network | API/workers only |

Staging uses `ghcr.io/fmat-restaurant/orderskds-backend:develop`. The
database is `ordenes_staging`; production uses separate volumes, credentials,
and GitHub Environment secrets. Do not replace the documented placeholder
hostname with an unverified URL.

---

### §1 — Repository prerequisites

Before the CI/CD pipeline can run successfully, a maintainer must configure
the following once per repository.

#### 1.1 GitHub Actions permissions

`Settings → Actions → General → Workflow permissions`

Select **Read and write permissions** and save. This is required by the
`build-and-push` job in `ci.yml`, which publishes images to GHCR and needs
`packages: write`.

#### 1.2 Secrets

`Settings → Secrets and variables → Actions → New repository secret`

| Secret name | Where to get it | Required by |
|---|---|---|
| `SONAR_TOKEN` | SonarCloud → My Account → Security → Generate Token (type: **User Token**) | `sonarcloud` job |
| `DISCORD_WEBHOOK_ALERTS` | Discord → Channel Settings → Integrations → Webhooks → Copy URL (Alerts channel) | `notify-on-failure` job and release failure alerts |
| `DISCORD_WEBHOOK_REPORTS` | Discord → Channel Settings → Integrations → Webhooks → Copy URL (Reports channel) | `zap-api-scan` job (ZAP report notifications) |
| `DISCORD_WEBHOOK_RELEASES` | Discord → Channel Settings → Integrations → Webhooks → Copy URL (Releases channel) | `build-and-push` job (Docker image release notifications) |

`GITHUB_TOKEN` is injected automatically by GitHub Actions — **do not create it**.

#### 1.3 Required CI/CD environment variables (for integration tests)

Add these as **Actions secrets** as well if you want the integration-tests or
zap-api-scan jobs to use a real database:

| Secret name | Example value |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://ordenes:ordenes@postgres:5432/ordenes` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `RABBITMQ_URL` | `amqp://ordenes:ordenes@rabbitmq:5672/` |
| `SAGA_TIMEOUT_SECONDS` | `60` (agreed in OP-01 / ADR-0001) |

---

### §2 — Branch protection

Branch protection is configured in `Settings → Branches` (or Rulesets) for
**both** `develop` and `main`. The full rule set is documented in
[docs/CONTRIBUTING.md §15](docs/CONTRIBUTING.md#15-protección-de-ramas-para-mantenedores).

Summary:

| Rule | `develop` | `main` |
|---|:---:|:---:|
| Require PR before merge | ✅ | ✅ |
| Required approvals | ≥ 1 | ≥ 1 |
| Dismiss stale approvals on new commits | ✅ | ✅ |
| Require resolved conversations | ✅ | ✅ |
| Require status checks (CI green) | ✅ | ✅ |
| Require branch up to date | ✅ | ✅ |
| No direct push, no force push | ✅ | ✅ |
| No branch deletion | ✅ | ✅ |
| Merge method | Squash only | Merge commit only |

**Required status checks** — the job names to add in the selector:

```
lint-and-types
unit-tests
sonarcloud
integration-tests
api-and-contract
k6-smoke
zap-api-scan
```

> **Note:** Status check names only appear in GitHub's selector after the
> workflow has run at least once on that branch. Mark them after
> TASK-01/TASK-02 complete their first successful run.

Also enable **Automatically delete head branches** in
`Settings → General → Pull Requests`.

---

### §3 — CI workflow overview

File: `.github/workflows/ci.yml`

```
push/PR to develop or main
        │
        ├─ validate-pr          (PR only) — branch name + Conventional Commits title
        ├─ lint-and-types       — gitleaks, ruff check/format, mypy --strict
        │
        └─ unit-tests           — pytest tests/unit, uploads coverage.xml + junit.xml
               │
               ├─ sonarcloud    — downloads artifacts, SonarCloud + Quality Gate
               │
               └─ integration-tests — pytest tests/integration/test_smoke.py (Testcontainers)
                        │
                        ├─ api-and-contract — Newman + pytest tests/contract tests/bdd
                        ├─ k6-smoke         — PT-08 (10 VUs, 30 s against PostgreSQL + Redis)
                        └─ zap-api-scan     — OWASP ZAP against /openapi.json

Runs after any failure:
        notify-on-failure       — Discord webhook alert

Runs on push to develop or main (after all checks pass):
        build-and-push          — Docker image → ghcr.io, Discord notification
```

---

### §4 — Diagnosing a failing job

1. Go to **Actions** (tab at the top of the repository).
2. Click the failing workflow run.
3. Click the red ❌ job name in the left panel.
4. Expand the failing step to read the log.
5. For coverage and test reports: scroll to the bottom of the run page and
   open the **Artifacts** section. Download `unit-test-results` and open
   `htmlcov/index.html` in a browser for a line-by-line coverage view.

Common failures and fixes:

| Symptom | Likely cause | Fix |
|---|---|---|
| `ruff check` fails | Style or import violations | Run `ruff check . --fix && ruff format .` locally |
| `mypy` fails | Missing types or `Any` usage | Add type annotations; see GUIDELINES §5.3 |
| `pytest` fails | Test assertion or import error | Run `pytest tests/unit -v` locally |
| SonarCloud Quality Gate fails | Coverage < 85 % or new issues | Check the SonarCloud dashboard for details |
| `gitleaks` fails | Secret detected in a commit | Rotate the secret immediately; see CONTRIBUTING §12 |
| `zap-api-scan` fails (HIGH) | Security issue in the API | Check `.zap/rules.tsv` and fix the vulnerability |

---

### §5 — Machine requirements (install once per developer)

Every team member needs the tools below **before** running the bootstrap
script. PostgreSQL, Redis and RabbitMQ are **not** installed on your machine:
they run inside Docker containers defined in `docker-compose.yml`.

#### 5.1 Required software

| Tool | Minimum version | Used for | Download |
|---|---|---|---|
| **Git** | 2.40+ | Clone, branches, commits | https://git-scm.com/downloads |
| **Docker Desktop** (includes Docker Compose v2) | Docker 24+ / Compose v2 | PostgreSQL, Redis, RabbitMQ, Testcontainers and containerized API/workers | https://www.docker.com/products/docker-desktop |
| **Python** | 3.12+ | Backend, Alembic, tests | https://www.python.org/downloads/ |
| **Node.js** + **pnpm** | Node 24 LTS / pnpm 9+ | Frontend repo (`orders-frontend`) and Newman contract runner | https://nodejs.org · `corepack enable` |
| **DB client** (recommended) | — | Browse and query PostgreSQL visually (see §7) | https://dbeaver.io/download/ or https://www.pgadmin.org/download/ |
| *k6*, *Newman* (optional) | — | Performance / API contract tests | `npm i -g newman` · https://k6.io/docs/get-started/installation/ |

> **Do not install a local PostgreSQL server.** The containerised database is
> the single source of truth for everybody, uses the exact version of CI
> (PostgreSQL 16), and a local server would collide with port `5432`.
> Install a *client* instead (§7).

#### 5.2 Windows-specific notes

1. Docker Desktop requires **WSL 2**. If it asks for it, run in an
   administrator PowerShell and restart:

   ```powershell
   wsl --install
   ```

2. During the Python installer, tick **Add python.exe to PATH**.
3. If PowerShell blocks the scripts (`bootstrap.ps1`, `Activate.ps1`), run
   once per machine:

   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
   ```

4. Open **Docker Desktop** and wait until the whale icon says *Engine running*
   before using any `docker` command.

#### 5.3 Verify the installation

```powershell
git --version              # 2.40 or newer
docker --version           # Docker version 24 or newer
docker compose version     # Docker Compose version v2.x
docker run --rm hello-world   # confirms the Docker engine is running
python --version           # Python 3.12 or newer
node --version             # v24.x (frontend / Newman)
```

#### 5.4 Ports that must be free

| Port | Service | Container (`docker compose`) |
|---|---|---|
| `5432` (or `POSTGRES_PORT=5433`) | PostgreSQL | `ordenes-postgres` (`postgres:16-alpine`) |
| `6379` | Redis | `ordenes-redis` (`redis:7-alpine`) |
| `5672` | RabbitMQ (AMQP) | `ordenes-rabbitmq` (`rabbitmq:3-management-alpine`) |
| `15672` | RabbitMQ management UI | `ordenes-rabbitmq` (`rabbitmq:3-management-alpine`) |
| `8000` | Orders API (`uvicorn` / `api`) | `ordenes-api-ci` (`orders-backend-api`, only when running Mode B) |

If one is taken (typically `5432` by a local PostgreSQL server), stop that
process or set `POSTGRES_PORT=5433` in `.env` (which maps `"5433:5432"` in
`docker-compose.yml`) and update `DATABASE_URL` in `.env` to port `5433`.

---

### §6 — Local development in one shot

**Windows PowerShell:**

```powershell
git clone https://github.com/FMAT-Restaurant/OrdersKDS-Backend.git
cd OrdersKDS-Backend
.\scripts\bootstrap.ps1
```

**macOS / Linux:**

```bash
git clone https://github.com/FMAT-Restaurant/OrdersKDS-Backend.git
cd OrdersKDS-Backend
bash scripts/bootstrap.sh
```

The bootstrap script:
1. Verifies Python 3.12+
2. Creates `.venv` and installs `requirements/dev.txt`
3. Generates `.env` with safe local defaults (`docker-compose.yml` credentials and `SAGA_TIMEOUT_SECONDS=60`)
4. Prints the remaining steps (Docker, migrations, API, workers)

After the script finishes:

```bash
# Activate the venv (required for alembic, uvicorn, pytest, etc.)
# Windows:     .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate

# Verify .env — ensure SAGA_TIMEOUT_SECONDS=60 (OP-01 / ADR-0001)

docker compose up -d postgres redis rabbitmq
alembic upgrade head

# Terminal 1 — API with hot reload (HTTP + WebSocket)
uvicorn app.main:app --reload --port 8000

# Terminal 2 — background workers (Outbox, Saga Watcher, Inbound Consumer)
python -m app.workers.run_all

# Generate a dev JWT for manual API testing
python scripts/dev_token.py --role waiter
```

API available at: `http://localhost:8000`  
OpenAPI docs: `http://localhost:8000/docs`  
RabbitMQ UI: `http://localhost:15672` (user `ordenes`, password `ordenes`)

#### 6.1 Verify that the infrastructure is healthy

```powershell
docker compose ps
# postgres, redis and rabbitmq must show STATUS "Up ... (healthy)"

docker exec ordenes-postgres psql -U ordenes -d ordenes -c "\dt"   # lists shifts, orders, order_items, outbox, processed_events
docker exec ordenes-redis redis-cli ping                          # PONG
```

The RabbitMQ container takes up to ~30 s to become *healthy* the first time.

#### 6.2 Daily routine

| Goal | Command |
|---|---|
| Start the infrastructure | `docker compose up -d postgres redis rabbitmq` |
| Stop it keeping the data | `docker compose stop` |
| Remove containers keeping the data | `docker compose down` |
| **Reset everything** (deletes the local DB, cache and queues) | `docker compose down -v` then `docker compose up -d` and `alembic upgrade head` |
| Follow a service log | `docker compose logs -f rabbitmq` |
| Apply new migrations after a `git pull` | `alembic upgrade head` |

#### 6.3 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker: error during connect` / `cannot connect to the Docker daemon` | Docker Desktop is not running | Open Docker Desktop and wait for *Engine running* |
| `port is already allocated` | Another process uses `5432`, `6379`, `5672` or `15672` | See §5.4 |
| `Activate.ps1 cannot be loaded` | PowerShell execution policy | See §5.2 (step 3) |
| `connection refused` on `localhost:5432` | Container not healthy yet | `docker compose ps`; wait for *healthy* |
| `password authentication failed for user "ordenes"` | `.env` credentials differ from the compose file, or the volume was created with other credentials | Align `.env` or run `docker compose down -v` |
| Service refuses to start: `SAGA_TIMEOUT_SECONDS` missing | Variable intentionally has no default (OP-01) | Set `SAGA_TIMEOUT_SECONDS=60` in `.env` |
| Integration tests fail to start containers | Docker not running (Testcontainers needs it) | Start Docker Desktop |

#### 6.4 Operational configuration variables (Sagas & KDS)

| Variable | Repo | Required | Example | Meaning |
|---|---|---|---|---|
| `SAGA_TIMEOUT_SECONDS` | `orders-backend` | Yes | `60` | Maximum time (in seconds) a confirmed `CREATED` order waits for Inventory reply before transitioning to `EXPIRED` (OP-01 / ADR-0001). The 60 s countdown starts at `confirmed_at`, not at order creation. No default value in code. |
| `SAGA_WATCHER_INTERVAL_SECONDS` | `orders-backend` | No | `5` | Polling interval (in seconds) for the Saga Timeout Watcher (OP-01 / ADR-0001, TASK-06). Worst-case real expiration is ≈ 65 s (60 s timeout + 5 s watcher interval). |
| `KDS_WARNING_AFTER_SECONDS` | `orders-backend` / `orders-frontend` | Optional | *(empty)* | Elapsed kitchen time threshold (in seconds from `in_preparation_at`) to mark a KDS card as warning (`--color-warning`, OP-11 / ADR-0009). |
| `KDS_LATE_AFTER_SECONDS` | `orders-backend` / `orders-frontend` | Optional | *(empty)* | Elapsed kitchen time threshold (in seconds from `in_preparation_at`) to mark a KDS card as critical (`--color-error`, OP-11 / ADR-0009). |

---

### §7 — Browsing the database with a visual client

The database lives in the `ordenes-postgres` container. You do not need a
PostgreSQL server on your machine to see it graphically: connect a **client**
(DBeaver Community or pgAdmin 4) to the container.

**Local connection (container started with `docker compose up -d`):**

| Field | Value |
|---|---|
| Host | `localhost` |
| Port | `5432` |
| Database | `ordenes` |
| User | `ordenes` |
| Password | `ordenes` |

In DBeaver: *Database → New Database Connection → PostgreSQL*, fill in the
fields and press **Test Connection** (it downloads the JDBC driver the first
time). In pgAdmin: *Register → Server*.

#### Databases per environment

| Environment | Where it runs | Who uses it | How to connect |
|---|---|---|---|
| **Local development** | Your own Docker container | You only; `docker compose down -v` resets it | Table above |
| **Automated tests** | Ephemeral Testcontainers (created and destroyed by `pytest tests/integration` and CI) | Test code | Nothing to connect to; never share it |
| **Shared testing / staging** | Shared host defined in [docs/STAGING.md](docs/STAGING.md) | The whole team and integration tests with other microservices | Host, port and read-only credentials via SSH tunnel |
| **Final version (release)** | Deployed from `main` | The released system | Restricted credentials; not used for experiments |

---

### §8 — Exact Step-by-Step Startup & Verification Guide (Local & Full Docker)

Follow **Mode A** for daily development (infrastructure in Docker, API and workers in local `.venv` with `--reload`), or **Mode B** to build all Docker images and run the **entire microservice inside Docker containers** (`postgres`, `redis`, `rabbitmq`, `api`, `workers`, `stubs`).

#### Step 1 — Start Docker Desktop
1. Open **Docker Desktop** and wait until the bottom-left indicator turns green (**Engine running**).
2. Keep the **Containers** tab visible to monitor container status and logs.

#### Step 2 — Choose Your Startup Mode

##### Mode A — Infrastructure in Docker + Local API & Workers (Recommended for development)

```powershell
# 1. Navigate to the backend repository root
cd C:\path\to\orders-backend

# 2. (First time only) Create .venv, install dev dependencies, and generate .env
.\scripts\bootstrap.ps1

# 3. Activate the Python virtual environment
.\.venv\Scripts\Activate.ps1

# 4. Ensure .env exists with local Docker defaults (SAGA_TIMEOUT_SECONDS=60)
python scripts/setup_env.py

# 5. Start PostgreSQL, Redis, and RabbitMQ and wait for healthy status
docker compose up -d postgres redis rabbitmq
docker compose ps
```

##### Mode B — Full 6-Container Stack Inside Docker (`docker-compose.yml` + `docker-compose.ci.yml`)

Combining `docker-compose.yml` and `docker-compose.ci.yml` builds **3 application images** from `Dockerfile` (`python:3.12-slim` + `requirements/runtime.txt`, including `websockets==15.0.1` for `/ws/kds` and `/ws/waiter`, and Python `urllib` healthcheck without requiring `curl`) and runs **6 containers total**:

| Compose service | Image | Container name | Host port(s) | Role |
|---|---|---|---|---|
| `postgres` | `postgres:16-alpine` | `ordenes-postgres` | `${POSTGRES_PORT:-5432}:5432` | ACID store (`shifts`, `orders`, `order_items`, `outbox`, `processed_events`) |
| `redis` | `redis:7-alpine` | `ordenes-redis` | `6379:6379` | Menu catalog projection (`catalog:platillo:*`) + WS Pub/Sub bridge |
| `rabbitmq` | `rabbitmq:3-management-alpine` | `ordenes-rabbitmq` | `5672:5672`, `15672:15672` | Event broker (`ordenes.topic`, `inventory.topic`, `menu.topic`, `sala.events`) + Management UI |
| `api` | `orders-backend-api` *(built from `Dockerfile`)* | `ordenes-api-ci` | `8000:8000` | FastAPI REST + WebSockets (`/ws/kds`, `/ws/waiter`) |
| `workers` | `orders-backend-workers` *(built from `Dockerfile`)* | `ordenes-workers-ci` | — *(internal)* | `python -m app.workers.run_all` (Outbox Publisher, Saga Timeout Watcher, Inbound Consumer, Catalog Reconciler; waits for `api` healthy) |
| `stubs` | `orders-backend-stubs` *(built from `Dockerfile`)* | `ordenes-stubs-ci` | — *(internal)* | `python -m app.stubs.runner` (Inventory & Billing contract stubs over RabbitMQ; waits for `rabbitmq` healthy) |

```powershell
cd C:\path\to\orders-backend

# 1. Export internal Docker-network connection variables for Compose
#    Why: Docker Compose interpolates .env first; if your local .env uses
#    127.0.0.1:5433 or an external AMQP URL, containers would fail to reach
#    sibling services unless you override these variables in the shell.
$env:DATABASE_URL="postgresql+asyncpg://ordenes:ordenes@postgres:5432/ordenes"
$env:REDIS_URL="redis://redis:6379/0"
$env:RABBITMQ_URL="amqp://ordenes:ordenes@rabbitmq:5672/"
$env:SAGA_TIMEOUT_SECONDS="60"
# Uncomment if a local PostgreSQL server occupies host port 5432:
# $env:POSTGRES_PORT="5433"

# 2. Build all images (`api`, `workers`, `stubs`) and start all 6 containers
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.ci.yml ps
docker images | Select-String "orders-backend|postgres|redis|rabbitmq"

# 3. Apply Alembic migrations inside the running API container
docker exec ordenes-api-ci alembic upgrade head
```

> **Important (`Dockerfile` & `scripts/`):** `Dockerfile` packages `app/`, `alembic.ini`, `contracts/events/`, and `ops/staging/staging-smoke.py` — it intentionally does **not** copy `scripts/`. Therefore:
> - Run `alembic upgrade head` inside the API container (`docker exec ordenes-api-ci alembic upgrade head`).
> - Run `python scripts/seed_demo.py --reset` and `python scripts/dev_token.py` from your **host `.venv`** against the exposed ports (`localhost:5432` or `5433`, and `localhost:6379`), as shown in Step 3.
> - If you run **Mode B**, skip Step 4 (the API, workers, and stubs are already running in Docker on port `8000`).

#### Step 3 — Run Migrations & Seed Demo Data

```powershell
# Activate host .venv (needed for seed_demo.py and dev_token.py in both Mode A and Mode B)
.\.venv\Scripts\Activate.ps1

# 1. (Mode A only — skip if already run via `docker exec` in Mode B)
alembic upgrade head

# 2. Populate Redis catalog + Shift #1 + 8 demo orders with fresh random UUIDv4 IDs
python scripts/seed_demo.py --reset

# Other useful seed commands:
# python scripts/seed_demo.py                 # Seeds only if the active shift has 0 orders (random UUIDv4s)
# python scripts/seed_demo.py --catalog-only  # Seeds only Redis catalog + active Shift #1 (0 demo orders)
```

> **Note on UUIDs:** Every run of `python scripts/seed_demo.py` or `python scripts/seed_demo.py --reset` generates **new random UUIDv4 values** for `orderId` and `itemId` and prints all 8 generated `OrderId`s directly in the terminal. You can also inspect the active IDs anytime via `GET /orders` or `GET /kds/queue`. (`--fixed-ids` exists only as a legacy option for static test scripts and is not the default workflow.)

**Seeded Data Reference by Shift Folio (`seed_demo.py`, aligned with Inventory `TEST_DATA.md`):**

| Folio | Location | Initial Status | Seeded Dishes | What to test with this order |
|---|---|---|---|---|
| `#1` | Table 5 | `RESERVED` | 2× `PLATE-BURGER-01`, 1× `PLATE-QUES-01` | KDS active queue. Test `PATCH /orders/{orderId}/start-preparation` or `PATCH .../items/{itemId}/kitchen-reject`. |
| `#2` | Table 12 | `IN_PREPARATION` | 2× `PLATE-QUES-01` (`PENDING`), 1× `PLATE-CESAR-01` (`READY`) | KDS active queue. Test `PATCH .../items/{itemId}/ready`, Admin void (`PATCH .../void`), or Chef void (`PATCH .../void-kitchen-error`). |
| `#3` | Table 3 | `DELIVERED` | 2× `PLATE-BURGER-01` (`DELIVERED`) | KDS Delivered tab (`GET /kds/queue?view=delivered`). Test dish redo (`PATCH .../items/{itemId}/redo`) or bill request (`POST /orders/{orderId}/request-bill`). |
| `#4` | Table 8 | `CREATED` | 1× `PLATE-BURGER-01`, 1× `PLATE-CESAR-01` | Draft order. Test `PATCH /orders/{orderId}/items`, `PATCH .../transfer`, `POST /orders/{orderId}/confirm` (triggers RabbitMQ Saga), or `PATCH .../cancel`. |
| `#5` | Takeaway | `CREATED` | 2× `PLATE-QUES-01`, 1× `PLATE-SIM-0001` | Takeaway draft order (`isTakeaway: true`). Ideal for testing `POST /orders/{orderId}/confirm` against stubs or manual RabbitMQ events. |
| `#6` | Table 15 | `RESERVED` | `PLATE-BURGER-01`, `PLATE-QUES-01`, `PLATE-CESAR-01`, `PLATE-SIM-0001` | Multi-station KDS test (`parrilla` + `fria`). |
| `#7` | Table 9 | `REJECTED` | 2× `PLATE-BURGER-01`, 1× `PLATE-QUES-01` | Rejected order with `rejectionDetails`. Reopen draft via `PATCH .../items` or cancel. |
| `#8` | Table 11 | `RESERVED` | 1× `PLATE-CESAR-01` | Reserved > 15 min ago (`Caducados` KDS tab with 10 min grace window). |

*Redis Catalog (`catalog:platillo:<id>`):*
- Available (`available=true`): `PLATE-BURGER-01` (`OFFER-BURGER-01`), `PLATE-QUES-01` (`OFFER-QUES-01`), `PLATE-CESAR-01` (`OFFER-CESAR-01`), `PLATE-SIM-0001` (`OFFER-SIM-0001`).
- Unavailable (`available=false`, returns `422 DISH_UNAVAILABLE`): `PLATE-AGOTADO-01`.

#### Step 4 — Generate Dev JWT Tokens & Start API + Workers (Mode A)

```powershell
# 1. Generate 24-hour development JWT tokens
python scripts/dev_token.py --role waiter --ttl 86400
python scripts/dev_token.py --role kitchen --ttl 86400
python scripts/dev_token.py --role chef --ttl 86400
python scripts/dev_token.py --role admin --ttl 86400

# 2. Terminal 1 — Start FastAPI (REST + WebSockets /ws/kds and /ws/waiter)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

In a **second PowerShell terminal**, start the background workers (required for Outbox publishing, Saga 60 s timeout, and RabbitMQ consumers):

```powershell
cd C:\path\to\orders-backend
.\.venv\Scripts\Activate.ps1
python -m app.workers.run_all
```

#### Step 5 — Verify Health, Swagger UI & Persistence

1. Verify all four health endpoints return `200 {"status": "ok"}`:
   - `http://localhost:8000/health`
   - `http://localhost:8000/api/v1/health`
   - `http://localhost:8000/api/v1/orders/health`
   - `http://localhost:8000/api/v1/kitchen/health`
2. Open **`http://localhost:8000/docs`**, click **Authorize**, paste a JWT from Step 4 (without `Bearer `), and test:
   - `waiter` / `admin`: `GET /orders` (copy the real `orderId`s of `#1`..`#8`), `POST /orders`, `PATCH /orders/{orderId}/items`, `PATCH /orders/{orderId}/transfer`, `POST /orders/{orderId}/confirm`, `POST /orders/{orderId}/request-bill`.
   - `kitchen` / `chef` / `admin`: `GET /kds/queue`, `PATCH /orders/{orderId}/start-preparation`, `PATCH /orders/{orderId}/items/{itemId}/ready`.
   - `chef` / `admin`: `GET /shifts/current`, `POST /shifts/open`, `POST /shifts/current/close`, `PATCH /orders/{orderId}/void-kitchen-error`.
   - `admin`: `PATCH /orders/{orderId}/void`.
3. Inspect PostgreSQL and Redis directly:
   ```powershell
   # PostgreSQL: orders and outbox
   docker exec ordenes-postgres psql -U ordenes -d ordenes -c "SELECT public_id, shift_folio, table_number, status, total_amount, version FROM orders ORDER BY id;"
   docker exec ordenes-postgres psql -U ordenes -d ordenes -c "SELECT id, event_id, event_type, correlation_id, published_at FROM outbox ORDER BY id;"

   # Redis catalog keys
   docker exec ordenes-redis redis-cli keys "catalog:platillo:*"
   ```

#### Step 6 — Testing End-to-End Flows with Real RabbitMQ

1. **Inspect exchanges and queues in RabbitMQ Management UI (`http://localhost:15672`, user `ordenes`, password `ordenes`):**
   - **Exchanges (durable `topic`):** `ordenes.topic`, `inventory.topic`, `menu.topic`, `sala.events`.
   - **Queues (durable):**
     - `orders.inventory.inbound` (bound by `workers` to `inventory.reservation.*`, `inventario.stock.*`, `menu.#`, `sala.#`, `pagos.#`, `payments.#`, `pago.#`, etc.).
     - `stubs.orders.inbound` (bound by `stubs` to `ordenes.orden.creada`, `ordenes.orden.cancelada`, `ordenes.orden.expirada`, `ordenes.cuenta.solicitada`, `ordenes.cobro.solicitado`).

2. **Flow 6A — Automated real RabbitMQ round-trip with `stubs` running:**
   - Ensure `ordenes-workers-ci` and `ordenes-stubs-ci` are running (Mode B) — or in Mode A start `python -m app.stubs.runner` in a 3rd terminal alongside `run_all`.
   - Confirm draft Folio `#4` (`CREATED`, Table 8) using the real `orderId` printed by `seed_demo.py`:
     ```powershell
     $WAITER_JWT = (python scripts/dev_token.py --role waiter | Select-String '^Bearer\s+(\S+)$').Matches[0].Groups[1].Value
     $ORDER_ID = "<real-orderId-of-folio-4>"
     Invoke-RestMethod -Method Post -Uri "http://localhost:8000/orders/$ORDER_ID/confirm" `
       -Headers @{ Authorization = "Bearer $WAITER_JWT" }
     ```
   - Trace the real broker loop:
     1. `OutboxWorker` publishes `ordenes.orden.creada` to `ordenes.topic` and sets `outbox.published_at`.
     2. `ordenes-stubs-ci` (`InventoryStub`, default `INVENTORY_STUB_MODE=RESERVE`) receives it and publishes `inventario.stock.reservado`.
     3. `ordenes-workers-ci` (`InboundConsumerWorker`) consumes `inventario.stock.reservado` from `orders.inventory.inbound`, records it in `processed_events`, and updates the order to `RESERVED` (pushed live to `/ws/kds`).
   - Verify with `GET /orders/$ORDER_ID` (status is now `RESERVED`) and `docker compose -f docker-compose.yml -f docker-compose.ci.yml logs --tail=30 workers stubs`.
   - Repeat with `POST /orders/<real-orderId-of-folio-3>/request-bill` (`DELIVERED` order): `BillingStub` (`BILLING_STUB_AUTO_PAY=true`) publishes `pagos.pago.completado` and the order transitions `DELIVERED -> PAID`.
   - You can switch stub behavior via env vars (`INVENTORY_STUB_MODE=RESERVE|REJECT|SILENT|DELAYED|ATOMIC`, `BILLING_STUB_AUTO_PAY=true|false`).

3. **Flow 6B — Manual real RabbitMQ event testing WITHOUT `stubs` (real contract simulation):**
   - Stop `stubs` so only real incoming broker messages resolve the Saga:
     ```powershell
     docker compose -f docker-compose.yml -f docker-compose.ci.yml stop stubs
     ```
   - Confirm Folio `#5` (`CREATED`, Takeaway) with `POST /orders/<real-orderId-of-folio-5>/confirm`. It stays `CREATED` waiting for Inventory.
   - In RabbitMQ UI (`http://localhost:15672`) → **Exchanges** → click **`inventory.topic`** → **Publish message**:
     - To **reserve** stock, set **Routing key** = `inventario.stock.reservado` and **Payload**:
       ```json
       {
         "eventId": "<generate-a-new-uuid-v4>",
         "orderId": "<real-orderId-of-folio-5>",
         "correlationId": "<real-orderId-of-folio-5>",
         "reservations": [{"offerId": "OFFER-QUES-01", "quantity": 2}],
         "reservas": [{"offerId": "OFFER-QUES-01", "quantity": 2}]
       }
       ```
     - Or to test **stock rejection** on another confirmed order, set **Routing key** = `inventario.stock.insuficiente` and **Payload**:
       ```json
       {
         "eventId": "<generate-a-new-uuid-v4>",
         "orderId": "<real-orderId-of-created-order>",
         "correlationId": "<real-orderId-of-created-order>",
         "missingIngredients": ["pan"],
         "faltantes": ["pan"],
         "rejectedItems": [{"offerId": "OFFER-BURGER-01", "requestedQuantity": 1, "availableQuantity": 0}],
         "reservedItems": []
       }
       ```
     - Or to test **payment completion** on a `DELIVERED` order, open exchange **`ordenes.topic`**, set **Routing key** = `pagos.pago.completado` and **Payload**:
       ```json
       {
         "eventId": "<generate-a-new-uuid-v4>",
         "orderId": "<real-orderId-of-delivered-order>",
         "sessionId": "00000000-0000-0000-0000-000000000099",
         "totalPaid": 330.0,
         "status": "PAID"
       }
       ```
   - Verify the transition via `GET /orders/{orderId}` and inspect `processed_events` in PostgreSQL (re-publishing with the exact same `eventId` is ignored idempotently).

---
---

## Versión en Español

Referencia rápida para el estándar de CI/CD requerido por el curso.
Las instrucciones completas de configuración están en [README.md](README.md); el flujo de contribución está en [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).
Una guía básica sobre cómo funciona Docker en local y su alcance en la nube (TASK-38) se encuentra en [docs/GUIA_DOCKER.md](docs/GUIA_DOCKER.md).
El runbook del entorno compartido de staging está en [docs/STAGING.md](docs/STAGING.md).

---

### §1 — Requisitos previos del repositorio

Antes de que el pipeline de CI/CD pueda ejecutarse correctamente, un mantenedor debe configurar lo siguiente una vez por repositorio.

#### 1.1 Permisos de GitHub Actions

`Settings → Actions → General → Workflow permissions`

Selecciona **Read and write permissions** y guarda. Esto es requerido por el job `build-and-push` en `ci.yml`, el cual publica imágenes a GHCR y necesita `packages: write`.

#### 1.2 Secretos

`Settings → Secrets and variables → Actions → New repository secret`

| Nombre del secreto | Dónde obtenerlo | Requerido por |
|---|---|---|
| `SONAR_TOKEN` | SonarCloud → My Account → Security → Generate Token (tipo: **User Token**) | Job `sonarcloud` |
| `DISCORD_WEBHOOK_ALERTS` | Discord → Ajustes del canal → Integraciones → Webhooks → Copiar URL (Canal de Alertas) | Job `notify-on-failure` y alertas de fallo al empaquetar |
| `DISCORD_WEBHOOK_REPORTS` | Discord → Ajustes del canal → Integraciones → Webhooks → Copiar URL (Canal de Reportes) | Job `zap-api-scan` (reportes de ZAP API Scan) |
| `DISCORD_WEBHOOK_RELEASES` | Discord → Ajustes del canal → Integraciones → Webhooks → Copiar URL (Canal de Releases) | Job `build-and-push` (notificación de éxito al empaquetar Docker) |

`GITHUB_TOKEN` es inyectado automáticamente por GitHub Actions — **no lo crees**.

#### 1.3 Variables de entorno CI/CD requeridas (para pruebas de integración)

Añade estas también como **Actions secrets** si quieres que los jobs `integration-tests` o `zap-api-scan` usen una base de datos real:

| Nombre del secreto | Valor de ejemplo |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://ordenes:ordenes@postgres:5432/ordenes` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `RABBITMQ_URL` | `amqp://ordenes:ordenes@rabbitmq:5672/` |
| `SAGA_TIMEOUT_SECONDS` | `60` (acordado en OP-01 / ADR-0001) |

---

### §2 — Protección de ramas

La protección de ramas se configura en `Settings → Branches` (o Rulesets) para **ambas** `develop` y `main`. Las reglas completas están en [docs/CONTRIBUTING.md §15](docs/CONTRIBUTING.md#15-protección-de-ramas-para-mantenedores).

Resumen:

| Regla | `develop` | `main` |
|---|:---:|:---:|
| Requerir PR antes del merge | ✅ | ✅ |
| Aprobaciones requeridas | ≥ 1 | ≥ 1 |
| Descartar aprobaciones en nuevos commits | ✅ | ✅ |
| Requerir conversaciones resueltas | ✅ | ✅ |
| Requerir comprobaciones de estado (CI en verde) | ✅ | ✅ |
| Requerir que la rama esté actualizada | ✅ | ✅ |
| Sin direct push, sin force push | ✅ | ✅ |
| Sin borrado de rama | ✅ | ✅ |
| Método de merge | Solo Squash | Solo Merge commit |

**Comprobaciones de estado requeridas** — nombres de jobs a agregar en el selector:

```
lint-and-types
unit-tests
sonarcloud
integration-tests
api-and-contract
k6-smoke
zap-api-scan
```

> **Nota:** Los nombres de las comprobaciones de estado solo aparecen en el selector de GitHub después de que el workflow se ha ejecutado al menos una vez en esa rama. Márcalos después de que la TASK-01/TASK-02 completen su primera ejecución exitosa.

También habilita **Automatically delete head branches** en `Settings → General → Pull Requests`.

---

### §3 — Resumen del flujo CI

Archivo: `.github/workflows/ci.yml`

```
push/PR a develop o main
        │
        ├─ validate-pr          (Solo en PR) — nombre de rama + título de Conventional Commits
        ├─ lint-and-types       — gitleaks, ruff check/format, mypy --strict
        │
        └─ unit-tests           — pytest tests/unit, sube coverage.xml + junit.xml
               │
               ├─ sonarcloud    — descarga artefactos, SonarCloud + Quality Gate
               │
               └─ integration-tests — pytest tests/integration/test_smoke.py (Testcontainers)
                        │
                        ├─ api-and-contract — Newman + pytest tests/contract tests/bdd
                        ├─ k6-smoke         — PT-08 (10 VUs, 30 s contra PostgreSQL + Redis)
                        └─ zap-api-scan     — OWASP ZAP contra /openapi.json

Se ejecuta tras cualquier fallo:
        notify-on-failure       — Alerta por webhook a Discord

Se ejecuta al hacer push a develop o main (tras pasar todos los checks):
        build-and-push          — Imagen Docker → ghcr.io, Notificación a Discord
```

---

### §4 — Diagnosticar un job fallido

1. Ve a **Actions** (pestaña superior del repositorio).
2. Haz clic en la ejecución fallida del workflow.
3. Haz clic en el nombre rojo ❌ del job en el panel izquierdo.
4. Expande el paso fallido para leer el log.
5. Para reportes de cobertura y pruebas: baja al fondo de la página y abre la sección **Artifacts**. Descarga `unit-test-results` y abre `htmlcov/index.html` en un navegador para ver la cobertura línea por línea.

Fallos comunes y soluciones:

| Síntoma | Causa probable | Solución |
|---|---|---|
| `ruff check` falla | Infracciones de estilo o import | Ejecutar `ruff check . --fix && ruff format .` localmente |
| `mypy` falla | Tipos faltantes o uso de `Any` | Añadir anotaciones; ver GUIDELINES §5.3 |
| `pytest` falla | Error de aserción o import | Ejecutar `pytest tests/unit -v` localmente |
| Quality Gate falla | Cobertura < 85 % o nuevos problemas | Revisar el dashboard de SonarCloud para detalles |
| `gitleaks` falla | Secreto detectado en un commit | Rotar el secreto inmediatamente; ver CONTRIBUTING §12 |
| `zap-api-scan` falla (ALTO) | Problema de seguridad en la API | Revisar `.zap/rules.tsv` y arreglar la vulnerabilidad |

---

### §5 — Requerimientos de la máquina (instalar una vez por desarrollador)

Cada miembro del equipo necesita las siguientes herramientas **antes** de ejecutar el script de inicialización. PostgreSQL, Redis y RabbitMQ **no** se instalan en tu máquina: corren dentro de contenedores Docker definidos en `docker-compose.yml`.

#### 5.1 Software requerido

| Herramienta | Versión mínima | Usado para | Descarga |
|---|---|---|---|
| **Git** | 2.40+ | Clonar, ramas, commits | https://git-scm.com/downloads |
| **Docker Desktop** (incluye Docker Compose v2) | Docker 24+ / Compose v2 | PostgreSQL, Redis, RabbitMQ, Testcontainers y API/workers contenerizados | https://www.docker.com/products/docker-desktop |
| **Python** | 3.12+ | Backend, Alembic, tests | https://www.python.org/downloads/ |
| **Node.js** + **pnpm** | Node 24 LTS / pnpm 9+ | Repo Frontend (`orders-frontend`) y runner Newman | https://nodejs.org · `corepack enable` |
| **Cliente BD** (recomendado) | — | Explorar PostgreSQL visualmente (ver §7) | https://dbeaver.io/download/ or https://www.pgadmin.org/download/ |
| *k6*, *Newman* (opcional) | — | Pruebas de rendimiento / contrato de API | `npm i -g newman` · https://k6.io/docs/get-started/installation/ |

> **No instales un servidor PostgreSQL local.** La base de datos contenerizada es la única fuente de verdad para todos, usa la versión exacta de CI (PostgreSQL 16), y un servidor local chocaría con el puerto `5432`.
> Instala un *cliente* en su lugar (§7).

#### 5.2 Notas específicas para Windows

1. Docker Desktop requiere **WSL 2**. Si te lo pide, ejecuta en un PowerShell como administrador y reinicia:

   ```powershell
   wsl --install
   ```

2. Durante el instalador de Python, marca **Add python.exe to PATH**.
3. Si PowerShell bloquea los scripts (`bootstrap.ps1`, `Activate.ps1`), ejecuta una vez por máquina:

   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
   ```

4. Abre **Docker Desktop** y espera a que el ícono de la ballena diga *Engine running* antes de usar cualquier comando `docker`.

#### 5.3 Verificar la instalación

```powershell
git --version              # 2.40 o superior
docker --version           # Docker version 24 o superior
docker compose version     # Docker Compose version v2.x
docker run --rm hello-world   # confirma que el motor Docker corre
python --version           # Python 3.12 o superior
node --version             # v24.x (frontend / Newman)
```

#### 5.4 Puertos que deben estar libres

| Puerto | Servicio | Contenedor (`docker compose`) |
|---|---|---|
| `5432` (o `POSTGRES_PORT=5433`) | PostgreSQL | `ordenes-postgres` (`postgres:16-alpine`) |
| `6379` | Redis | `ordenes-redis` (`redis:7-alpine`) |
| `5672` | RabbitMQ (AMQP) | `ordenes-rabbitmq` (`rabbitmq:3-management-alpine`) |
| `15672` | RabbitMQ interfaz de administración | `ordenes-rabbitmq` (`rabbitmq:3-management-alpine`) |
| `8000` | API de Órdenes (`uvicorn` / `api`) | `ordenes-api-ci` (`orders-backend-api`, solo en Modo B) |

Si uno está ocupado (típicamente el `5432` por un servidor local de PostgreSQL), detén ese proceso o define `POSTGRES_PORT=5433` en `.env` (que mapea `"5433:5432"` en `docker-compose.yml`) y actualiza el puerto a `5433` en `DATABASE_URL`.

---

### §6 — Desarrollo local de un tirón

**Windows PowerShell:**

```powershell
git clone https://github.com/FMAT-Restaurant/OrdersKDS-Backend.git
cd OrdersKDS-Backend
.\scripts\bootstrap.ps1
```

**macOS / Linux:**

```bash
git clone https://github.com/FMAT-Restaurant/OrdersKDS-Backend.git
cd OrdersKDS-Backend
bash scripts/bootstrap.sh
```

El script de bootstrap:
1. Verifica Python 3.12+
2. Crea `.venv` e instala `requirements/dev.txt`
3. Genera `.env` con defaults locales seguros (credenciales de `docker-compose.yml` y `SAGA_TIMEOUT_SECONDS=60`)
4. Imprime los pasos restantes (Docker, migraciones, API, workers)

Después de que el script termine:

```bash
# Activa el entorno virtual (requerido para alembic, uvicorn, pytest, etc.)
# Windows:     .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate

# Verifica .env — asegura SAGA_TIMEOUT_SECONDS=60 (OP-01 / ADR-0001)

docker compose up -d postgres redis rabbitmq
alembic upgrade head

# Terminal 1 — API con recarga en caliente (HTTP + WebSocket)
uvicorn app.main:app --reload --port 8000

# Terminal 2 — workers en segundo plano (Outbox, Saga Watcher, Inbound Consumer)
python -m app.workers.run_all

# Genera un JWT de desarrollo para probar la API manualmente
python scripts/dev_token.py --role waiter
```

API disponible en: `http://localhost:8000`  
Docs OpenAPI: `http://localhost:8000/docs`  
Interfaz RabbitMQ: `http://localhost:15672` (usuario `ordenes`, contraseña `ordenes`)

#### 6.1 Verificar que la infraestructura esté sana

```powershell
docker compose ps
# postgres, redis y rabbitmq deben mostrar STATUS "Up ... (healthy)"

docker exec ordenes-postgres psql -U ordenes -d ordenes -c "\dt"   # lista shifts, orders, order_items, outbox, processed_events
docker exec ordenes-redis redis-cli ping                          # PONG
```

El contenedor de RabbitMQ tarda hasta ~30 s en estar *healthy* la primera vez.

#### 6.2 Rutina diaria

| Objetivo | Comando |
|---|---|
| Iniciar infraestructura | `docker compose up -d postgres redis rabbitmq` |
| Detener conservando datos | `docker compose stop` |
| Eliminar contenedores conservando datos | `docker compose down` |
| **Reiniciar todo** (borra BD local, caché y colas) | `docker compose down -v` luego `docker compose up -d` y `alembic upgrade head` |
| Seguir log de un servicio | `docker compose logs -f rabbitmq` |
| Aplicar nuevas migraciones tras un `git pull` | `alembic upgrade head` |

#### 6.3 Solución de problemas (Troubleshooting)

| Síntoma | Causa probable | Solución |
|---|---|---|
| `docker: error during connect` | Docker Desktop no está corriendo | Abre Docker Desktop y espera *Engine running* |
| `port is already allocated` | Otro proceso usa `5432`, `6379`, `5672` o `15672` | Ver §5.4 |
| `Activate.ps1 cannot be loaded` | Política de ejecución de PowerShell | Ver §5.2 (paso 3) |
| `connection refused` en `localhost:5432` | Contenedor aún no está *healthy* | `docker compose ps`; esperar *healthy* |
| `password authentication failed` | Credenciales en `.env` difieren del compose... | Alinear `.env` o ejecutar `docker compose down -v` |
| Servicio rechaza arrancar: falta `SAGA_TIMEOUT_SECONDS` | Variable intencionalmente sin default | Establécela en `60` dentro de `.env` |
| Falla arranque de tests de integración | Docker no corre (Testcontainers lo necesita) | Inicia Docker Desktop |

#### 6.4 Variables operacionales de configuración (Sagas y KDS)

| Variable | Repo | Obligatoria | Ejemplo | Significado |
|---|---|---|---|---|
| `SAGA_TIMEOUT_SECONDS` | `orders-backend` | Sí | `60` | Tiempo máximo (en segundos) que una orden confirmada en estado `CREATED` espera respuesta de Inventario antes de transitar a `EXPIRED` (OP-01 / ADR-0001). El conteo de 60 s inicia en `confirmed_at`, no en la creación del pedido. Sin valor por defecto en código. |
| `SAGA_WATCHER_INTERVAL_SECONDS` | `orders-backend` | No | `5` | Intervalo de sondeo (en segundos) con el que el Saga Timeout Watcher revisa órdenes vencidas (OP-01 / ADR-0001, TASK-06). El peor caso real de expiración es ≈ 65 s (60 s del timeout + 5 s del intervalo del watcher). |
| `KDS_WARNING_AFTER_SECONDS` | `orders-backend` / `orders-frontend` | Opcional | *(vacía)* | Umbral de tiempo en cocina (en segundos desde `in_preparation_at`) para marcar una tarjeta en el KDS como "Por atrasarse" (`--color-warning`, OP-11 / ADR-0009). |
| `KDS_LATE_AFTER_SECONDS` | `orders-backend` / `orders-frontend` | Opcional | *(vacía)* | Umbral crítico de tiempo en cocina (en segundos desde `in_preparation_at`) para marcar una tarjeta en el KDS como "Atrasada" (`--color-error`, OP-11 / ADR-0009). |

---

### §7 — Explorando la base de datos con un cliente visual

La base de datos vive en el contenedor `ordenes-postgres`. No necesitas un servidor PostgreSQL en tu máquina para verla gráficamente: conecta un **cliente** (DBeaver Community o pgAdmin 4) al contenedor.

**Conexión local (contenedor iniciado con `docker compose up -d`):**

| Campo | Valor |
|---|---|
| Host | `localhost` |
| Port | `5432` |
| Database | `ordenes` |
| User | `ordenes` |
| Password | `ordenes` |

En DBeaver: *Database → New Database Connection → PostgreSQL*, llena los campos y presiona **Test Connection** (descargará el driver JDBC la primera vez). En pgAdmin: *Register → Server*.

#### Bases de datos por entorno

| Entorno | Dónde corre | Quién la usa | Cómo conectarse |
|---|---|---|---|
| **Desarrollo local** | Tu propio contenedor Docker | Solo tú; `docker compose down -v` la reinicia | Tabla superior |
| **Pruebas automáticas** | Testcontainers efímeros (creados y destruidos por `pytest tests/integration` y CI) | Código de pruebas | Nada a qué conectar; nunca la compartas |
| **Pruebas compartidas / staging** | Host compartido definido en [docs/STAGING.md](docs/STAGING.md) | Todo el equipo y tests de integración con otros MS | Host, puerto y credenciales de solo lectura vía túnel SSH |
| **Versión final (release)** | Desplegada desde `main` | El sistema liberado | Credenciales restringidas; no se usa para experimentos |

---

### §8 — Guía Exacta Paso a Paso para Levantar y Verificar el Microservicio (Local y Docker Completo)

Usa el **Modo A** para desarrollo diario (infraestructura en Docker, API y workers locales en `.venv` con `--reload`), o el **Modo B** para construir todas las imágenes Docker y ejecutar **todo el microservicio dentro de contenedores** (`postgres`, `redis`, `rabbitmq`, `api`, `workers`, `stubs`).

#### Paso 1 — Iniciar Docker Desktop
1. Abre **Docker Desktop** y espera hasta que la esquina inferior izquierda muestre el indicador verde **Engine running**.
2. Mantén abierta la pestaña **Containers** para supervisar el estado y los logs de los contenedores.

#### Paso 2 — Elegir Modo de Arranque

##### Modo A — Infraestructura en Docker + API y Workers locales (Recomendado para desarrollo)

```powershell
# 1. Entrar a la raíz del repositorio backend
cd C:\ruta\a\orders-backend

# 2. (Solo la primera vez) Crear .venv, instalar dependencias dev y generar .env
.\scripts\bootstrap.ps1

# 3. Activar el entorno virtual de Python
.\.venv\Scripts\Activate.ps1

# 4. Asegurar .env con los defaults de Docker local (SAGA_TIMEOUT_SECONDS=60)
python scripts/setup_env.py

# 5. Levantar PostgreSQL, Redis y RabbitMQ y esperar estado healthy
docker compose up -d postgres redis rabbitmq
docker compose ps
```

##### Modo B — Stack Completo de 6 Contenedores en Docker (`docker-compose.yml` + `docker-compose.ci.yml`)

Al combinar `docker-compose.yml` y `docker-compose.ci.yml`, Docker construye **3 imágenes de aplicación** desde `Dockerfile` (`python:3.12-slim` + `requirements/runtime.txt`, con `websockets==15.0.1` para `/ws/kds` y `/ws/waiter`, y `healthcheck` con Python `urllib` sin requerir `curl`) y levanta **6 contenedores en total**:

| Servicio Compose | Imagen | Contenedor | Puerto(s) en host | Rol |
|---|---|---|---|---|
| `postgres` | `postgres:16-alpine` | `ordenes-postgres` | `${POSTGRES_PORT:-5432}:5432` | Base ACID (`shifts`, `orders`, `order_items`, `outbox`, `processed_events`) |
| `redis` | `redis:7-alpine` | `ordenes-redis` | `6379:6379` | Vista materializada del catálogo (`catalog:platillo:*`) + puente Pub/Sub WS |
| `rabbitmq` | `rabbitmq:3-management-alpine` | `ordenes-rabbitmq` | `5672:5672`, `15672:15672` | Broker de eventos (`ordenes.topic`, `inventory.topic`, `menu.topic`, `sala.events`) + Panel UI |
| `api` | `orders-backend-api` *(creada desde `Dockerfile`)* | `ordenes-api-ci` | `8000:8000` | API FastAPI REST + WebSockets (`/ws/kds`, `/ws/waiter`) |
| `workers` | `orders-backend-workers` *(creada desde `Dockerfile`)* | `ordenes-workers-ci` | — *(red interna)* | `python -m app.workers.run_all` (Outbox, Saga Watcher, Consumer AMQP, Reconciliador; espera a `api` healthy) |
| `stubs` | `orders-backend-stubs` *(creada desde `Dockerfile`)* | `ordenes-stubs-ci` | — *(red interna)* | `python -m app.stubs.runner` (Stubs de contrato de Inventario y Pagos sobre RabbitMQ; espera a `rabbitmq` healthy) |

```powershell
cd C:\ruta\a\orders-backend

# 1. Definir en consola las URLs internas de la red Docker para Compose
#    Por qué: Docker Compose lee primero tu .env; si tu .env local apunta a
#    127.0.0.1:5433 o a una URL externa de AMQP, los contenedores fallarían al
#    intentar conectarse a los servicios hermanos si no sobrescribes estas variables.
$env:DATABASE_URL="postgresql+asyncpg://ordenes:ordenes@postgres:5432/ordenes"
$env:REDIS_URL="redis://redis:6379/0"
$env:RABBITMQ_URL="amqp://ordenes:ordenes@rabbitmq:5672/"
$env:SAGA_TIMEOUT_SECONDS="60"
# Descomenta esta línea si un PostgreSQL local ocupa el puerto 5432 en tu máquina:
# $env:POSTGRES_PORT="5433"

# 2. Compilar todas las imágenes (`api`, `workers`, `stubs`) y levantar los 6 contenedores
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.ci.yml ps
docker images | Select-String "orders-backend|postgres|redis|rabbitmq"

# 3. Aplicar migraciones Alembic dentro del contenedor de la API
docker exec ordenes-api-ci alembic upgrade head
```

> **Importante (`Dockerfile` y `scripts/`):** El `Dockerfile` copia `app/`, `alembic.ini`, `contracts/events/` y `ops/staging/staging-smoke.py`, pero intencionalmente **no** incluye la carpeta `scripts/`. Por ello:
> - Ejecuta `alembic upgrade head` dentro del contenedor (`docker exec ordenes-api-ci alembic upgrade head`).
> - Ejecuta `python scripts/seed_demo.py --reset` y `python scripts/dev_token.py` desde el **`.venv` de tu máquina host** contra los puertos expuestos (`localhost:5432` o `5433`, y `localhost:6379`), como indica el Paso 3.
> - Si utilizas el **Modo B**, omite el Paso 4 siguiente (API, workers y stubs ya están corriendo en Docker en el puerto `8000`).

#### Paso 3 — Ejecutar Migraciones y Cargar Datos Demo

```powershell
# Activar el .venv del host (necesario para seed_demo.py y dev_token.py en Modo A y Modo B)
.\.venv\Scripts\Activate.ps1

# 1. (Solo Modo A — omítelo si ya aplicaste migraciones con `docker exec` en Modo B)
alembic upgrade head

# 2. Poblar catálogo en Redis + Turno #1 + 8 órdenes demo con UUIDv4 aleatorios nuevos
python scripts/seed_demo.py --reset

# Otros modos útiles del seeder:
# python scripts/seed_demo.py                 # Siembra solo si el turno activo tiene 0 órdenes (UUIDv4 aleatorios)
# python scripts/seed_demo.py --catalog-only  # Carga solo el catálogo en Redis y abre Turno #1 (0 órdenes)
```

> **Nota sobre los UUID:** Cada ejecución de `python scripts/seed_demo.py` o `python scripts/seed_demo.py --reset` genera **UUIDv4 aleatorios nuevos** para cada `orderId` e `itemId`, e imprime los 8 `OrderId` generados directamente en la consola. También puedes consultarlos en todo momento mediante `GET /orders` o `GET /kds/queue`. *(El flag `--fixed-ids` solo permanece como opción heredada para scripts estáticos; ya no es el flujo por defecto.)*

**Tabla de referencia por Folio de Turno (`seed_demo.py`, alineado a `TEST_DATA.md` de Inventario):**

| Folio | Ubicación | Estado Inicial | Platillos Sembrados | Qué probar con esta orden |
|---|---|---|---|---|
| `#1` | Mesa 5 | `RESERVED` | 2× `PLATE-BURGER-01`, 1× `PLATE-QUES-01` | Cola activa KDS. Probar `PATCH /orders/{orderId}/start-preparation` o `PATCH .../items/{itemId}/kitchen-reject`. |
| `#2` | Mesa 12 | `IN_PREPARATION` | 2× `PLATE-QUES-01` (`PENDING`), 1× `PLATE-CESAR-01` (`READY`) | Cola activa KDS. Probar `PATCH .../items/{itemId}/ready`, anulación admin (`PATCH .../void`) o error de cocina (`PATCH .../void-kitchen-error`). |
| `#3` | Mesa 3 | `DELIVERED` | 2× `PLATE-BURGER-01` (`DELIVERED`) | Pestaña Entregadas (`GET /kds/queue?view=delivered`). Probar rehechura (`PATCH .../items/{itemId}/redo`) o solicitud de cuenta (`POST /orders/{orderId}/request-bill`). |
| `#4` | Mesa 8 | `CREATED` | 1× `PLATE-BURGER-01`, 1× `PLATE-CESAR-01` | Borrador. Probar `PATCH /orders/{orderId}/items`, `PATCH .../transfer`, `POST /orders/{orderId}/confirm` (dispara Saga en RabbitMQ) o `PATCH .../cancel`. |
| `#5` | Para llevar | `CREATED` | 2× `PLATE-QUES-01`, 1× `PLATE-SIM-0001` | Borrador para llevar (`isTakeaway: true`). Ideal para probar `POST /orders/{orderId}/confirm` con stubs o eventos manuales en RabbitMQ. |
| `#6` | Mesa 15 | `RESERVED` | `PLATE-BURGER-01`, `PLATE-QUES-01`, `PLATE-CESAR-01`, `PLATE-SIM-0001` | Prueba multi-estación en KDS (`parrilla` + `fria`). |
| `#7` | Mesa 9 | `REJECTED` | 2× `PLATE-BURGER-01`, 1× `PLATE-QUES-01` | Orden rechazada con `rejectionDetails`. Reabrir borrador con `PATCH .../items` o cancelar. |
| `#8` | Mesa 11 | `RESERVED` | 1× `PLATE-CESAR-01` | Reservada hace > 15 min (pestaña `Caducados` del KDS con ventana de gracia de 10 min). |

*Catálogo en Redis (`catalog:platillo:<id>`):*
- **Disponibles (`available=true`):** `PLATE-BURGER-01` (`OFFER-BURGER-01`), `PLATE-QUES-01` (`OFFER-QUES-01`), `PLATE-CESAR-01` (`OFFER-CESAR-01`), `PLATE-SIM-0001` (`OFFER-SIM-0001`).
- **Agotado (`available=false`, responde `422 DISH_UNAVAILABLE`):** `PLATE-AGOTADO-01`.

#### Paso 4 — Generar Tokens JWT de Desarrollo y Arrancar API + Workers (Modo A)

```powershell
# 1. Generar tokens JWT de desarrollo válidos por 24 horas
python scripts/dev_token.py --role waiter --ttl 86400
python scripts/dev_token.py --role kitchen --ttl 86400
python scripts/dev_token.py --role chef --ttl 86400
python scripts/dev_token.py --role admin --ttl 86400

# 2. Terminal 1 — Levantar FastAPI (REST + WebSockets /ws/kds y /ws/waiter)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

En una **segunda terminal de PowerShell**, inicia los workers en segundo plano (necesarios para publicar el `outbox`, vigilar el timeout de Saga de 60 s y consumir eventos de RabbitMQ):

```powershell
cd C:\ruta\a\orders-backend
.\.venv\Scripts\Activate.ps1
python -m app.workers.run_all
```

#### Paso 5 — Verificar Salud, Swagger UI y Persistencia

1. Verifica que las cuatro rutas de salud respondan `200 {"status": "ok"}`:
   - `http://localhost:8000/health`
   - `http://localhost:8000/api/v1/health`
   - `http://localhost:8000/api/v1/orders/health`
   - `http://localhost:8000/api/v1/kitchen/health`
2. Abre **`http://localhost:8000/docs`**, haz clic en **Authorize**, pega uno de los JWT del Paso 4 (sin la palabra `Bearer`) y prueba:
   - `waiter` / `admin`: `GET /orders` (copia los `orderId` reales de `#1`..`#8`), `POST /orders`, `PATCH /orders/{orderId}/items`, `PATCH /orders/{orderId}/transfer`, `POST /orders/{orderId}/confirm`, `POST /orders/{orderId}/request-bill`.
   - `kitchen` / `chef` / `admin`: `GET /kds/queue`, `PATCH /orders/{orderId}/start-preparation`, `PATCH /orders/{orderId}/items/{itemId}/ready`.
   - `chef` / `admin`: `GET /shifts/current`, `POST /shifts/open`, `POST /shifts/current/close`, `PATCH /orders/{orderId}/void-kitchen-error`.
   - `admin`: `PATCH /orders/{orderId}/void`.
3. Inspecciona directamente PostgreSQL y Redis:
   ```powershell
   # PostgreSQL: órdenes y eventos en outbox
   docker exec ordenes-postgres psql -U ordenes -d ordenes -c "SELECT public_id, shift_folio, table_number, status, total_amount, version FROM orders ORDER BY id;"
   docker exec ordenes-postgres psql -U ordenes -d ordenes -c "SELECT id, event_id, event_type, correlation_id, published_at FROM outbox ORDER BY id;"

   # Claves del catálogo en Redis
   docker exec ordenes-redis redis-cli keys "catalog:platillo:*"
   ```

#### Paso 6 — Pruebas End-to-End con RabbitMQ Real

1. **Inspeccionar exchanges y colas en RabbitMQ UI (`http://localhost:15672`, usuario `ordenes`, contraseña `ordenes`):**
   - **Exchanges (durables `topic`):** `ordenes.topic`, `inventory.topic`, `menu.topic`, `sala.events`.
   - **Colas (durables):**
     - `orders.inventory.inbound` (declarada por `workers`, enlazada a `inventory.reservation.*`, `inventario.stock.*`, `menu.#`, `sala.#`, `pagos.#`, `payments.#`, `pago.#`, etc.).
     - `stubs.orders.inbound` (declarada por `stubs`, enlazada a `ordenes.orden.creada`, `ordenes.orden.cancelada`, `ordenes.orden.expirada`, `ordenes.cuenta.solicitada`, `ordenes.cobro.solicitado`).

2. **Flujo 6A — Ciclo completo automático en RabbitMQ real (con `stubs` activo):**
   - Verifica que `ordenes-workers-ci` y `ordenes-stubs-ci` estén corriendo (Modo B) — o en Modo A levanta `python -m app.stubs.runner` en una 3ra terminal.
   - Confirma el borrador Folio `#4` (`CREATED`, Mesa 8) usando su `orderId` real impreso por `seed_demo.py`:
     ```powershell
     $WAITER_JWT = (python scripts/dev_token.py --role waiter | Select-String '^Bearer\s+(\S+)$').Matches[0].Groups[1].Value
     $ORDER_ID = "<orderId-real-del-folio-4>"
     Invoke-RestMethod -Method Post -Uri "http://localhost:8000/orders/$ORDER_ID/confirm" `
       -Headers @{ Authorization = "Bearer $WAITER_JWT" }
     ```
   - Traza del recorrido real por RabbitMQ:
     1. `OutboxWorker` publica `ordenes.orden.creada` en `ordenes.topic` y llena `outbox.published_at`.
     2. `ordenes-stubs-ci` (`InventoryStub`, por defecto `INVENTORY_STUB_MODE=RESERVE`) la consume y responde `inventario.stock.reservado`.
     3. `ordenes-workers-ci` (`InboundConsumerWorker`) consume `inventario.stock.reservado` de `orders.inventory.inbound`, registra el evento en `processed_events` y transiciona la orden a `RESERVED` (notificando en vivo a `/ws/kds`).
   - Verifica con `GET /orders/$ORDER_ID` (estado `RESERVED`) y logs: `docker compose -f docker-compose.yml -f docker-compose.ci.yml logs --tail=30 workers stubs`.
   - Prueba también `POST /orders/<orderId-real-del-folio-3>/request-bill` (orden `DELIVERED`): `BillingStub` (`BILLING_STUB_AUTO_PAY=true`) publica `pagos.pago.completado` y la orden pasa a `PAID`.
   - Puedes cambiar el comportamiento del stub con variables de entorno (`INVENTORY_STUB_MODE=RESERVE|REJECT|SILENT|DELAYED|ATOMIC`, `BILLING_STUB_AUTO_PAY=true|false`).

3. **Flujo 6B — Pruebas manuales con eventos reales SIN `stubs`:**
   - Detén `stubs` para que la Saga espere mensajes reales en el broker:
     ```powershell
     docker compose -f docker-compose.yml -f docker-compose.ci.yml stop stubs
     ```
   - Confirma la orden Folio `#5` (`CREATED`, Para llevar) con `POST /orders/<orderId-real-del-folio-5>/confirm`: permanecerá en `CREATED` esperando respuesta de Inventario.
   - En RabbitMQ UI (`http://localhost:15672`) → **Exchanges** → **`inventory.topic`** → **Publish message**:
     - Para **reservar** stock, usa **Routing key** = `inventario.stock.reservado` y **Payload**:
       ```json
       {
         "eventId": "<nuevo-uuid-v4>",
         "orderId": "<orderId-real-del-folio-5>",
         "correlationId": "<orderId-real-del-folio-5>",
         "reservations": [{"offerId": "OFFER-QUES-01", "quantity": 2}],
         "reservas": [{"offerId": "OFFER-QUES-01", "quantity": 2}]
       }
       ```
     - Para simular **stock insuficiente**, usa **Routing key** = `inventario.stock.insuficiente` y **Payload**:
       ```json
       {
         "eventId": "<nuevo-uuid-v4>",
         "orderId": "<orderId-real-de-la-orden>",
         "correlationId": "<orderId-real-de-la-orden>",
         "missingIngredients": ["pan"],
         "faltantes": ["pan"],
         "rejectedItems": [{"offerId": "OFFER-BURGER-01", "requestedQuantity": 1, "availableQuantity": 0}],
         "reservedItems": []
       }
       ```
     - Para simular **pago completado** sobre una orden `DELIVERED`, entra al exchange **`ordenes.topic`**, usa **Routing key** = `pagos.pago.completado` y **Payload**:
       ```json
       {
         "eventId": "<nuevo-uuid-v4>",
         "orderId": "<orderId-real-de-la-orden>",
         "sessionId": "00000000-0000-0000-0000-000000000099",
         "totalPaid": 330.0,
         "status": "PAID"
       }
       ```
   - Consulta `GET /orders/{orderId}` para confirmar la transición y revisa `processed_events` en PostgreSQL (si reenvías exactamente el mismo `eventId`, se descarta por idempotencia).
