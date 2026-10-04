# QUICKSTART — Orders & KDS Backend

- [English Version](#english-version)
- [Versión en Español](#versión-en-español)

---

## English Version

Quick reference for the CI/CD standard required by the course.
Full setup instructions are in [README.md](README.md); the contributing
workflow is in [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).  A basic guide
(in Spanish) on how Docker works locally and the cloud scope (TASK-38) is in
[docs/GUIA_DOCKER.md](docs/GUIA_DOCKER.md).

---

### §1 — Repository prerequisites

Before the CI/CD pipeline can run successfully, a maintainer must configure
the following once per repository.

#### 1.1 GitHub Actions permissions

`Settings → Actions → General → Workflow permissions`

Select **Read and write permissions** and save.  This is required by the
`build-and-push` job in `ci.yml`, which publishes images to GHCR and needs
`packages: write`.

#### 1.2 Secrets

`Settings → Secrets and variables → Actions → New repository secret`

| Secret name | Where to get it | Required by |
|---|---|---|
| `SONAR_TOKEN` | SonarCloud → My Account → Security → Generate Token (type: **User Token**) | `sonarcloud` job |
| `DISCORD_WEBHOOK` | Discord → Channel Settings → Integrations → Webhooks → Copy URL | `notify-on-failure` and `build-and-push` jobs |

`GITHUB_TOKEN` is injected automatically by GitHub Actions — **do not create it**.

#### 1.3 Required CI/CD environment variables (for integration tests)

Add these as **Actions secrets** as well if you want the integration-tests or
zap-api-scan jobs to use a real database:

| Secret name | Example value |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://ordenes:ordenes@postgres:5432/ordenes` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `RABBITMQ_URL` | `amqp://ordenes:ordenes@rabbitmq:5672/` |
| `SAGA_TIMEOUT_SECONDS` | `60` (agree with the team — OP-01) |

---

### §2 — Branch protection

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

### §3 — CI workflow overview

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

### §4 — Diagnosing a failing job

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

### §5 — Machine requirements (install once per developer)

Every team member needs the tools below **before** running the bootstrap
script.  PostgreSQL, Redis and RabbitMQ are **not** installed on your machine:
they run inside Docker containers defined in `docker-compose.yml`.

#### 5.1 Required software

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

#### 5.2 Windows-specific notes

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

#### 5.3 Verify the installation

```powershell
git --version              # 2.40 or newer
docker --version           # Docker version 24 or newer
docker compose version     # Docker Compose version v2.x
docker run --rm hello-world   # confirms the Docker engine is running
python --version           # Python 3.12 or newer
node --version             # v20.x (frontend only)
```

#### 5.4 Ports that must be free

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

### §6 — Local development in one shot

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

#### 6.1 Verify that the infrastructure is healthy

```powershell
docker compose ps
# postgres, redis and rabbitmq must show STATUS "Up ... (healthy)"

docker exec ordenes-postgres psql -U ordenes -d ordenes -c "\dt"   # lists tables after alembic upgrade head
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
| Service refuses to start: `SAGA_TIMEOUT_SECONDS` missing | Variable intentionally has no default (OP-01) | Set it in `.env` |
| Integration tests fail to start containers | Docker not running (Testcontainers needs it) | Start Docker Desktop |

---

### §7 — Browsing the database with a visual client

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

#### Databases per environment

| Environment | Where it runs | Who uses it | How to connect |
|---|---|---|---|
| **Local development** | Your own Docker container | You only; `docker compose down -v` resets it | Table above |
| **Automated tests** | Ephemeral Testcontainers (created and destroyed by `pytest tests/integration` and CI) | Test code | Nothing to connect to; never share it |
| **Shared testing / staging** | Shared host defined in **TASK-38** | The whole team and the integration tests with other microservices | Host, port and credentials provided by the Scrum Master (never commit them) |
| **Final version (release)** | Deployed from `main` | The released system | Restricted credentials; not used for experiments |

> Use the same client to inspect the shared staging database by creating a
> second connection.  Prefer **read-only credentials** for it so nobody alters
> shared test data by accident.

---
---

## Versión en Español

Referencia rápida para el estándar de CI/CD requerido por el curso.
Las instrucciones completas de configuración están en [README.md](README.md); el flujo de contribución está en [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).
Una guía básica sobre cómo funciona Docker en local y su alcance en la nube (TASK-38) se encuentra en [docs/GUIA_DOCKER.md](docs/GUIA_DOCKER.md).

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
| `DISCORD_WEBHOOK` | Discord → Ajustes del canal → Integraciones → Webhooks → Copiar URL | Jobs `notify-on-failure` y `build-and-push` |

`GITHUB_TOKEN` es inyectado automáticamente por GitHub Actions — **no lo crees**.

#### 1.3 Variables de entorno CI/CD requeridas (para pruebas de integración)

Añade estas también como **Actions secrets** si quieres que los jobs `integration-tests` o `zap-api-scan` usen una base de datos real:

| Nombre del secreto | Valor de ejemplo |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://ordenes:ordenes@postgres:5432/ordenes` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `RABBITMQ_URL` | `amqp://ordenes:ordenes@rabbitmq:5672/` |
| `SAGA_TIMEOUT_SECONDS` | `60` (acordar con el equipo — OP-01) |

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
        ├─ lint-and-types        — gitleaks, ruff, mypy --strict
        │
        └─ unit-tests            — pytest tests/unit, sube coverage.xml + junit.xml
               │
               ├─ sonarcloud     — descarga artefactos, SonarCloud + Quality Gate
               │
               └─ integration-tests — pytest tests/integration (Testcontainers)
                        │
                        ├─ api-and-contract  — Newman (vacío hasta TASK-34)
                        │
                        └─ zap-api-scan      — OWASP ZAP contra /openapi.json

Se ejecuta tras cualquier fallo:
        notify-on-failure        — Alerta por webhook a Discord

Se ejecuta solo al hacer push a main (tras pasar todos los checks):
        build-and-push           — Imagen Docker → ghcr.io, Notificación a Discord
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
| `gitleaks` falla | Secreta detectada en un commit | Rotar el secreto inmediatamente; ver CONTRIBUTING §12 |
| `zap-api-scan` falla (ALTO)| Problema de seguridad en la API | Revisar `.zap/rules.tsv` y arreglar la vulnerabilidad |

---

### §5 — Requerimientos de la máquina (instalar una vez por desarrollador)

Cada miembro del equipo necesita las siguientes herramientas **antes** de ejecutar el script de inicialización. PostgreSQL, Redis y RabbitMQ **no** se instalan en tu máquina: corren dentro de contenedores Docker definidos en `docker-compose.yml`.

#### 5.1 Software requerido

| Herramienta | Versión mínima | Usado para | Descarga |
|---|---|---|---|
| **Git** | 2.40+ | Clonar, ramas, commits | https://git-scm.com/downloads |
| **Docker Desktop** (incluye Docker Compose v2) | Docker 24+ / Compose v2 | PostgreSQL, Redis, RabbitMQ y Testcontainers | https://www.docker.com/products/docker-desktop |
| **Python** | 3.12+ | Backend, Alembic, tests | https://www.python.org/downloads/ |
| **Node.js** + **pnpm** | Node 20 LTS | Solo repo Frontend (`ordenes-kds-frontend`) | https://nodejs.org · `corepack enable` |
| **Cliente BD** (recomendado)| — | Explorar PostgreSQL visualmente (ver §7) | https://dbeaver.io/download/ o https://www.pgadmin.org/download/ |
| *k6*, *Newman* (opcional) | — | Pruebas de rendimiento / contrato de API (S5–S6)| `npm i -g newman` · https://k6.io/docs/get-started/installation/ |

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
node --version             # v20.x (solo frontend)
```

#### 5.4 Puertos que deben estar libres

| Puerto | Servicio |
|---|---|
| `5432` | PostgreSQL |
| `6379` | Redis |
| `5672` | RabbitMQ (AMQP) |
| `15672` | RabbitMQ interfaz de administración |
| `8000` | API de Órdenes (uvicorn) |

Si uno está ocupado (típicamente el `5432` por un servidor local de PostgreSQL), detén ese proceso o cambia el lado **izquierdo** del mapeo en `docker-compose.yml` (ej. `"5433:5432"`) y actualiza el puerto en tu `.env`.

---

### §6 — Desarrollo local de un tirón

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

El script de bootstrap:
1. Verifica Python 3.12+
2. Crea `.venv` e instala `requirements/dev.txt`
3. Genera `.env` con defaults locales seguros (credenciales de docker-compose)
4. Imprime los pasos restantes (editar `.env`, Docker, migraciones, API)

Después de que el script termine:

```bash
# Activa el entorno virtual (requerido para alembic, uvicorn, pytest, etc.)
# Windows:   .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate

# Edita .env — define SAGA_TIMEOUT_SECONDS antes de iniciar el servicio
# (punto abierto OP-01: acordar el valor con el equipo)

docker compose up -d postgres redis rabbitmq
alembic upgrade head

# Terminal 1 — API con recarga en caliente
uvicorn app.main:app --reload --port 8000

# Terminal 2 — workers en segundo plano
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

docker exec ordenes-postgres psql -U ordenes -d ordenes -c "\dt"   # lista tablas tras alembic upgrade head
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
| Servicio rechaza arrancar: falta `SAGA_TIMEOUT_SECONDS` | Variable intencionalmente sin default | Establécela en `.env` |
| Falla arranque de tests de integración | Docker no corre (Testcontainers lo necesita) | Inicia Docker Desktop |

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
| **Pruebas compartidas / staging** | Host compartido definido en **TASK-38** | Todo el equipo y tests de integración con otros MS | Host, puerto y credenciales provistas por el Scrum Master (nunca las comitees) |
| **Versión final (release)** | Desplegada desde `main` | El sistema liberado | Credenciales restringidas; no se usa para experimentos |

> Usa el mismo cliente para inspeccionar la base de staging compartida creando una segunda conexión. Prefiere **credenciales de solo lectura** para que nadie altere los datos de prueba compartidos por accidente.
