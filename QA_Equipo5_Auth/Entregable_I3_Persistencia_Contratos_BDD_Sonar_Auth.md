---
title: "Entregable Iteración 3 — Verificación de Persistencia, Contratos API, Seguridad, BDD y Calidad Estática (Auth Microservice)"
subtitle: "Plan de Pruebas FMAT-REST-EQ5-STP-EQ6-v1.0 · TASK-47 · QA_Equipo5_Auth"
author: "Equipo 5 / QA (V&V sobre Auth-Backend, Auth-Frontend y Auth-Documentation)"
date: "2026-11-28"
geometry: "margin=1.4cm"
fontsize: 10pt
colorlinks: true
linkcolor: NavyBlue
urlcolor: NavyBlue
header-includes:
  - \usepackage{booktabs}
  - \usepackage{longtable}
  - \usepackage{array}
  - \usepackage{etoolbox}
  - \usepackage{xurl}
  - \AtBeginEnvironment{longtable}{\scriptsize\setlength{\tabcolsep}{3.5pt}}
  - \sloppy
---

# 1. Ficha de Ejecución y Alcance

| Campo | Valor Observado |
| :--- | :-------------------------------------------------------------- |
| **Identificador de Reporte** | `QA-E5-I3-AUTH-2026-V1.0` (Paridad técnica con `RP-E6-OKDS-2026-V1.0`) |
| **Plan Base** | `FMAT-REST-EQ5-STP-EQ6-v1.0` (Versión final congelada, enfoque RNF de Seguridad) |
| **Tarea / Sprint** | `TASK-47` (#141) · Sprint 7 (Meta de entrega: Martes 01 Dic 2026) |
| **Rama de Trabajo QA** | `qaauthi3-luisgildzib` (derivada de `origin/develop`, patrón CI `^[a-z0-9]+-[a-z]+$`) |
| **Timestamp de Corrida Unificada (Corrida 2)** | `2026-11-28T03:12:56Z` (`resumen_casos_i3.json` `03:13:30.038Z`, SQL `03:13:30.099Z`, SonarCloud `03:13:36.817Z`) |
| **Commit `Auth-Backend` (`main`)** | `a3844c97c7cc151c169369546a32f2054cfe6ed8` |
| **Commit `Auth-Frontend` (`main`)** | `330f16d236e8bbd101761da0ac57f609711255a0` |
| **Commit `Auth-Documentation` (`main`)** | `8b1fc349de850f87e59618516fb9bfaef559c47c` |
| **Rama base (`origin/develop`)** | `2bb7accc7bfef11111080ef543eeecc7fdeea482` |
| **Topología Real Ejecutada (Cero Mocks)** | Stack aislado `qa-auth-i3-isolated` con `tmpfs` en RAM (sin mutar volúmenes previos): `qa-i3-auth-db` (PostgreSQL `16-alpine` `:5433`, BD `fmat_auth`) + `qa-i3-auth-rabbitmq` (`rabbitmq:3.13-management-alpine`, interno `:5672`, aislado en host `:5673/:15673` por coexistencia con `ordenes-rabbitmq`) + `qa-i3-auth-backend` (`http://localhost:4000`) |
| **Cobertura Funcional de Casos** | 18 casos: `CP-AUTH-01..15`, `CP-AUTH-17`, `CP-AUTH-18`, `CP-AUTH-21` + seguimiento `H-AUTH-B01, B04, B05, B08, B09` |

### Trazabilidad de Ejecuciones (`Corrida 1` vs. `Corrida 2`)

| Ejecución | Timestamp Inicio UTC | Entorno Docker y Aislamiento | Diferencias Clave respecto a la Corrida Anterior |
| :--- | :--- | :--- | :--- |
| **Corrida 1 (Base inicial)** | `2026-11-28T02:29:36Z` *(desfase heredado de `40 s` tras re-ejecución manual previa al commit)* | Contenedores base `fmat-auth-*` (`down -v`) | Semilla creó rol dinámico al final (`E000008`), `CP-AUTH-14` (`2/2`, solo desactivación), `87` verificaciones (`80/7`), sanitización aplicada en etapa posterior. |
| **Corrida 2 (Vigente / Final)** | `2026-11-28T03:12:56Z` *(SQL `03:13:30.099Z`, coherente en todos los JSON)* | Proyecto aislado `qa-auth-i3-isolated` con **`tmpfs`** y **cero `-v`** (`diff` de volúmenes = `0`; `ordenes-*` intactos) | Intenta `E000001` en primer orden tras `ADM000001` (documenta `E000002` real), añade prueba post-`DELETE` en `CP-AUTH-14` (`3/3`), enmarca `ANTI-ENUM-02` como muestra exploratoria de 5 peticiones/rama y ejecuta `sanitize_reports.mjs` automáticamente dentro del lanzador (`89` verificaciones: `82/7`). |

---

# 2. Resumen Ejecutivo y Dictamen

Se construyó y ejecutó de extremo a extremo el arnés automatizado y reproducible `QA_Equipo5_Auth/` (`Corrida 2`) sobre un proyecto Docker completamente aislado en memoria (`qa-auth-i3-isolated` con `tmpfs`, sin crear ni borrar volúmenes persistentes del host), validando migraciones TypeORM reales desde esquema vacío, persistencia transaccional en PostgreSQL 16, mensajería CloudEvents sobre RabbitMQ 3.13, contratos OpenAPI/HTTP con Newman (`ajv`), escenarios Gherkin en Cucumber.js, *Abuse Cases* de seguridad directos al puerto `:4000`, auditoría SonarCloud y escaneo histórico `gitleaks`.

## Métricas Globales de Ejecución (Corrida 2)

- **Casos maestros evaluados (`18`):** `13 PASA` (`72.2%`), `5 FALLA_PARCIAL` (`27.8%`), `0` bloqueados (`100%` ejecutados en vivo).
- **Verificaciones individuales consolidadas (`89`):** `82 PASA` (`92.1%`), `7 FALLA` (`7.9%`) — todas las fallas corresponden a hallazgos reales y reproducibles en el SUT o en su configuración por defecto.
- **Colección Postman + Newman (`auth_qa_e5.postman_collection.json`):** `53/53` peticiones ejecutadas, **`106/106` aserciones en verde (`0` fallos)**, duración total `3.91 s` (`3908 ms`), latencia media `59.79 ms` (mín. `3 ms`, máx. `357 ms`).
- **Escenarios BDD ejecutables (`Cucumber.js`):** **`4` suites `.feature` (`6/6` escenarios, `41/41` pasos `PASSED`, `0` omitidos/fallidos)** en `3.59 s`.
- **Inspección SQL directa (`PostgreSQL 16`):** `4/4` verificaciones en verde (`11/11` hashes `$2b$12$`, `23/23` `refresh_tokens` en SHA-256 hex de 64 chars, `74/74` registros en `audit_logs` con `ipAddress` y `userAgent` no nulos).
- **SonarCloud Quality Gate:** `OK` en ambos repositorios (`Auth-Backend`: `95.4%` cobertura, `0.0%` duplicación, Ratings `A/A/A`; `Auth-Frontend`: `83.5%` cobertura — aún `< 85%` de umbral QA —, Ratings `A/A/A`).
- **Escaneo de secretos (`gitleaks --redact`):** `0` secretos en `Auth-Frontend` (`66` commits), `0` secretos en `QA_Equipo5_Auth/` (`scripts/`, `postman/`, `bdd/`, `evidencias/` tras sanitización de Newman), **`1` hallazgo en historial Git de `Auth-Backend`** (`52` commits: literal de prueba redactado en `src/common/guards/guards.spec.ts:82`, commit `964ce9bb`).

## Dictamen Formal (`FMAT-REST-EQ5-STP-EQ6-v1.0` §7.2): **NO CUMPLE**

> **Veredicto al corte de Iteración 3: `NO CUMPLE`.** Aunque la base funcional y de persistencia criptográfica (`bootstrap` con lock transaccional, dual-login anti-enumeración, generación de `StaffID` por prefijo, reglas BVA `8..72`, rotación de `RefreshToken`, revocación inmediata por desactivación y eliminación `DELETE`, headers Helmet/`no-store`, hashes `$2b$12$`/`SHA-256` y trazabilidad en `audit_logs`) opera correctamente contra PostgreSQL 16 y RabbitMQ 3.13 reales, **no se satisfacen todas las compuertas vigentes de la Sección 7.2** del Plan Congelado debido a los 6 defectos detectados en el SUT (`DEF-E6-001`..`006`) y brechas estáticas heredadas.

### Evaluación contra la Compuerta de Aceptación (`§7.2` del Plan Congelado)

| Clasificación | Compuerta (`FMAT-REST-EQ5-STP-EQ6-v1.0` §7.2) | Resultado I3 | Evidencia / Brecha Observada |
| :--- | :--- | :---: | :--- |
| **Cumplido en I3** | Cobertura de los 18 casos asignados a I3 con arnés sin mocks y `registro.json` | **CUMPLE** | `18/18` casos ejecutados contra Docker real; Newman `53/53` (`106/106`); BDD `6/6` (`41/41`); SQL `4/4`. *(Semilla con desviación `E000002` vs. `E000001`, registrada abajo).* |
| **Cumplido en I3** | Cobertura y Ratings en `Auth-Backend` y Quality Gate SonarCloud | **CUMPLE** | QG `OK`; `Auth-Backend` `95.4%` (`A/A/A`, dup `0.0%`); Jest `20/20` (`266/266`, `0` *force exit*). |
| **Desviación pendiente de aceptación** | Semilla determinista con rol dinámico `E000001` (TASK-47) | **DESVIACIÓN (`E000002`)** | Al crear `ROL_ENCARGADO_OP` en el primer orden vía API tras `ADM000001`, el SUT asignó **`E000002`** por conteo global `count + 1` en `StaffService.generateUniqueStaffId`; no se marca como cumplida con `E000001` (`OBS-E6-05`). |
| **Pendiente (`NO CUMPLE`)** | Complejidad ciclomática individual `CC <= 10` por función | **NO CUMPLE** | SonarCloud API solo reporta CC agregada por archivo (`12` BE / `24` FE con CC de archivo `> 10`) y `typescript:S3776` (`1` BE / `6` FE). Auditoría AST individual por función (`function_cc_audit.json`): **`6/195` funciones `> 10` en `Auth-Backend` (`máx. 16`)** y **`18/438` en `Auth-Frontend` (`máx. 25`)** (`OBS-E6-04`). |
| **Pendiente (`NO CUMPLE`)** | Defectos abiertos (`0` Blocker/Críticos/Altos incl. `H-AUTH-B01`; máx. `3` Medios) | **NO CUMPLE** | **`DEF-E6-001`** (Alta: JWT sin `exp -> 200`), **`DEF-E6-002`** (Media-Alta: replay sin incrementar `tv`), **`DEF-E6-003..006`** (4 Medias, incl. `H-AUTH-B01` y `H-AUTH-B04` abiertos). |
| **Pendiente (`NO CUMPLE`)** | Rechazo `401/403` en `100%` de *Abuse Cases* de seguridad | **NO CUMPLE** | Fallan `ABUSE-JWT-07` (`DEF-E6-001`), `REPLAY-TV-01` (`DEF-E6-002`), `ABUSE-TEMP-03/04` (`DEF-E6-003`) y `ABUSE-CORS-02` (`DEF-E6-006`). |
| **Pendiente (`NO CUMPLE`)** | Cobertura global `Auth-Frontend >= 85%` | **NO CUMPLE** | SonarCloud `Auth-Frontend`: **`83.5%`** (aunque subió a Ratings `A/A/A`). |
| **Pendiente (`NO CUMPLE`)** | `0` secretos en historial Git completo (`gitleaks`) | **NO CUMPLE (BE hist.)** | `0` leaks en `Auth-Frontend` y `QA_Equipo5_Auth/`; `1` literal de test en historial de `Auth-Backend` (`guards.spec.ts:82`, redactado). |
| **Futuro (I4)** | `CP-AUTH-16, 20, 22, 23, 24`, Playwright multi-navegador, `@axe-core` WCAG 2.1 AA, build Nginx, `0` tokens `localStorage` | *Fuera de alcance I3* | Programado para **Iteración 4** (`TASK-48`). |
| **Futuro (I5)** | `CP-AUTH-19`, `AUTH-GW-UNIT` (`test_gateway.py` + `lupa`), Kong 3.4 `:8000`, acople `sala.staff-sync` y 6 MFEs | *Fuera de alcance I3* | Programado para **Iteración 5** (`TASK-49`). |
| **Futuro (I6 / Cierre)** | Carga Grafana k6 (`p95 < 500 ms` / `p99 <= 10 ms`), OWASP ZAP Baseline y `DRE >= 90%` | *Fuera de alcance I3* | Programado para **Iteración 6** y **Cierre Final**. |

---

# 3. Ambiente Docker Real, Migraciones y Datos Semilla

El arnés `QA_Equipo5_Auth/ejecutar_i3.sh` levanta un entorno aislado (`docker compose -p qa-auth-i3-isolated ... down --remove-orphans -> up -d --build`) respaldado exclusivamente por **`tmpfs` en RAM** (`volumes: !reset []`), combinando `Auth-Backend/docker-compose.yml` con `QA_Equipo5_Auth/docker-compose.i3.yml` para iniciar desde esquema vacío sin modificar ni borrar volúmenes Docker existentes:

- **Broker RabbitMQ real:** `qa-i3-auth-rabbitmq` (`rabbitmq:3.13-management-alpine`), `RABBITMQ_URL=amqp://<local-broker-user>:<local-broker-pass>@fmat-auth-rabbitmq:5672` (mapeado en host a `:5673/:15673` al convivir con `ordenes-rabbitmq`). `GET /health` verificó `{"status":"healthy","dependencies":{"rabbitmq":"connected"}}`.
- **Migraciones TypeORM ejecutadas sobre BD vacía (`fmat_auth` en `qa-i3-auth-db`):**
  1. `InitialSchema1762900000000` (`id: 1`)
  2. `SessionVersionAndHashedTempPasswords1762900000001` (`id: 2`)
  - Tablas verificadas en PostgreSQL: `users`, `staff_profiles`, `roles`, `user_roles`, `refresh_tokens`, `audit_logs`, `migrations`.

## 3.1 Higiene y Clasificación de Credenciales Locales de Prueba

| Tipo de Dato | Dónde Aparece | Clasificación | Tratamiento de Seguridad |
| :--- | :--- | :--- | :--- |
| **`JWT_SECRET`** | Generado en RAM al arrancar `ejecutar_i3.sh`/`.ps1` (`crypto.randomBytes(32)`) | Secreto efímero local | Nunca persistido en Git ni en logs/reportes. |
| **Defaults Docker local** (`postgres`, `guest` en `localhost:5433` / `:5672`) | `docker-compose.i3.yml`, `ejecutar_i3.*` | Solo entorno efímero local (`tmpfs`) | Desechado al desmontar `qa-auth-i3-isolated`; sin conexión a CloudAMQP ni servidores remotos y sin tocar volúmenes existentes. |
| **Cuentas y claves de semilla** (`admin@fmat.edu.mx` + contraseñas de prueba en `seed_qa_auth.mjs` / BDD / Postman) | Scripts y colección de pruebas | Valores sintéticos locales de prueba | Redactados (`[REDACTED_TEST_PASSWORD]`, `[REDACTED_TEST_CREDENTIAL]`) en reportes `newman-report.*` y `bdd-report.*`. |
| **JWT, cookies `fmat_refresh_token`, claves temporales `Fmat-*`** | Emitidos en vivo durante Newman | Credenciales efímeras de corrida | Sanitizados por `scripts/sanitize_reports.mjs` (`[REDACTED_EPHEMERAL_JWT]`, `[REDACTED_EPHEMERAL_COOKIE]`, `[REDACTED_EPHEMERAL_TEMP_PASS]`). |
| **Correos personales / secretos externos / hashes bcrypt completos** | Evidencias JSON, HTML, PDF | Datos sensibles reales | **Eliminados / redactados** (`[REDACTED_PERSONAL_EMAIL]` en `gitleaks_Auth-Backend.json`; solo prefijo `$2b$12$` en `sql_audit.json`). |
- **Datos semilla deterministas (`scripts/seed_qa_auth.mjs`, `11` usuarios persistidos):**

> **Desviación técnica pendiente de aceptación (`E000001` vs. `E000002`):** En Corrida 2 se colocó la creación del rol dinámico `ROL_ENCARGADO_OP` (prefijo `E`) en el **primer orden permitido vía API** inmediatamente después del *bootstrap* de `ADM000001`. Aun así, `Auth-Backend` asignó **`E000002`** (y desplazó `HOST`..`CAJERO` a `H000003`..`J000008`), debido a que `StaffService.generateUniqueStaffId` calcula `sequence = (await this.staffProfileRepo.count()) + 1`. Como `ADM000001` ocupa la fila 1 de `staff_profiles`, el contador global impide generar `E000001` mediante la API pública sin alterar la BD; **esta subtarea de semilla no se da por cumplida con `E000001` y queda registrada como desviación pendiente de aceptación (`OBS-E6-05`)**.

| Cuenta / Rol | `staffId` Asignado | Estado Contraseña | `isActive` | Observación |
| :------------------------ | :--------- | :-------- | :---: | :-------------------------------- |
| Gerente (`admin@fmat.edu.mx`, `ADMINISTRADOR`) | `ADM000001` | `ACTIVE` | `true` | Bootstrap concurrente `CP-AUTH-01` (fila 1 en `staff_profiles`) |
| Encargado Op. (`ROL_ENCARGADO_OP` dinámico) | `E000002` | `ACTIVE` | `true` | Desviación pendiente vs. `E000001` (1.º vía API; recibe `E000002` por `count + 1`) |
| Host (`HOST`) | `H000003` | `ACTIVE` | `true` | Prefijo `H`, `^[A-Z][0-9]{6}$` |
| Almacenista (`ALMACENISTA`) | `A000004` | `ACTIVE` | `true` | Prefijo `A` |
| Gestor Menú (`GESTOR_MENU`) | `G000005` | `ACTIVE` | `true` | Prefijo `G` |
| Mesero (`MESERO`) | `M000006` | `ACTIVE` | `true` | Prefijo `M` |
| Chef (`CHEF_MASTER`) | `C000007` | `ACTIVE` | `true` | Prefijo `C` |
| Cajero (`CAJERO`) | `J000008` | `ACTIVE` | `true` | Prefijo `J` |
| Temporal Uno (`MESERO`) | `M000009` | `TEMPORARY` | `true` | `mustChangePassword: true` |
| Temporal Dos (`CHEF_MASTER`) | `C000010` | `TEMPORARY` | `true` | `mustChangePassword: true` |
| Desactivado (`MESERO`) | `M000011` | `ACTIVE` | `false` | Baja lógica verificada en `CP-AUTH-03` |

---

# 4. Matriz Caso por Caso (`CP-AUTH-01..15`, `17`, `18`, `21`) — Corrida 2

| Caso | Requisitos | Estado | Checks | Tiempo | Esperado vs. Observado (Síntesis Factual) | Evidencia (`registro.json`) |
| :--- | :------------ | :---: | :---: | ---: | :---------------------------------------------------------- | :------------------------ |
| **CP-AUTH-01** | `REQ-AUTH-001`, `BR-AUTH-001`, `INV-AUTH-001` | ✔ **PASA** | `3/3` | `16 ms` | Esperado: `setup-status` `false -> true` y lock ante concurrencia. Observado: 12 llamadas paralelas produjeron `1x201` (`ADM000001`) y `11x409`; segundo setup Newman `409`. | [`evidencias/CP-AUTH-01/registro.json`](evidencias/CP-AUTH-01/registro.json) |
| **CP-AUTH-02** | `REQ-AUTH-002`, `REQ-AUTH-009`, `BR-AUTH-002` | ✔ **PASA** | `5/5` | `795 ms` | Esperado: Login dual exclusivo ADMIN/STAFF y bloqueo de cruce. Observado: ambos `200`; cruces `ADMIN + ADM000001` y `STAFF + email` rechazados con `401`. | [`evidencias/CP-AUTH-02/registro.json`](evidencias/CP-AUTH-02/registro.json) |
| **CP-AUTH-03** | `REQ-AUTH-009`, `BR-AUTH-016`, `NFR-AUTH-SEC-003` | ✔ **PASA** | `5/5` | `568 ms` | Esperado: `401` homogéneo y latencia compatible con bcrypt (prueba estadística formal en I6 k6). Observado: mensaje único `"Credenciales inválidas o cuenta deshabilitada"`; muestra exploratoria (5/rama) `168.85 / 170.24 / 171.90 ms` (`spread 3.05 ms`). | [`evidencias/CP-AUTH-03/registro.json`](evidencias/CP-AUTH-03/registro.json) |
| **CP-AUTH-04** | `REQ-AUTH-003`, `BR-AUTH-004`, `INV-AUTH-002` | ✔ **PASA** | `3/3` | `360 ms` | Esperado: Generación automática `^[A-Z][0-9]{6}$` por prefijo. Observado: `E/H/A/G/M/C/J` validados en semilla (`E000002` en lugar de `E000001`, desviación pendiente `OBS-E6-05`) y alta Newman (`201`). | [`evidencias/CP-AUTH-04/registro.json`](evidencias/CP-AUTH-04/registro.json) |
| **CP-AUTH-05** | `REQ-AUTH-004`, `REQ-AUTH-006`, `INV-AUTH-003/005` | ✔ **PASA** | `7/7` | `23 ms` | Esperado: Rechazo `roles:[]`, `permissions:[]`, `*`, `ADMINISTRADOR`, SQLi y Mass Assignment. Observado: todos `400/401`; en BD quedó `STAFF`, `TEMPORARY`, `isActive: true`, `tv: 0`. | [`evidencias/CP-AUTH-05/registro.json`](evidencias/CP-AUTH-05/registro.json) |
| **CP-AUTH-06** | `REQ-AUTH-005`, `REQ-AUTH-007`, `BR-AUTH-006` | ✔ **PASA** | `6/6` | `25 ms` | Esperado: Catálogo PBAC, rol dinámico, lectura operativa `MESERO` y validación `H-AUTH-16`. Observado: `7` dominios / `61` permisos (`>=56`), rol `ROL_ENCARGADO_OP` (`E000002`, desviación vs. `E000001` documentada), `?isActive=valor_invalido -> 400`. | [`evidencias/CP-AUTH-06/registro.json`](evidencias/CP-AUTH-06/registro.json) |
| **CP-AUTH-07** | `REQ-AUTH-012/013`, `NFR-AUTH-SEC-004`, `INV-AUTH-006` | ✘ **PARCIAL** | `10/12` | `189 ms` | Esperado: `403 PASSWORD_CHANGE_REQUIRED` en todas las rutas + neutralización Header Spoofing. Observado: `/staff` y `/roles` bloquean `PASSWORD_CHANGE_REQUIRED`, spoofing `401/403`; `PATCH /auth/profile` y `DELETE /auth/account` dan `403` sin `code` (`DEF-E6-003`). | [`evidencias/CP-AUTH-07/registro.json`](evidencias/CP-AUTH-07/registro.json) |
| **CP-AUTH-08** | `REQ-AUTH-013`, `BR-AUTH-011`, `BR-AUTH-012` | ✔ **PASA** | `11/11` | `917 ms` | Esperado: BVA exacto `8..72` y complejidad. Observado: `7`, `73`, espacios, sin may/min/dígito/símbolo e igual a `staffId` -> `400`; `8` y `72` chars complejos -> `200`. | [`evidencias/CP-AUTH-08/registro.json`](evidencias/CP-AUTH-08/registro.json) |
| **CP-AUTH-09** | `REQ-AUTH-013`, `INV-AUTH-002`, `INV-AUTH-006` | ✔ **PASA** | `1/1` | `4 ms` | Esperado: Transición irreversible `TEMPORARY -> ACTIVE` e inmutabilidad de `staffId`. Observado: `GET /auth/me` confirma `ACTIVE`, `mustChangePassword: false` y mismo `staffId`. | [`evidencias/CP-AUTH-09/registro.json`](evidencias/CP-AUTH-09/registro.json) |
| **CP-AUTH-10** | `REQ-AUTH-010`, `NFR-AUTH-SEC-002/004` | ✘ **PARCIAL** | `9/10` | `N/A` | Esperado: Claims completos y `401` ante 9 manipulaciones JWT. Observado: `iss/aud/exp=900s` y 8 ataques dan `401`, **pero JWT sin claim `exp` respondió `200 OK`** (`DEF-E6-001`). | [`evidencias/CP-AUTH-10/registro.json`](evidencias/CP-AUTH-10/registro.json) |
| **CP-AUTH-11** | `REQ-AUTH-011`, `BR-AUTH-013`, `NFR-AUTH-SEC-002` | ✘ **PARCIAL** | `2/3` | `10 ms` | Esperado: Rotación, replay `401` e incremento de `tv`. Observado: rotación `200` y replay `401`, **pero `tv` se mantuvo en `1`** y `AccessToken` rotado siguió `200` (`DEF-E6-002`). | [`evidencias/CP-AUTH-11/registro.json`](evidencias/CP-AUTH-11/registro.json) |
| **CP-AUTH-12** | `REQ-AUTH-014`, `REQ-AUTH-021` | ✔ **PASA** | `1/1` | `4 ms` | Esperado: `GET /api/v1/auth/me` devuelve perfil y `allowedViews` coherentes por rol. Observado: `200 OK` validado por esquema `ajv`. | [`evidencias/CP-AUTH-12/registro.json`](evidencias/CP-AUTH-12/registro.json) |
| **CP-AUTH-13** | `REQ-AUTH-006`, `REQ-AUTH-007`, `INV-AUTH-002` | ✔ **PASA** | `1/1` | `7 ms` | Esperado: `PUT /staff/:id/roles` actualiza roles sin mutar `staffId`. Observado: `200 OK`, rol actualizado a `MESERO`, `staffId` preservado. | [`evidencias/CP-AUTH-13/registro.json`](evidencias/CP-AUTH-13/registro.json) |
| **CP-AUTH-14** | `REQ-AUTH-015`, `BR-AUTH-015`, `INV-AUTH-007` | ✔ **PASA** | `3/3` | `12 ms` | Esperado: Desactivación y `DELETE /staff/:id` revocan `AccessToken` inmediatamente. Observado: `/status {isActive:false} -> 200` (`/auth/me` -> `401`) y `REVOKE-DELETE-01` tras `DELETE /staff/:id` (`M000012`) -> `GET /auth/me` inmediato `401`. | [`evidencias/CP-AUTH-14/registro.json`](evidencias/CP-AUTH-14/registro.json) |
| **CP-AUTH-15** | `REQ-AUTH-016`, `REQ-AUTH-017`, `NFR-AUTH-AVAIL-002` | ✘ **PARCIAL** | `2/4` | `N/A` | Esperado: Eventos en `restaurant.staff.events` incluyendo `PATCH /staff/:id` y campos `permissions`/`roleLabel`. Observado: 4 eventos capturados; `H-AUTH-B01` y `H-AUTH-B04` siguen **ABIERTOS**. | [`evidencias/CP-AUTH-15/registro.json`](evidencias/CP-AUTH-15/registro.json) |
| **CP-AUTH-17** | `REQ-AUTH-017`, `BR-AUTH-014`, `BR-AUTH-016` | ✔ **PASA** | `3/3` | `183 ms` | Esperado: `request-password-reset` homogéneo y `POST /staff/:id/reset-password -> TEMPORARY`. Observado: mensajes idénticos en válida vs `Z999999` y transición a `TEMPORARY` (`200`). | [`evidencias/CP-AUTH-17/registro.json`](evidencias/CP-AUTH-17/registro.json) |
| **CP-AUTH-18** | `REQ-AUTH-020`, `BR-AUTH-017`, `INV-AUTH-004` | ✔ **PASA** | `4/4` | `21 ms` | Esperado: `STAFF` bloqueado `403` en `/profile` y `/account`; `ADMIN` puede editar perfil y no puede auto-eliminarse siendo el último (`409`). Observado: `403 / 403 / 200 / 409`. | [`evidencias/CP-AUTH-18/registro.json`](evidencias/CP-AUTH-18/registro.json) |
| **CP-AUTH-21** | `REQ-AUTH-022`, `NFR-AUTH-AUD-001`, `NFR-AUTH-SEC-001/004` | ✘ **PARCIAL** | `6/7` | `N/A` | Esperado: `$2b$12$`, SHA-256, `audit_logs` con IP/UA, Helmet y CORS. Observado: SQL `4/4`, Helmet/CSP/`no-store` OK, CORS malicioso rechazado; **CORS `:5179` (`cocinaApp`) sin `Allow-Origin` por defecto** (`DEF-E6-006`). | [`evidencias/CP-AUTH-21/registro.json`](evidencias/CP-AUTH-21/registro.json) |

---

# 5. Resultados Detallados por Batería de Pruebas (Corrida 2)

## 5.1 Bootstrap Transaccional y Verificación Exploratoria Anti-Enumeración

- **Bootstrap concurrente (`CP-AUTH-01`, `evidencias/CP-AUTH-01/bootstrap_concurrency.json`):** Sobre BD recién migrada (`configured: false`), `12` invocaciones simultáneas vía `Promise.all` a `POST /api/v1/auth/setup-admin` arrojaron **`1` respuesta `201 Created`** (`staffId: "ADM000001"`, `237.36 ms`) y **`11` respuestas `409 Conflict`** (`225.36–263.74 ms`), tras lo cual `GET /api/v1/auth/setup-status` devolvió `{"configured": true}`. Valida `pg_advisory_xact_lock(74201901)`.
- **Anti-enumeración (`CP-AUTH-03`, `evidencias/security_abuse_results.json`):** Se midió una muestra exploratoria de `15` peticiones (`5` por rama) contra `POST /api/v1/auth/login` (`STAFF`):
  - Usuario inexistente (`Z999990..4`): `401`, `"Credenciales inválidas o cuenta deshabilitada"`, promedio **`168.85 ms`** (mín. `167.18`, máx. `170.82`).
  - Contraseña incorrecta (`M000006`): `401`, `"Credenciales inválidas o cuenta deshabilitada"`, promedio **`170.24 ms`** (mín. `168.64`, máx. `172.61`).
  - Cuenta inactiva (`M000011`): `401`, `"Credenciales inválidas o cuenta deshabilitada"`, promedio **`171.90 ms`** (mín. `167.29`, máx. `186.94`).
  - Dispersión observada entre promedios: **`3.05 ms`**, compatible con ejecución de `bcrypt` en todas las ramas. **Nota estadística:** cinco muestras por rama constituyen únicamente un indicio funcional exploratorio y **no** una prueba estadística definitiva de tiempo constante; la evaluación estadística bajo carga corresponde a **Grafana k6 (Iteración 6)**.

## 5.2 Colección Postman + Newman (`ajv` + `htmlextra`)

- Archivo colección: `QA_Equipo5_Auth/postman/auth_qa_e5.postman_collection.json`
- Reportes generados: `QA_Equipo5_Auth/evidencias/newman-report.html` y `QA_Equipo5_Auth/evidencias/newman-report.json`
- Resultado: `1` iteración, **`53` requests**, **`106` test scripts**, **`106/106` aserciones exitosas (`0` fallos)**, duración total `3.91 s`, latencia media `59.79 ms` (mín. `3 ms`, máx. `357 ms` en login bcrypt).
- **Nota de contrato observada en catálogo de permisos (`GET /api/v1/roles/catalog`):** El servicio expone actualmente **`61` permisos en `7` dominios** (9 `inventory`, 7 `kitchen`, 9 `orders`, 13 `menu`, 19 `sala`, 2 `billing`, 2 `staff`), superando los `56` referidos originalmente en documentación anterior (`permissions-catalog.ts`).

## 5.3 *Abuse Cases* de Seguridad Directos contra `:4000`

Archivo de evidencia: `QA_Equipo5_Auth/evidencias/security_abuse_results.json`.

| ID Prueba | Vector Atacado | Esperado | Observado Real | Estado |
| :------------ | :------------------------------ | :------------------------- | :------------------------- | :------------ |
| `ABUSE-JWT-01` | JWT firmado con secreto ajeno | `401` | `401` | ✔ PASA |
| `ABUSE-JWT-02` | JWT con firma basura modificada | `401` | `401` | ✔ PASA |
| `ABUSE-JWT-03` | JWT `alg: "none"` sin firma | `401` | `401` | ✔ PASA |
| `ABUSE-JWT-04` | JWT con cabecera sin campo `alg` | `401` | `401` | ✔ PASA |
| `ABUSE-JWT-05` | JWT con cabecera `alg: ""` | `401` | `401` | ✔ PASA |
| `ABUSE-JWT-06` | JWT expirado (`exp` en el pasado) | `401` | `401` | ✔ PASA |
| `ABUSE-JWT-07` | JWT firmado con secreto real **sin claim `exp`** | `401` | **`200 OK` (`/auth/me`)** | ✘ **FALLA (`DEF-E6-001`)** |
| `ABUSE-JWT-08/09` | JWT en URL (`?token=` / `?access_token=`) | `401` | `401 / 401` | ✔ PASA |
| `ABUSE-SPOOF-01/02` | `X-User-Roles: ADMINISTRADOR`, `X-User-Permissions: *` sin token | `401` | `401 / 401` | ✔ PASA |
| `ABUSE-SPOOF-03..05` | Cabeceras `X-User-*` admin + JWT válido de `MESERO` | `403` | `403 / 403 / 403` | ✔ PASA |
| `ABUSE-TEMP-01/02` | Token `TEMPORARY` directo a `GET /staff` y `GET /roles` | `403` (`PASSWORD_CHANGE_` `REQUIRED`) | `403` (`PASSWORD_CHANGE_` `REQUIRED`) | ✔ PASA |
| `ABUSE-TEMP-03/04` | Token `TEMPORARY` en `PATCH /auth/profile` y `DELETE /auth/account` | `403` (`PASSWORD_CHANGE_` `REQUIRED`) | **`403` sin `code` (solo check `STAFF`)** | ✘ **FALLA (`DEF-E6-003`)** |
| `ABUSE-CORS-01` | `OPTIONS`/`GET` con `Origin: https://sitio-malicioso.example` | Sin `Allow-Origin` | `allowOrigin: null` | ✔ PASA |
| `ABUSE-CORS-02` | `OPTIONS` con `Origin: http://localhost:5179` (`cocinaApp`) | Permitido en compose | **`allowOrigin: null` (`404`)** | ✘ **FALLA (`DEF-E6-006`)** |
| `ABUSE-MASS-01` | Inyección `isActive:false`, `tokenVersion:99`, `userType:"ADMIN"` | Ignorados en BD | `STAFF / TEMPORARY / true / tv=0` | ✔ PASA |
| `ABUSE-SQLI-01` | Payloads `' OR '1'='1` y `' UNION SELECT...` | `401` / `<500` sin error SQL | `401 / 200` sin fuga SQL | ✔ PASA |
| `REPLAY-TV-01` | Replay `RefreshToken` previo + verificación `tv` en BD | `401` + incremento `tv` | **`401`, pero `tv=1` intacto** | ✘ **FALLA (`DEF-E6-002`)** |
| `REVOKE-DELETE-01` | Uso inmediato de `AccessToken` tras `DELETE /api/v1/staff/:id` (`M000012`) | `401` inmediato en `GET /auth/me` | `200 -> DELETE 200 -> GET /auth/me 401` (`La sesión ya no es válida`) | ✔ PASA |
| `SEC-HEADERS-01` | `/health` y `/api/v1/auth/me` | Helmet + `no-store`, sin `X-Powered-By` | `no-store`, `no-cache`, `DENY`, `nosniff`, CSP activo | ✔ PASA |

## 5.4 Inspección SQL Directa en PostgreSQL (`fmat_auth`)

Evidencia: `QA_Equipo5_Auth/evidencias/CP-AUTH-21/sql_audit.json` y `sql_audit.txt` (`verify_db_audit.sql` / `verify_db_audit.mjs`).

- **Hashes de contraseñas (`users`):** **`11` de `11` usuarios (`100%`)** cumplen el patrón `^\$2b\$12\$[./A-Za-z0-9]{53}$` (`length = 60`), `0` hashes en texto plano o legado (`NFR-AUTH-SEC-001`).
- **Tokens de refresco (`refresh_tokens`):** **`23` de `23` registros (`100%`)** almacenan hash SHA-256 hexadecimal (`^[a-f0-9]{64}$`), `0` tokens en claro; `9` revocados y `14` activos (`NFR-AUTH-SEC-002`).
- **Bitácora inmutable (`audit_logs`):** **`74` de `74` registros (`100%`)** presentan `ipAddress` (`192.168.65.1`) y `userAgent` no nulos (`missing_network_context = 0`, `NFR-AUTH-AUD-001`). Distribución observada al cierre de la Fase 5 (`74` eventos acumulados previos a BDD): `LOGIN_SUCCESS (20)`, `STAFF_CREATED (16)`, `PASSWORD_CHANGED (12)`, `LOGIN_FAILED (6)`, `STAFF_DELETED (6)`, `STAFF_STATUS_CHANGED (3)`, `ROLE_CREATED (2)`, `STAFF_ROLES_UPDATED (2)`, `PASSWORD_RESET_BY_ADMIN (1)`, `PASSWORD_RESET_REQUESTED (1)`, `PROFILE_UPDATED (1)`, `ROLE_DELETED (1)`, `ROLE_UPDATED (1)`, `SETUP_ADMIN (1)`, `STAFF_UPDATED (1)`.

## 5.5 Mensajería RabbitMQ en Vivo (`restaurant.staff.events`) y Seguimiento `H-AUTH-B01`/`B04`

Evidencia: `QA_Equipo5_Auth/evidencias/CP-AUTH-15/rabbitmq_events.json`.

- Exchange verificado vía Management API: `restaurant.staff.events`, tipo `topic`, `durable: true`, `auto_delete: false`.
- **Eventos CloudEvents v1.0 capturados (`4`):**
  1. `POST /api/v1/staff` -> `routingKey: "staff.created"`, `type: "urn:fmat:event:staff:member-created"` (`deliveryMode: 2`).
  2. `PUT /api/v1/staff/:id/roles` -> `routingKey: "staff.roles.updated"`, `type: "urn:fmat:event:staff:roles-updated"`.
  3. `PATCH /api/v1/staff/:id/status` -> `routingKey: "staff.status.changed"`, `type: "urn:fmat:event:staff:status-changed"`.
  4. `DELETE /api/v1/staff/:id` -> `routingKey: "staff.deleted"`, `type: "urn:fmat:event:staff:member-deleted"`.
- **Confirmación de hallazgos de Iteración 2:**
  - **`H-AUTH-B01` (ABIERTO):** Al ejecutar `PATCH /api/v1/staff/:id` enviando `{ firstName: "RabbitPatch", roles: ["CAJERO"], isActive: false }`, la API respondió `200 OK` y persistió en BD, **pero emitió `0` mensajes a RabbitMQ**.
  - **`H-AUTH-B04` (ABIERTO):** Las únicas claves observadas dentro de `payload.data` son `["userId", "staffId", "firstName", "lastName", "roles", "isActive"]`. No se incluyen **`permissions`** ni **`roleLabel`** documentados en `architecture.md` (líneas 246–261).

## 5.6 Suites Ejecutables BDD (`Cucumber.js`)

Reportes generados: `QA_Equipo5_Auth/evidencias/bdd-report.html` y `QA_Equipo5_Auth/evidencias/bdd-report.json`.

| Archivo `.feature` | Escenarios | Pasos | Resultado | Cobertura Principal |
| :----------------------------------------- | :-----------: | :-------: | :------------: | :--------------------------------------------------- |
| `01_bootstrap_and_login.feature` | `3` | `14` | ✔ `PASSED` | Bootstrap `409`, login dual ADMIN/STAFF, rechazo cruce canales y anti-enumeración |
| `02_temporary_password_containment.feature` | `1` | `9` | ✔ `PASSED` | `mustChangePassword: true`, `403 PASSWORD_CHANGE_REQUIRED`, BVA `7` vs `8` chars, `ACTIVE` |
| `03_staff_pbac_and_roles.feature` | `1` | `11` | ✔ `PASSED` | Bloqueo `*` / `[]` / `ADMINISTRADOR`, rol dinámico prefijo `E`, lectura sin `passwordHash` |
| `04_token_rotation_and_revocation.feature` | `1` | `7` | ✔ `PASSED` | Rotación `RefreshToken`, replay `401`, revocación inmediata vía `/status` |
| **TOTAL** | **`6`** | **`41`** | ✔ **`6/6` (`100%`)** | Ejecutado contra `http://localhost:4000` (`3.59 s`) |

## 5.7 SonarCloud, Complejidad Ciclomática, Pruebas Unitarias Jest y `gitleaks`

### Evolución de Quality Gate en SonarCloud (`evidencias/sonarcloud_metrics.json`)

| Repositorio | Quality Gate | Cobertura | Duplicación | Deuda Téc. | Rel / Sec / Maint | Bugs / Vuln / Hotspots | Code Smells |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`Auth-Backend`** | `OK` | `95.4%` | `0.0%` | `0.0%` (`40m`) | `A / A / A` | `0 / 0 / 0` | `9` |
| **`Auth-Frontend`** | `OK` | **`83.5%`** (`<85%`) | `1.0%` | `0.1%` (`185m`) | `A / A / A` *(subió desde `B`)* | `0 / 0 / 0` | `33` |

### Complejidad Ciclomática Individual por Función (`CC <= 10`, Anexo B)

- **Limitación de la API de SonarCloud y método de verificación individual:** El *endpoint* `measures/component_tree` de SonarCloud únicamente entrega complejidad ciclomática **agregada por archivo** (`12` archivos de `Auth-Backend` y `24` de `Auth-Frontend` con CC de archivo `> 10`), por lo que dividir `CC / funciones` **no** permite afirmar cumplimiento de `CC <= 10` por función. Para verificar el criterio función por función de forma objetiva, se ejecutó `scripts/audit_function_cc.mjs` mediante el AST de TypeScript sobre `src/**/*.{ts,tsx}` (excluyendo specs y `.d.ts`), contrastado además con las incidencias `typescript:S3776` (Complejidad Cognitiva `> 15`) publicadas por SonarCloud (`evidencias/static_reaudit/function_cc_audit.json`).
- **Resultado individual (`NO CUMPLE CC <= 10` por función):**
  - **`Auth-Backend` (`195` funciones analizadas; `6` superan `CC > 10`; `máx. = 16`):** `src/auth/auth.service.ts:170 login` (`16`), `src/staff/staff.service.ts:292 updateStaff` (`16`), `src/common/guards/roles.guard.ts:11 canActivate` (`12`), `src/common/security/security.config.ts:36 isPrivateAddress` (`12`), `src/staff/staff.service.ts:516 getRolePrefix` (`12`), `src/common/validation/password.validation.ts:46 validateStrongPassword` (`11`). SonarCloud reporta además **`1` incidencia `CRITICAL` (`S3776`)** en `src/auth/auth.service.ts:512` (complejidad cognitiva `18 > 15`).
  - **`Auth-Frontend` (`438` funciones analizadas; `18` superan `CC > 10`; `máx. = 25`):** `src/components/ui/Input.tsx:13 Input` (`25`), `src/components/admin/StaffDetailDrawer.tsx:15 StaffDetailDrawer` (`23`), `src/services/authApi.ts:451 getMe` (`23`), `src/components/auth/LoginForm.tsx:11 LoginForm` (`21`), `src/services/authStorage.ts:67 sanitizeSession` (`21`), `src/components/admin/StaffManagementView.tsx:14 StaffManagementView` (`20`), `src/components/profile/ProfileSettingsView.tsx:50 ProfileSettingsView` (`19`), `src/components/admin/StaffManagementView.tsx:90 handleCreateStaff` (`17`), y `10` funciones adicionales con `CC` entre `11` y `15` (detalle completo en `evidencias/static_reaudit/function_cc_audit.json`). SonarCloud reporta **`6` incidencias `CRITICAL` (`S3776`)** en `LoginForm.tsx` (`L14`, `L57`: `16`), `StaffManagementView.tsx` (`L14`: `16`; `L90`: `27`), `StaffDetailDrawer.tsx` (`L23`: `19`) e `Input.tsx` (`L26`: `20`).

### Pruebas Unitarias Locales `Auth-Backend` y `gitleaks`

- **Jest (`evidencias/static_reaudit/jest_auth_backend.log`):** `20/20` suites (`266/266` tests en verde, `18.226 s`), `99.79%` Stmts, `90.76%` Branches, `100%` Funcs, `99.77%` Lines. **La advertencia `force exited` de Iteración 2 quedó corregida (`0` ocurrencias).** Sin embargo, `Auth-Backend/tsconfig.json` conserva `strictNullChecks: false` y `noImplicitAny: false`.
- **`gitleaks` (`ghcr.io/gitleaks/gitleaks:latest --redact`, `evidencias/gitleaks/`):**
  - `Auth-Backend` (`52` commits): **`1` coincidencia** (`generic-api-key`) en commit `964ce9bb` archivo `src/common/guards/guards.spec.ts` línea `82` (cadena literal de prueba redactada).
  - `Auth-Frontend` (`66` commits): **`0` secretos filtrados**.
  - `QA_Equipo5_Auth` (`scripts/`, `postman/`, `bdd/`): **`0` secretos filtrados**.

---

# 6. Seguimiento Anexo B (`H-AUTH`) y Defectos Registrados (IEEE 829)

## 6.1 Seguimiento de Hallazgos de Iteración 2

| Hallazgo | Estado I3 | Evidencia Verificada en Iteración 3 |
| :---------- | :---: | :------------------------------------------------------------- |
| **`H-AUTH-B01`** | **ABIERTO** | `PATCH /api/v1/staff/:id` cambiando `roles` e `isActive` retorna `200 OK` pero publica `0` eventos en `restaurant.staff.events` (`evidencias/CP-AUTH-15/rabbitmq_events.json`). |
| **`H-AUTH-B04`** | **ABIERTO** | CloudEvents reales contienen solo `userId, staffId, firstName, lastName, roles, isActive`; omiten `permissions` y `roleLabel` especificados en `architecture.md` L246–261. |
| **`H-AUTH-B05`** | **PARCIAL** | Advertencia Jest `force exited` resuelta (`266/266` tests limpios en `18.23 s`); `Auth-Backend/tsconfig.json` sigue con `strictNullChecks: false` y `noImplicitAny: false`. |
| **`H-AUTH-B08`** | **ABIERTO** | `Auth-Documentation/README.md` L53 anuncia `REQ-AUTH-001 al REQ-AUTH-020`; `output/ers/traceability.md` termina en `REQ-AUTH-024`, omite `REQ-AUTH-025` y usa `BR-001`/`INV-001`. |
| **`H-AUTH-B09`** | **ABIERTO** | `output/ers/configuration.md` L11–18 documenta `CORS_ALLOWED_ORIGINS`, `DATABASE_URL`, `JWT_ACCESS_TOKEN_TTL` y `REFRESH_TOKEN_TTL_DAYS`, desalineados vs `Auth-Backend/.env.example` y `docker-compose.yml` (`CORS_ORIGINS`, `DB_*`). |

## 6.2 Registro Formal de Defectos y Observaciones (`DEF-E6` / `OBS-E6`)

| ID | Severidad | Caso(s) | Descripción y Evidencia Reproducible | Acción Correctiva Sugerida (Eq. 6 Auth) |
| :---------- | :---: | :---: | :---------------------------------------- | :---------------------------------------- |
| **`DEF-E6-001`** | **Alta** | `CP-AUTH-10` | `JwtStrategy` acepta (`200 OK`) un JWT firmado con el `JWT_SECRET` real pero sin claim `exp` (`ABUSE-JWT-07-NO-EXP-CLAIM`). | Validar explícitamente `typeof payload.exp === 'number'` en `JwtStrategy.validate()` o exigir presencia obligatoria de `exp`. |
| **`DEF-E6-002`** | **Media-Alta** | `CP-AUTH-11` | Al detectar replay de `RefreshToken` revocado (`401`), `AuthService.refresh` no incrementa `users.tokenVersion` (`tv` sigue en `1` y `AccessToken` hermano mantiene `200`). | Incrementar `tokenVersion` del usuario y revocar la familia activa de tokens cuando se intente reutilizar un `RefreshToken` ya rotado. |
| **`DEF-E6-003`** | **Media** | `CP-AUTH-07` | `PATCH /api/v1/auth/profile` y `DELETE /api/v1/auth/account` devuelven `403` genérico de `STAFF` y no aplican `TemporaryPasswordGuard` (`PASSWORD_CHANGE_REQUIRED`). | Añadir `TemporaryPasswordGuard` en ambos endpoints de `AuthController` para uniformar el contrato perimetral `PASSWORD_CHANGE_REQUIRED`. |
| **`DEF-E6-004`** | **Media** | `CP-AUTH-15` | `H-AUTH-B01`: `StaffService.updateStaff` (`PATCH /api/v1/staff/:id`) omite la emisión de eventos RabbitMQ al cambiar `roles` o `isActive`. | Emitir `staff.roles.updated` / `staff.status.changed` en `updateStaff` igual que en `PUT /roles` y `PATCH /status`. |
| **`DEF-E6-005`** | **Media** | `CP-AUTH-15` | `H-AUTH-B04`: `EventsService` omite `permissions` y `roleLabel` en `data` del CloudEvent. | Enriquecer `StaffEventPayload` con `permissions` consolidados y `roleLabel` antes de publicar en `restaurant.staff.events`. |
| **`DEF-E6-006`** | **Media** | `CP-AUTH-21` | `Auth-Backend/docker-compose.yml` lista `CORS_ORIGINS` hasta `:5178`, excluyendo `:5179` (`cocinaApp`, KDS). | Incorporar `http://localhost:5179` al valor por defecto de `CORS_ORIGINS` y unificar el nombre de variable en `configuration.md`. |
| **`OBS-E6-01`** | Baja | Estático FE | Cobertura global de `Auth-Frontend` en SonarCloud (`83.5%`) sigue por debajo del umbral QA `>= 85%` (p. ej. `ProfileSettingsView.tsx` `53.4%`, `RoleDetailDrawer.tsx` `68.9%`). | Añadir pruebas de componentes sobre perfil y cajón de roles en `Auth-Frontend`. |
| **`OBS-E6-02`** | Baja | Estático BE | `ValidationPipe({ whitelist: true })` sin `forbidNonWhitelisted: true` responde `201` descartando en silencio campos extra (no hay escalación en BD, pero no rechaza con `400`). | Evaluar activar `forbidNonWhitelisted: true` en rutas administrativas sensibles. |
| **`OBS-E6-03`** | Baja | `gitleaks` | Cadena literal de test detectada en commit histórico `964ce9bb` (`guards.spec.ts:82`, valor redactado en JSON). | Sustituir en futuras refactorizaciones por generación dinámica `crypto.randomBytes(32)` y configurar `.gitleaksignore` para fixtures antiguos. |
| **`OBS-E6-04`** | Media | Estático BE/FE | Auditoría AST individual (`function_cc_audit.json`) confirma **`NO CUMPLE CC <= 10`** en **`6/195` funciones de `Auth-Backend`** (`login` `16`, `updateStaff` `16`, `canActivate`/`isPrivateAddress`/`getRolePrefix` `12`, `validateStrongPassword` `11`) y **`18/438` funciones de `Auth-Frontend`** (`Input` `25`, `StaffDetailDrawer` `23`, `getMe` `23`, etc.), además de `1` y `6` incidencias `CRITICAL` (`S3776`) en SonarCloud. | Extraer validaciones y subcomponentes para reducir CC <= 10 por función y resolver las `7` incidencias `typescript:S3776` reportadas por SonarCloud. |
| **`OBS-E6-05`** | Baja | Semilla / `CP-AUTH-04` | La especificación de semilla de TASK-47 refiere `E000001` para el rol dinámico; el SUT asignó **`E000002`** incluso creándolo inmediatamente después de `ADM000001`, por conteo global `count + 1` en `StaffService.generateUniqueStaffId`. Desviación pendiente de aceptación formal. | Confirmar con Eq. 6 si `StaffID` secuencial global (`E000002` tras `ADM000001`) es comportamiento esperado o si debe ser secuencia independiente por prefijo (`E000001`). |

---

# 7. Reproducibilidad, Validación y Limitaciones de Medición

Para reproducir íntegramente esta verificación en macOS/Linux o Windows desde un entorno limpio sin modificar volúmenes existentes:

```bash
cd QA_Equipo5_Auth
./ejecutar_i3.sh                 # Corrida completa aislada (tmpfs)
node scripts/audit_function_cc.mjs   # Auditoría AST individual CC <= 10
```

## 7.1 Comandos de Validación Ejecutados y Resultados Reales

- `./ejecutar_i3.sh` (Corrida 2, proyecto `qa-auth-i3-isolated` con `tmpfs`): código de salida `0` (`SQL_RC=0, BDD_RC=0`); Newman `53/53` (`106/106`), BDD `6/6` (`41/41`), SQL `4/4`, `18/18` `registro.json` (`13 PASA`, `5 FALLA_PARCIAL`).
- `docker volume ls -q | sort | diff -u /tmp/qa_vols_before.txt -`: código `0` (`VOL_DIFF_EMPTY_OK`, `0` volúmenes modificados/eliminados; `ordenes-postgres`, `ordenes-rabbitmq`, `ordenes-redis` intactos).
- `node --check scripts/audit_function_cc.mjs && node scripts/audit_function_cc.mjs`: código `0` (`Auth-Backend: total=195 maxCC=16 over10=6 S3776=1`; `Auth-Frontend: total=438 maxCC=25 over10=18 S3776=6`).
- `pandoc QA_Equipo5_Auth/Entregable_I3_Persistencia_Contratos_BDD_Sonar_Auth.md -o ...pdf --pdf-engine=xelatex ...`: compilación limpia del PDF (`10` páginas generadas sin desbordes ni datos sensibles).
- `ghcr.io/gitleaks/gitleaks:latest git -v /repo --log-opts="origin/develop..HEAD"`: `1` commit escaneado (`3.84 MB`), `no leaks found` (exit `0`).
- Escaneo regex automático sobre `QA_Equipo5_Auth/evidencias/**/*` y `Entregable_I3_*.md`: `0` coincidencias (`REPORT_SCAN_HITS: []`) de JWTs, cookies, hashes bcrypt completos, contraseñas temporales `Fmat-*`, correos personales ni contraseñas BVA.

## 7.2 Limitaciones Reconocidas en las Mediciones

1. **`ANTI-ENUM-02` (5 muestras/rama):** Las `15` peticiones cronometradas son una comprobación funcional exploratoria; **no** constituyen prueba estadística definitiva de tiempo constante (evaluación formal con **Grafana k6** en Iteración 6).
2. **Complejidad SonarCloud API vs. AST:** `measures/component_tree` solo reporta CC agregada por archivo; el cumplimiento individual `CC <= 10` se auditó localmente vía AST McCabe en TypeScript ([`static_reaudit/function_cc_audit.json`](evidencias/static_reaudit/function_cc_audit.json)).
3. **Semilla `E000001`:** Imposibilidad estructural en la API actual de generar `E000001` tras `ADM000001` (`count + 1`); documentada sin alterar evidencia como desviación pendiente (`E000002`).
4. **Aislamiento de puertos y alcance I4–I6:** RabbitMQ del arnés mapeó host `:5673/:15673` (interno `:5672`) por convivencia con `ordenes-rabbitmq`; pruebas de Frontend/Playwright/`@axe-core` (I4), Gateway Kong (I5) y k6/ZAP (I6) quedan fuera del corte I3.

Artículos clave generados automáticamente en `QA_Equipo5_Auth/evidencias/`:

- `metadata_ejecucion.json`, `seed_summary.json`, `resumen_casos_i3.json`
- `newman-report.html`, `newman-report.json`
- `bdd-report.html`, `bdd-report.json`
- `security_abuse_results.json`
- `CP-AUTH-01/bootstrap_concurrency.json`
- `CP-AUTH-15/rabbitmq_events.json`
- `CP-AUTH-21/sql_audit.json`, `CP-AUTH-21/sql_audit.txt`
- `CP-AUTH-01..15,17,18,21/registro.json` (`18` directorios individuales)
- `sonarcloud_metrics.json`, `gitleaks/gitleaks_*.json`, `static_reaudit/jest_auth_backend.log`, `static_reaudit/function_cc_audit.json`
