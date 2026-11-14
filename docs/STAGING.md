# Shared staging environment

This document defines the reproducible integration environment for INT-02..06
and INT-08 (TASK-38, RNF-10, RNF-03). The selected hosting model is a
team-managed Linux VM with Docker Engine and a restricted firewall. It keeps
the public API and broker reachable by the other microservices while keeping
PostgreSQL, Redis, and the RabbitMQ management UI private to the VM.

## Architecture and access

| Component | Staging endpoint | Access policy |
|---|---|---|
| Orders API | `https://staging.<team-domain>/` (VM port `8000`) | Team and integration services |
| PostgreSQL | VM `127.0.0.1:15432` through an SSH tunnel | DBeaver/pgAdmin read-only user |
| RabbitMQ AMQP | VM port `5672` or private network | Integration services only |
| RabbitMQ management | VM `127.0.0.1:15672` through SSH tunnel | Maintainers only |
| Redis | Docker network only | API and workers only |

The hostname and public API port are deployment values, not credentials. The
VM firewall must allow API traffic and the broker only from the integration
network; PostgreSQL and management must not be exposed publicly.

The final `main` environment uses a separate VM/database/volumes and separate
GitHub Environment secrets. No staging database or credential is reused for
production.

## Image deployment

The backend CI publishes:

```text
ghcr.io/fmat-restaurant/orderskds-backend:develop
ghcr.io/fmat-restaurant/orderskds-backend:main
ghcr.io/fmat-restaurant/orderskds-backend:sha-<commit>
```

The `develop` tag is the staging candidate. On the staging VM, a maintainer
copies [`docker-compose.staging.yml`](../docker-compose.staging.yml),
[`ops/staging/postgres-init.sh`](../ops/staging/postgres-init.sh), and
`.env.staging` (created from `.env.staging.example`) and runs:

```bash
docker login ghcr.io
docker compose --env-file .env.staging -f docker-compose.staging.yml pull
docker compose --env-file .env.staging -f docker-compose.staging.yml up -d postgres redis rabbitmq
docker compose --env-file .env.staging -f docker-compose.staging.yml --profile migrate run --rm migrate
docker compose --env-file .env.staging -f docker-compose.staging.yml up -d api workers
docker compose --env-file .env.staging -f docker-compose.staging.yml --profile smoke run --rm smoke
```

The image registry token is supplied interactively or by the deployment
runner. It is never stored in this repository.

## PostgreSQL ownership and migrations

The database is `ordenes_staging` and is initialized with three distinct
roles:

- `POSTGRES_APP_USER`: runtime API/worker role. It can read and modify
  application tables and sequences but cannot create schema objects.
- `POSTGRES_MIGRATION_USER`: release operator role. It is used only by the
  `migrate` profile to run `alembic upgrade head`.
- `POSTGRES_READONLY_USER`: DBeaver/pgAdmin role with `SELECT` only.

Only the release operator runs migrations, after the infrastructure health
checks and before starting the new API image. This includes the migrations
for `OrderStatus.RESERVED`, `stock_reserved_at`, `rejection_details`, and the
`1:N` `dining_session_id` index. The init script runs only when the volume is
created; changing a role password later must be done by the VM maintainer.

For a read-only DBeaver connection, create an SSH tunnel:

```bash
ssh -N -L 15432:127.0.0.1:5432 <vm-user>@<staging-host>
```

Then use host `127.0.0.1`, port `15432`, database `ordenes_staging`, and
`POSTGRES_READONLY_USER`. The production database is never used for this
connection.

## GitHub Environments and secrets

Create protected GitHub Environments named `staging` and `production`, with
required reviewers and branch restrictions. Store values only as environment
secrets:

| Secret | Purpose |
|---|---|
| `STAGING_SSH_HOST`, `STAGING_SSH_USER`, `STAGING_SSH_PRIVATE_KEY` | VM deployment |
| `STAGING_GHCR_TOKEN` | Pull the private GHCR image |
| `STAGING_API_URL` | URL used by integration tests |
| `STAGING_DATABASE_URL_READONLY` | Optional masked DBeaver handoff |
| `PRODUCTION_*` equivalents | Separate release environment |

Database passwords, RabbitMQ credentials, Redis password, JWT material, and
SSH keys must be environment secrets or VM secret files. They must not be
written to Compose, README files, logs, PR bodies, or workflow command output.

## Smoke evidence

The `smoke` Compose profile verifies `/health` and a RabbitMQ publish/consume
round trip. A maintainer records the command output and the timestamp in the
S6 integration evidence. The DBeaver/pgAdmin connection is verified with the
read-only role by running:

```sql
SELECT current_database(), current_user;
SELECT has_schema_privilege(current_user, 'public', 'USAGE');
```

An attempted `CREATE TABLE` must fail for the read-only user. This proves the
shared database is usable without allowing accidental test-data mutation.
