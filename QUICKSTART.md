# QUICKSTART — Orders & KDS Backend

Quick reference for the CI/CD standard required by the course.
Full setup instructions are in [README.md](README.md); the contributing
workflow is in [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

---

## §1 — Repository prerequisites

Before the CI/CD pipeline can run successfully, a maintainer must configure
the following once per repository.

### 1.1 GitHub Actions permissions

`Settings → Actions → General → Workflow permissions`

Select **Read and write permissions** and save.  This is required by the
`build-and-push` job in `ci.yml`, which publishes images to GHCR and needs
`packages: write`.

### 1.2 Secrets

`Settings → Secrets and variables → Actions → New repository secret`

| Secret name | Where to get it | Required by |
|---|---|---|
| `SONAR_TOKEN` | SonarCloud → My Account → Security → Generate Token (type: **User Token**) | `sonarcloud` job |
| `DISCORD_WEBHOOK` | Discord → Channel Settings → Integrations → Webhooks → Copy URL | `notify-on-failure` and `build-and-push` jobs |

`GITHUB_TOKEN` is injected automatically by GitHub Actions — **do not create it**.

### 1.3 Required CI/CD environment variables (for integration tests)

Add these as **Actions secrets** as well if you want the integration-tests or
zap-api-scan jobs to use a real database:

| Secret name | Example value |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://ordenes:ordenes@postgres:5432/ordenes` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `RABBITMQ_URL` | `amqp://ordenes:ordenes@rabbitmq:5672/` |
| `SAGA_TIMEOUT_SECONDS` | `60` (agree with the team — OP-01) |

---

## §2 — Branch protection

Branch protection is configured in `Settings → Branches` (or Rulesets) for
**both** `develop` and `main`.  The full rule set is documented in
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
zap-api-scan
```

> **Note:** Status check names only appear in GitHub's selector after the
> workflow has run at least once on that branch.  Mark them after
> TASK-01/TASK-02 complete their first successful run.

Also enable **Automatically delete head branches** in
`Settings → General → Pull Requests`.

---

## §3 — CI workflow overview

File: `.github/workflows/ci.yml`

```
push/PR to develop or main
        │
        ├─ validate-pr          (PR only) — branch name + Conventional Commits title
        ├─ lint-and-types        — gitleaks, ruff, mypy --strict
        │
        └─ unit-tests            — pytest tests/unit, uploads coverage.xml + junit.xml
               │
               ├─ sonarcloud     — downloads artifacts, SonarCloud + Quality Gate
               │
               └─ integration-tests — pytest tests/integration (Testcontainers)
                        │
                        ├─ api-and-contract  — Newman (empty until TASK-34)
                        │
                        └─ zap-api-scan      — OWASP ZAP against /openapi.json

Runs after any failure:
        notify-on-failure        — Discord webhook alert

Runs only on push to main (after all checks pass):
        build-and-push           — Docker image → ghcr.io, Discord notification
```

---

## §4 — Diagnosing a failing job

1. Go to **Actions** (tab at the top of the repository).
2. Click the failing workflow run.
3. Click the red ❌ job name in the left panel.
4. Expand the failing step to read the log.
5. For coverage and test reports: scroll to the bottom of the run page and
   open the **Artifacts** section.  Download `unit-test-results` and open
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

## §5 — Machine requirements (install once per developer)

Every team member needs the tools below **before** running the bootstrap
script.  PostgreSQL, Redis and RabbitMQ are **not** installed on your machine:
they run inside Docker containers defined in `docker-compose.yml`.

### 5.1 Required software

| Tool | Minimum version | Used for | Download |
|---|---|---|---|
| **Git** | 2.40+ | Clone, branches, commits | https://git-scm.com/downloads |
| **Docker Desktop** (includes Docker Compose v2) | Docker 24+ / Compose v2 | PostgreSQL, Redis, RabbitMQ and Testcontainers | https://www.docker.com/products/docker-desktop |
| **Python** | 3.12+ | Backend, Alembic, tests | https://www.python.org/downloads/ |
| **Node.js** + **pnpm** | Node 20 LTS | Frontend repo (`ordenes-kds-frontend`) only | https://nodejs.org · `corepack enable` |
| **DB client** (recommended) | — | Browse and query PostgreSQL visually (see §7) | https://dbeaver.io/download/ or https://www.pgadmin.org/download/ |
| *k6*, *Newman* (optional) | — | Performance / API contract tests (S5–S6) | `npm i -g newman` · https://k6.io/docs/get-started/installation/ |

> **Do not install a local PostgreSQL server.**  The containerised database is
> the single source of truth for everybody, uses the exact version of CI
> (PostgreSQL 16), and a local server would collide with port `5432`.
> Install a *client* instead (§7).

### 5.2 Windows-specific notes

1. Docker Desktop requires **WSL 2**.  If it asks for it, run in an
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

### 5.3 Verify the installation

```powershell
git --version              # 2.40 or newer
docker --version           # Docker version 24 or newer
docker compose version     # Docker Compose version v2.x
docker run --rm hello-world   # confirms the Docker engine is running
python --version           # Python 3.12 or newer
node --version             # v20.x (frontend only)
```

### 5.4 Ports that must be free

| Port | Service |
|---|---|
| `5432` | PostgreSQL |
| `6379` | Redis |
| `5672` | RabbitMQ (AMQP) |
| `15672` | RabbitMQ management UI |
| `8000` | Orders API (uvicorn) |

If one is taken (typically `5432` by a local PostgreSQL server), stop that
process or change the **left** side of the mapping in `docker-compose.yml`
(e.g. `"5433:5432"`) and update the port in your `.env`.

---

## §6 — Local development in one shot

**Windows PowerShell:**

```powershell
git clone https://github.com/<org>/ordenes-kds-backend.git
cd ordenes-kds-backend
.\scripts\bootstrap.ps1
```

**macOS / Linux:**

```bash
git clone https://github.com/<org>/ordenes-kds-backend.git
cd ordenes-kds-backend
bash scripts/bootstrap.sh
```

The bootstrap script:
1. Verifies Python 3.12+
2. Creates `.venv` and installs `requirements/dev.txt`
3. Generates `.env` with safe local defaults (docker-compose credentials)
4. Prints the remaining steps (edit `.env`, Docker, migrations, API)

After the script finishes:

```bash
# Activate the venv (required for alembic, uvicorn, pytest, etc.)
# Windows:   .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate

# Edit .env — set SAGA_TIMEOUT_SECONDS before starting the service
# (open point OP-01: agree the value with the team)

docker compose up -d postgres redis rabbitmq
alembic upgrade head

# Terminal 1 — API with hot reload
uvicorn app.main:app --reload --port 8000

# Terminal 2 — background workers
python -m app.workers.run_all

# Generate a dev JWT for manual API testing
python scripts/dev_token.py --role waiter
```

API available at: `http://localhost:8000`  
OpenAPI docs: `http://localhost:8000/docs`  
RabbitMQ UI: `http://localhost:15672` (user `ordenes`, password `ordenes`)

### 6.1 Verify that the infrastructure is healthy

```powershell
docker compose ps
# postgres, redis and rabbitmq must show STATUS "Up ... (healthy)"

docker exec ordenes-postgres psql -U ordenes -d ordenes -c "\dt"   # lists tables after alembic upgrade head
docker exec ordenes-redis redis-cli ping                          # PONG
```

The RabbitMQ container takes up to ~30 s to become *healthy* the first time.

### 6.2 Daily routine

| Goal | Command |
|---|---|
| Start the infrastructure | `docker compose up -d postgres redis rabbitmq` |
| Stop it keeping the data | `docker compose stop` |
| Remove containers keeping the data | `docker compose down` |
| **Reset everything** (deletes the local DB, cache and queues) | `docker compose down -v` then `docker compose up -d` and `alembic upgrade head` |
| Follow a service log | `docker compose logs -f rabbitmq` |
| Apply new migrations after a `git pull` | `alembic upgrade head` |

### 6.3 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker: error during connect` / `cannot connect to the Docker daemon` | Docker Desktop is not running | Open Docker Desktop and wait for *Engine running* |
| `port is already allocated` | Another process uses `5432`, `6379`, `5672` or `15672` | See §5.4 |
| `Activate.ps1 cannot be loaded` | PowerShell execution policy | See §5.2 (step 3) |
| `connection refused` on `localhost:5432` | Container not healthy yet | `docker compose ps`; wait for *healthy* |
| `password authentication failed for user "ordenes"` | `.env` credentials differ from the compose file, or the volume was created with other credentials | Align `.env` or run `docker compose down -v` |
| Service refuses to start: `SAGA_TIMEOUT_SECONDS` missing | Variable intentionally has no default (OP-01) | Set it in `.env` |
| Integration tests fail to start containers | Docker not running (Testcontainers needs it) | Start Docker Desktop |

---

## §7 — Browsing the database with a visual client

The database lives in the `ordenes-postgres` container.  You do not need a
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
time).  In pgAdmin: *Register → Server*.

### Databases per environment

| Environment | Where it runs | Who uses it | How to connect |
|---|---|---|---|
| **Local development** | Your own Docker container | You only; `docker compose down -v` resets it | Table above |
| **Automated tests** | Ephemeral Testcontainers (created and destroyed by `pytest tests/integration` and CI) | Test code | Nothing to connect to; never share it |
| **Shared testing / staging** | Shared host defined in **TASK-38** | The whole team and the integration tests with other microservices | Host, port and credentials provided by the Scrum Master (never commit them) |
| **Final version (release)** | Deployed from `main` | The released system | Restricted credentials; not used for experiments |

> Use the same client to inspect the shared staging database by creating a
> second connection.  Prefer **read-only credentials** for it so nobody alters
> shared test data by accident.

