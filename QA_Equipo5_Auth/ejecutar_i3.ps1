$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$AuthReposDir = if ($env:AUTH_REPOS_DIR) { $env:AUTH_REPOS_DIR } else { Join-Path $env:TEMP "fmat-auth-sut" }
$AuthBackendDir = if ($env:AUTH_BACKEND_DIR) { $env:AUTH_BACKEND_DIR } else { Join-Path $AuthReposDir "Auth-Backend" }
$AuthFrontendDir = if ($env:AUTH_FRONTEND_DIR) { $env:AUTH_FRONTEND_DIR } else { Join-Path $AuthReposDir "Auth-Frontend" }
$AuthDocsDir = if ($env:AUTH_DOCS_DIR) { $env:AUTH_DOCS_DIR } else { Join-Path $AuthReposDir "Auth-Documentation" }

New-Item -ItemType Directory -Force -Path $AuthReposDir, "evidencias", "evidencias/gitleaks", "evidencias/static_reaudit" | Out-Null

function Ensure-Repo($Name, $Url, $Dest) {
    if (-not (Test-Path (Join-Path $Dest ".git"))) {
        Write-Host "[QA-I3] Clonando $Name en $Dest..."
        git clone $Url $Dest
    } elseif (Test-Path (Join-Path $Dest ".git/shallow")) {
        git -C $Dest fetch --unshallow
    }
}

Ensure-Repo "Auth-Backend" "https://github.com/FMAT-Restaurant/Auth-Backend.git" $AuthBackendDir
Ensure-Repo "Auth-Frontend" "https://github.com/FMAT-Restaurant/Auth-Frontend.git" $AuthFrontendDir
Ensure-Repo "Auth-Documentation" "https://github.com/FMAT-Restaurant/Auth-Documentation.git" $AuthDocsDir

if (-not (Test-Path (Join-Path $ScriptDir "node_modules"))) {
    Write-Host "[QA-I3] Instalando dependencias de QA_Equipo5_Auth..."
    npm ci --no-audit --no-fund
}

if (-not $env:AUTH_RMQ_PORT) {
    $rmqBusy = $false
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient("127.0.0.1", 5672)
        $tcp.Close()
        $rmqBusy = $true
    } catch {}
    if ($rmqBusy) {
        $env:AUTH_RMQ_PORT = "5673"
        $env:AUTH_RMQ_MGMT_PORT = "15673"
        Write-Host "[QA-I3] Puerto 5672 ocupado en host; mapeando fmat-auth-rabbitmq en localhost:5673 / 15673"
    } else {
        $env:AUTH_RMQ_PORT = "5672"
        $env:AUTH_RMQ_MGMT_PORT = "15672"
    }
}
if (-not $env:JWT_SECRET) {
    $env:JWT_SECRET = node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
}
if (-not $env:BASE_URL) { $env:BASE_URL = "http://localhost:4000" }
if (-not $env:PG_CONNECTION_STRING) { $env:PG_CONNECTION_STRING = "postgresql://postgres:postgres@localhost:5433/fmat_auth" }
if (-not $env:RABBITMQ_AMQP_URL) { $env:RABBITMQ_AMQP_URL = "amqp://guest:guest@localhost:$($env:AUTH_RMQ_PORT)" }
if (-not $env:RABBITMQ_MGMT_URL) { $env:RABBITMQ_MGMT_URL = "http://localhost:$($env:AUTH_RMQ_MGMT_PORT)" }

$TimestampUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$BeCommit = (git -C $AuthBackendDir rev-parse HEAD).Trim()
$FeCommit = (git -C $AuthFrontendDir rev-parse HEAD).Trim()
$DocCommit = (git -C $AuthDocsDir rev-parse HEAD).Trim()
$QaCommit = try { (git rev-parse HEAD).Trim() } catch { "uncommitted" }

node -e "const fs=require('fs');fs.writeFileSync('evidencias/metadata_ejecucion.json',JSON.stringify({plan:'FMAT-REST-EQ5-STP-EQ6-v1.0',iteracion:'Iteracion 3',timestampUtc:process.argv[1],repositorios:{'Auth-Backend':{commit:process.argv[2],branch:'main'},'Auth-Frontend':{commit:process.argv[3],branch:'main'},'Auth-Documentation':{commit:process.argv[4],branch:'main'},'OrdersKDS-Backend-QA':{commit:process.argv[5]}}},null,2));" $TimestampUtc $BeCommit $FeCommit $DocCommit $QaCommit

docker network inspect restaurant-network 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { docker network create restaurant-network | Out-Null }

$ComposeArgs = @("-p", "qa-auth-i3-isolated", "-f", (Join-Path $AuthBackendDir "docker-compose.yml"), "-f", (Join-Path $ScriptDir "docker-compose.i3.yml"))

Write-Host "[QA-I3] Recreando stack aislado con tmpfs (sin tocar volúmenes existentes)..."
docker compose @ComposeArgs down --remove-orphans
docker compose @ComposeArgs up -d --build

Write-Host "[QA-I3] Esperando healthcheck de Auth-Backend..."
$Healthy = $false
for ($i = 0; $i -lt 40; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri "$($env:BASE_URL)/health" -TimeoutSec 3
        if ($resp.status -eq "healthy" -and $resp.dependencies.rabbitmq -eq "connected") {
            $Healthy = $true
            break
        }
    } catch {}
    Start-Sleep -Seconds 2
}
if (-not $Healthy) {
    throw "Auth-Backend no alcanzo estado healthy con RabbitMQ connected"
}

node scripts/run_security_and_contracts.mjs --phase=bootstrap
node scripts/seed_qa_auth.mjs

$SeedJson = Get-Content -Raw "evidencias/seed_summary.json" | ConvertFrom-Json

npx newman run postman/auth_qa_e5.postman_collection.json `
  -e postman/auth_local_docker.postman_environment.json `
  --env-var "baseUrl=$($env:BASE_URL)" `
  --env-var "meseroStaffId=$($SeedJson.staff.mesero.staffId)" `
  --env-var "inactiveStaffId=$($SeedJson.staff.inactiveOne.staffId)" `
  --env-var "customRoleStaffId=$($SeedJson.staff.customPbac.staffId)" `
  -r "cli,htmlextra,json" `
  --reporter-htmlextra-export evidencias/newman-report.html `
  --reporter-json-export evidencias/newman-report.json `
  --suppress-exit-code

node scripts/sanitize_reports.mjs
docker compose @ComposeArgs down --remove-orphans

node scripts/run_security_and_contracts.mjs --phase=full
node scripts/verify_db_audit.mjs
npx cucumber-js --config cucumber.mjs
node scripts/collect_static_sonar_audit.mjs

docker run --rm -v "${AuthBackendDir}:/repo:ro" -v "${ScriptDir}/evidencias/gitleaks:/out" ghcr.io/gitleaks/gitleaks:latest git /repo --redact --report-format=json --report-path=/out/gitleaks_Auth-Backend.json --exit-code 0
docker run --rm -v "${AuthFrontendDir}:/repo:ro" -v "${ScriptDir}/evidencias/gitleaks:/out" ghcr.io/gitleaks/gitleaks:latest git /repo --redact --report-format=json --report-path=/out/gitleaks_Auth-Frontend.json --exit-code 0
docker run --rm -v "${ScriptDir}/scripts:/repo/scripts:ro" -v "${ScriptDir}/postman:/repo/postman:ro" -v "${ScriptDir}/bdd:/repo/bdd:ro" -v "${ScriptDir}/evidencias/gitleaks:/out" ghcr.io/gitleaks/gitleaks:latest dir /repo --redact --report-format=json --report-path=/out/gitleaks_QA_Equipo5_Auth.json --exit-code 0

node scripts/sanitize_reports.mjs
docker compose @ComposeArgs down --remove-orphans

Write-Host "[QA-I3] Ejecución Iteración 3 completada."
