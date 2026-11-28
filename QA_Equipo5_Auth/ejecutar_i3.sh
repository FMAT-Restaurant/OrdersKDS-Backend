#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

AUTH_REPOS_DIR="${AUTH_REPOS_DIR:-/tmp/fmat-auth-sut}"
AUTH_BACKEND_DIR="${AUTH_BACKEND_DIR:-$AUTH_REPOS_DIR/Auth-Backend}"
AUTH_FRONTEND_DIR="${AUTH_FRONTEND_DIR:-$AUTH_REPOS_DIR/Auth-Frontend}"
AUTH_DOCS_DIR="${AUTH_DOCS_DIR:-$AUTH_REPOS_DIR/Auth-Documentation}"

mkdir -p "$AUTH_REPOS_DIR" evidencias evidencias/gitleaks evidencias/static_reaudit

clone_if_missing() {
  local name="$1"
  local url="$2"
  local dest="$3"
  if [ ! -d "$dest/.git" ]; then
    echo "[QA-I3] Clonando $name en $dest..."
    git clone "$url" "$dest"
  else
    if [ -f "$dest/.git/shallow" ]; then
      echo "[QA-I3] Descompactando historial Git de $name para gitleaks..."
      git -C "$dest" fetch --unshallow || true
    fi
  fi
}

clone_if_missing "Auth-Backend" "https://github.com/FMAT-Restaurant/Auth-Backend.git" "$AUTH_BACKEND_DIR"
clone_if_missing "Auth-Frontend" "https://github.com/FMAT-Restaurant/Auth-Frontend.git" "$AUTH_FRONTEND_DIR"
clone_if_missing "Auth-Documentation" "https://github.com/FMAT-Restaurant/Auth-Documentation.git" "$AUTH_DOCS_DIR"

if [ ! -d "$SCRIPT_DIR/node_modules" ]; then
  echo "[QA-I3] Instalando dependencias de QA_Equipo5_Auth..."
  npm ci --no-audit --no-fund
fi

# Detectar si 5672/15672 ya está ocupado en host (ej. ordenes-rabbitmq) para aislar puertos del broker QA
if [ -z "${AUTH_RMQ_PORT:-}" ]; then
  if node -e "const n=require('net');const s=n.createConnection(5672,'127.0.0.1');s.on('connect',()=>{s.destroy();process.exit(0)});s.on('error',()=>process.exit(1));" 2>/dev/null; then
    export AUTH_RMQ_PORT=5673
    export AUTH_RMQ_MGMT_PORT=15673
    echo "[QA-I3] Puerto 5672 ocupado en host; mapeando fmat-auth-rabbitmq en localhost:5673 / 15673 (interno :5672)"
  else
    export AUTH_RMQ_PORT=5672
    export AUTH_RMQ_MGMT_PORT=15672
  fi
fi

# Secreto JWT efímero solo para ejecución local en Docker (nunca persistido en Git)
export JWT_SECRET="${JWT_SECRET:-$(node -e "console.log(require('crypto').randomBytes(32).toString('hex'))")}"
export BASE_URL="${BASE_URL:-http://localhost:4000}"
export PG_CONNECTION_STRING="${PG_CONNECTION_STRING:-postgresql://postgres:postgres@localhost:5433/fmat_auth}"
export RABBITMQ_AMQP_URL="${RABBITMQ_AMQP_URL:-amqp://guest:guest@localhost:${AUTH_RMQ_PORT}}"
export RABBITMQ_MGMT_URL="${RABBITMQ_MGMT_URL:-http://localhost:${AUTH_RMQ_MGMT_PORT}}"

TIMESTAMP_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
BE_COMMIT="$(git -C "$AUTH_BACKEND_DIR" rev-parse HEAD)"
FE_COMMIT="$(git -C "$AUTH_FRONTEND_DIR" rev-parse HEAD)"
DOC_COMMIT="$(git -C "$AUTH_DOCS_DIR" rev-parse HEAD)"
QA_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo "uncommitted")"

node -e "
const fs = require('fs');
fs.writeFileSync('evidencias/metadata_ejecucion.json', JSON.stringify({
  plan: 'FMAT-REST-EQ5-STP-EQ6-v1.0',
  iteracion: 'Iteracion 3',
  timestampUtc: process.argv[1],
  host: { node: process.version, platform: process.platform, arch: process.arch },
  repositorios: {
    'Auth-Backend': { commit: process.argv[2], branch: 'main' },
    'Auth-Frontend': { commit: process.argv[3], branch: 'main' },
    'Auth-Documentation': { commit: process.argv[4], branch: 'main' },
    'OrdersKDS-Backend-QA': { commit: process.argv[5] }
  }
}, null, 2));
" "$TIMESTAMP_UTC" "$BE_COMMIT" "$FE_COMMIT" "$DOC_COMMIT" "$QA_COMMIT"

echo "[QA-I3] Verificando red Docker restaurant-network..."
docker network inspect restaurant-network >/dev/null 2>&1 || docker network create restaurant-network

COMPOSE_FILES=(-f "$AUTH_BACKEND_DIR/docker-compose.yml" -f "$SCRIPT_DIR/docker-compose.i3.yml")
QA_COMPOSE_PROJECT="${QA_COMPOSE_PROJECT:-qa-auth-i3-isolated}"

echo "[QA-I3] Recreando stack aislado con tmpfs (proyecto=$QA_COMPOSE_PROJECT, sin modificar volúmenes Docker existentes)..."
docker compose -p "$QA_COMPOSE_PROJECT" "${COMPOSE_FILES[@]}" down --remove-orphans
docker compose -p "$QA_COMPOSE_PROJECT" "${COMPOSE_FILES[@]}" up -d --build

echo "[QA-I3] Esperando healthcheck de Auth-Backend (http://localhost:4000/health)..."
for i in $(seq 1 40); do
  if node -e "
    fetch('$BASE_URL/health')
      .then(r => r.json())
      .then(d => {
        if (d && d.status === 'healthy' && d.dependencies && d.dependencies.rabbitmq === 'connected') {
          console.log('HEALTH_OK', JSON.stringify(d));
          process.exit(0);
        }
        process.exit(1);
      })
      .catch(() => process.exit(1));
  "; then
    break
  fi
  if [ "$i" -eq 40 ]; then
    echo "[QA-I3] ERROR: Auth-Backend no alcanzó estado healthy con RabbitMQ connected" >&2
    docker compose "${COMPOSE_FILES[@]}" logs --tail=80 >&2
    exit 1
  fi
  sleep 2
done

echo "[QA-I3] Fase 1: Bootstrap concurrente (CP-AUTH-01) desde BD vacía..."
node scripts/run_security_and_contracts.mjs --phase=bootstrap

echo "[QA-I3] Fase 2: Sembrado determinista (seed_qa_auth.mjs)..."
node scripts/seed_qa_auth.mjs

MESERO_ID="$(node -e "const s=require('./evidencias/seed_summary.json'); console.log(s.staff.mesero.staffId)")"
INACTIVE_ID="$(node -e "const s=require('./evidencias/seed_summary.json'); console.log(s.staff.inactiveOne.staffId)")"
CUSTOM_PBAC_ID="$(node -e "const s=require('./evidencias/seed_summary.json'); console.log(s.staff.customPbac.staffId)")"

echo "[QA-I3] Fase 3: Ejecución Postman + Newman (contratos ajv + htmlextra)..."
npx newman run postman/auth_qa_e5.postman_collection.json \
  -e postman/auth_local_docker.postman_environment.json \
  --env-var "baseUrl=$BASE_URL" \
  --env-var "meseroStaffId=$MESERO_ID" \
  --env-var "inactiveStaffId=$INACTIVE_ID" \
  --env-var "customRoleStaffId=$CUSTOM_PBAC_ID" \
  -r cli,htmlextra,json \
  --reporter-htmlextra-export evidencias/newman-report.html \
  --reporter-json-export evidencias/newman-report.json \
  --suppress-exit-code

node scripts/sanitize_reports.mjs

echo "[QA-I3] Fase 4: Abuse Cases de seguridad, anti-enumeración, RabbitMQ en vivo y registro.json por caso..."
node scripts/run_security_and_contracts.mjs --phase=full

echo "[QA-I3] Fase 5: Inspección SQL directa en PostgreSQL (fmat_auth)..."
SQL_RC=0
node scripts/verify_db_audit.mjs || SQL_RC=$?

echo "[QA-I3] Fase 6: Escenarios BDD ejecutables en Cucumber/Gherkin..."
BDD_RC=0
npx cucumber-js --config cucumber.mjs || BDD_RC=$?

echo "[QA-I3] Fase 7: Auditoría SonarCloud + gitleaks..."
node scripts/collect_static_sonar_audit.mjs

if docker image inspect ghcr.io/gitleaks/gitleaks:latest >/dev/null 2>&1 || docker pull ghcr.io/gitleaks/gitleaks:latest >/dev/null 2>&1; then
  docker run --rm -v "$AUTH_BACKEND_DIR:/repo:ro" -v "$SCRIPT_DIR/evidencias/gitleaks:/out" \
    ghcr.io/gitleaks/gitleaks:latest git /repo --redact --report-format=json --report-path=/out/gitleaks_Auth-Backend.json --exit-code 0 > evidencias/gitleaks/gitleaks_Auth-Backend.log 2>&1 || true
  docker run --rm -v "$AUTH_FRONTEND_DIR:/repo:ro" -v "$SCRIPT_DIR/evidencias/gitleaks:/out" \
    ghcr.io/gitleaks/gitleaks:latest git /repo --redact --report-format=json --report-path=/out/gitleaks_Auth-Frontend.json --exit-code 0 > evidencias/gitleaks/gitleaks_Auth-Frontend.log 2>&1 || true
  docker run --rm -v "$SCRIPT_DIR/scripts:/repo/scripts:ro" -v "$SCRIPT_DIR/postman:/repo/postman:ro" -v "$SCRIPT_DIR/bdd:/repo/bdd:ro" -v "$SCRIPT_DIR/evidencias/gitleaks:/out" \
    ghcr.io/gitleaks/gitleaks:latest dir /repo --redact --report-format=json --report-path=/out/gitleaks_QA_Equipo5_Auth.json --exit-code 0 > evidencias/gitleaks/gitleaks_QA_Equipo5_Auth.log 2>&1 || true
fi

node scripts/sanitize_reports.mjs

echo "[QA-I3] Deteniendo stack temporal aislado ($QA_COMPOSE_PROJECT)..."
docker compose -p "$QA_COMPOSE_PROJECT" "${COMPOSE_FILES[@]}" down --remove-orphans

echo "[QA-I3] Ejecución Iteración 3 completada (SQL_RC=$SQL_RC, BDD_RC=$BDD_RC). Evidencias guardadas en $SCRIPT_DIR/evidencias/"
