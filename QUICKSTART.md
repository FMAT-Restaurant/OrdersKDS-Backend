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

## §5 — Local development in one shot

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
RabbitMQ UI: `http://localhost:15672`

