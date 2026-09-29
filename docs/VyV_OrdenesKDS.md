# Plan de Verificación y Validación (V&V) — Orders & KDS

> **Versión:** 1.0
> **Basado en:** `ERS_ordeneskds.md` (v4) · `Arquitectura_ordeneskds.md` · `Comunicaciones_API_ordenes_y_KDS.md` · `ordenes-kds-diagramas-secuencia.md` · Guía visual FMAT-RESTAURANT v2.0 · Programa de la asignatura *Verificación y Validación de Software* (7.º semestre LIS)
> **Estándar de referencia:** IEEE 829 (documentación de pruebas) · ISO/IEC 25010 (calidad de software)
> **Aplica a:** `ordenes-kds-backend` y `ordenes-kds-frontend` (este documento es **idéntico en ambos repositorios**, en `docs/VyV_OrdenesKDS.md`)
> **Stack bajo prueba:** Python 3.12 · FastAPI · PostgreSQL · Redis · RabbitMQ (backend) — React 18 · TypeScript · Tailwind · Vite Module Federation (frontend)

---

## Tabla de Contenido

1. [Introducción y Alcance](#1-introducción-y-alcance)
2. [Estrategia General de Testing](#2-estrategia-general-de-testing)
3. [Catálogo de Requisitos Trazables](#3-catálogo-de-requisitos-trazables)
4. [Los 6 Aspectos de V&V (equipos expertos)](#4-los-6-aspectos-de-vv-equipos-expertos)
5. [Pruebas Unitarias](#5-pruebas-unitarias)
6. [Pruebas de Integración](#6-pruebas-de-integración)
7. [Pruebas de Contrato y de API (Postman + Newman)](#7-pruebas-de-contrato-y-de-api-postman--newman)
8. [Pruebas de Sistema / E2E (Playwright)](#8-pruebas-de-sistema--e2e-playwright)
9. [Pruebas de Aceptación (BDD y UAT)](#9-pruebas-de-aceptación-bdd-y-uat)
10. [Pruebas No Funcionales y de Rendimiento (k6)](#10-pruebas-no-funcionales-y-de-rendimiento-k6)
11. [Pruebas de Seguridad (OWASP ZAP y otras)](#11-pruebas-de-seguridad-owasp-zap-y-otras)
12. [Entornos, Datos y Herramientas](#12-entornos-datos-y-herramientas)
13. [Criterios de Entrada y Salida](#13-criterios-de-entrada-y-salida)
14. [Matriz de Riesgos](#14-matriz-de-riesgos)
15. [Riesgos de Integración entre Equipos (hallazgos de contrato)](#15-riesgos-de-integración-entre-equipos-hallazgos-de-contrato)
16. [Métricas y Dashboard de Calidad](#16-métricas-y-dashboard-de-calidad)
17. [Cronograma](#17-cronograma)
18. [Matriz de Trazabilidad RF/RNF → Pruebas](#18-matriz-de-trazabilidad-rfrnf--pruebas)

---

## 1. Introducción y Alcance

### 1.1 Propósito

Este documento define **qué se prueba, cómo, con qué herramientas y con qué criterios de éxito** en el microservicio Órdenes y Cocina (Microservicio 4).

- **Verificación:** ¿estamos construyendo el sistema *correctamente*? (el código cumple `DEVELOPMENT_GUIDELINES.md`, pasa el análisis estático y sus pruebas).
- **Validación:** ¿estamos construyendo el sistema *correcto*? (el comportamiento satisface el ERS v4 y sirve al personal real del restaurante).

Es además el plan de V&V que este equipo presenta en la asignatura: integra **las 6 herramientas/aspectos** que los seis equipos exponen (CI/CD, análisis estático, E2E, API/contratos, rendimiento, seguridad + BDD) y que **todos** los equipos deben mantener corriendo en su *pipeline* (ver [§4](#4-los-6-aspectos-de-vv-equipos-expertos)).

### 1.2 Alcance

| Capa | Tecnología | Niveles de prueba aplicables |
|---|---|---|
| API REST + WebSocket | FastAPI | Unitaria, Integración, Contrato, Sistema, Rendimiento, Seguridad |
| Dominio y servicios | Python | Unitaria, Integración, BDD |
| Mensajería (outbox, consumers, DLQ) | RabbitMQ + aio-pika | Integración, Contrato de eventos, Rendimiento |
| Persistencia | PostgreSQL 16, Redis 7 | Integración (Testcontainers) |
| KDS App (pantalla de cocina) | React, Bump Bar | Unitaria, Integración (MSW), E2E, Accesibilidad, UAT |
| Order Ticket Widget (mesero) | React (componente federado) | Unitaria, Integración, E2E, UAT |
| Intermediate Dishes App *(opcional)* | React | Unitaria, E2E |
| Federation / estilos aislados | Vite Federation, Tailwind | Integración, E2E (host stub) |

### 1.3 Fuera de alcance

- Pruebas internas de los otros microservicios (Auth, Menú, Sala, Inventario, Pagos): se **simulan** con *stubs de contrato* y se verifican **solo** sus contratos con Órdenes.
- Rendimiento de RabbitMQ, PostgreSQL o Redis como productos (solo su comportamiento bajo nuestra carga).
- Las pantallas de Sala y Menú (otros equipos); solo se prueba el **Order Ticket Widget** que Menú incrusta.
- Navegadores obsoletos; el KDS y el handheld usan navegadores controlados (Chromium/WebKit modernos).

### 1.4 Convenciones del documento

- Los **IDs de requisito** (`RF-XX`, `RNF-XX`) los define este documento en la [§3](#3-catálogo-de-requisitos-trazables); el ERS v4 no numera sus requisitos (se recomienda añadir estos IDs en su próxima revisión).
- Los umbrales marcados **(propuesto)** no vienen de los documentos fuente: son propuestas de este plan que el equipo debe validar.
- Todo el código de prueba (tests, `.feature`, colecciones Postman, scripts k6) está **en inglés**; este documento está en español para el equipo.

---

## 2. Estrategia General de Testing

### 2.1 Pirámide de testing

```
              ┌──────────────────────────────┐
              │  Aceptación (BDD / UAT)      │  ← menos, más lentas, más valor de negocio
              ├──────────────────────────────┤
              │  Sistema E2E (Playwright)    │
              │  Rendimiento (k6) · ZAP      │
              ├──────────────────────────────┤
              │  Contrato y API (Newman)     │
              ├──────────────────────────────┤
              │  Integración (Testcontainers,│
              │  MSW, broker real)           │
              ├──────────────────────────────┤
              │  Unitarias (pytest, Vitest)  │  ← más, más rápidas, más granulares
              └──────────────────────────────┘
        Base transversal: análisis estático (SonarCloud) + CI/CD (GitHub Actions)
```

### 2.2 Principios

1. **Independencia:** cada prueba es autónoma; no depende del estado que dejó otra.
2. **Determinismo:** mismo resultado siempre (reloj inyectable, sin `sleep`, sin red real en unitarias).
3. **Trazabilidad:** cada prueba referencia al menos un `RF-XX`/`RNF-XX` (marcador `requirement`, ver Guidelines §11.3).
4. **Automatización prioritaria:** lo manual (UAT con hardware real) es la excepción y queda registrado.
5. **Shift-left:** las pruebas unitarias y estáticas corren en cada *push*; las lentas, en cada PR o por la noche.
6. **Pruebas basadas en riesgo:** más profundidad donde el impacto de fallar es mayor (máquina de estados, outbox, cobro) — ver [§14](#14-matriz-de-riesgos).
7. **Calidad distribuida:** al ser microservicios, se prueba cada servicio por separado **y** sus contratos con los demás.

### 2.3 Nomenclatura

```text
# Backend (pytest) — test_<unit>_<scenario>_<expected>
test_cancel_order_when_in_preparation_raises_invalid_transition
test_void_kitchen_error_does_not_request_charge

# Frontend (Vitest) — describe/it con lenguaje de usuario
describe('OrderTicketWidget', () => {
  it('disables cancel once the order is in preparation')
})

# BDD (Gherkin)
Feature: Void an order because of a kitchen error
  Scenario: The customer is not charged
```

### 2.4 Convención de IDs de prueba

| Prefijo | Nivel | Sección |
|---|---|---|
| `UT-BE-xx` / `UT-FE-xx` | Unitarias backend / frontend | §5 |
| `IT-xx` / `IT-FE-xx` | Integración backend / frontend | §6 |
| `CT-xx` | Contrato y API (Newman + esquemas de eventos) | §7 |
| `ST-xx` | Sistema / E2E (Playwright) | §8 |
| `AT-xx` / `UAT-xx` | Aceptación BDD / UAT manual | §9 |
| `PT-xx` / `NF-xx` | Rendimiento (k6) / otras no funcionales | §10 |
| `SEC-xx` | Seguridad | §11 |
| `SA-xx` / `CI-xx` | Análisis estático / pipeline | §4 |

### 2.5 Calidad ISO/IEC 25010 → dónde se verifica

| Característica | Requisitos | Cómo se verifica |
|---|---|---|
| Adecuación funcional | RF-01…RF-41 | Unitarias, integración, BDD, E2E |
| Eficiencia de desempeño | RNF-06 | k6 (PT-xx), métricas del outbox |
| Compatibilidad | RNF-08, RNF-13 | Contratos de eventos/REST, Federation en *host stub* |
| Usabilidad / accesibilidad | RNF-07, RNF-11 | E2E con teclado, axe-core, UAT con Bump Bar real |
| Fiabilidad | RNF-01, RNF-02, RNF-05, RNF-09 | Integración con broker real, DLQ, reconciliación |
| Seguridad | RNF-03 | ZAP, matriz de roles, pruebas de JWT/WebSocket |
| Mantenibilidad | RNF-10, RNF-12 | SonarCloud (Quality Gate), complejidad, logs |
| Portabilidad | — | Docker Compose reproducible en CI y local |

---

## 3. Catálogo de Requisitos Trazables

Fuente: ERS v4 (§2–§9) y Arquitectura/Comunicaciones (marcados *Arq.*). Los IDs son **asignados por este plan**.

### 3.1 Requisitos funcionales

| ID | Requisito | Fuente |
|---|---|---|
| RF-01 | Agrupar comandas en el KDS según su estado | ERS §2 |
| RF-02 | Ordenar comandas por prioridad (tiempo de preparación) | ERS §2 |
| RF-03 | Desempatar cronológicamente a igual prioridad | ERS §2 |
| RF-04 | Remover la comanda del KDS al pasar a `PAID` | ERS §2 |
| RF-05 | Mostrar platillos, modificadores y notas en cada tarjeta | ERS §3 |
| RF-06 | Mostrar la mesa asignada en cada tarjeta | ERS §3 |
| RF-07 | Mostrar cronómetro del tiempo transcurrido | ERS §3 |
| RF-08 | Filtrar platillos por estación (categoría de Catálogo) | ERS §3 |
| RF-09 | Marcar un platillo como "Listo" | ERS §4 |
| RF-10 | Publicar `ordenes.platillo.preparado` al marcar "Listo" | ERS §4 |
| RF-11 | Marcar la comanda completa como "Listo" | ERS §4 |
| RF-12 | Crear comanda vinculada a una mesa | ERS §5 |
| RF-13 | Crear comanda para llevar | ERS §5 |
| RF-14 | Agregar platillos, modificadores y notas | ERS §5 |
| RF-15 | Registrar el usuario autenticado (desde el JWT) en cada creación/modificación | ERS §5 |
| RF-16 | Transferir una comanda de mesa | ERS §5 |
| RF-17 | Impedir seleccionar un platillo no disponible (vista local) | ERS §5 |
| RF-18 | Actualizar la UI en cada transición de estado (ciclo principal y alternativos) | ERS §6 |
| RF-19 | Emitir notificaciones de cambio de estado a módulos externos (eventos) | ERS §6 |
| RF-20 | Publicar `ordenes.cuenta.solicitada` con el detalle íntegro (precios congelados) | ERS §6 |
| RF-21 | Pasar a `PAID` **solo** al consumir `pagos.pago.completado` | ERS §6 |
| RF-22 | Rehacer un platillo `DELIVERED → PENDING` con nota obligatoria | ERS §6 |
| RF-23 | Modificar platillos solo en `CREATED` | ERS §7 |
| RF-24 | Cancelar comanda solo en `CREATED` | ERS §7 |
| RF-25 | Publicar `ordenes.orden.cancelada` y pasar a `CANCELLED` (sin cobro) | ERS §7 |
| RF-26 | Solo `admin` anula en `IN_PREPARATION` por razón administrativa → `WASTED` | ERS §7 |
| RF-27 | `WASTED`: publicar `comanda.mermada` + `cobro.solicitado`; se cobra como entrega normal | ERS §7 |
| RF-28 | Anulación por error de cocina (`admin` o `chef`): cancela la comanda **completa** → `VOIDED` | ERS §7.1 |
| RF-29 | Nota obligatoria en la anulación por error de cocina | ERS §7.1 |
| RF-30 | `VOIDED`: publicar `comanda.mermada` (insumos consumidos no se liberan) | ERS §7.1 |
| RF-31 | `VOIDED`: **nunca** publicar evento de cobro | ERS §7.1 |
| RF-32 | Publicar `ordenes.orden.creada` al confirmar | ERS §8 |
| RF-33 | Enviar al KDS y pasar a `IN_PREPARATION` **solo** tras `inventario.stock.reservado` | ERS §8 |
| RF-34 | Pasar a `REJECTED` y alertar al mesero tras `inventario.stock.insuficiente` | ERS §8 |
| RF-35 | Pasar a `EXPIRED` si Inventario no responde en el tiempo límite | ERS §8 |
| RF-36 | *(Opc.)* El chef registra un lote de platillo intermedio | ERS §9 |
| RF-37 | *(Opc.)* Publicar `ordenes.platillo_intermedio.preparado` | ERS §9 |
| RF-38 | *(Opc.)* Vista nueva y dedicada al chef | ERS §9 |
| RF-39 | Consumir `sala.dining_session.iniciada` y crear la orden base (`sessionId`) | Arq. §4.3 |
| RF-40 | Mantener la vista materializada de catálogo con eventos `menu.*` | Arq. §4.3 |
| RF-41 | Sincronización de arranque y reconciliación periódica con Catálogo | Arq. §3.4 |

### 3.2 Requisitos no funcionales

| ID | Requisito | Fuente |
|---|---|---|
| RNF-01 | **Atomicidad** estado + evento (Transactional Outbox) | Arq. §4.1 |
| RNF-02 | **Idempotencia** de consumers y publicaciones (at-least-once) | Arq. §4.4 |
| RNF-03 | **Seguridad:** JWT, autorización por rol, errores genéricos, sin inyección/XSS | Arq. §1, §3.1 |
| RNF-04 | **Tiempo real por WebSocket**, sin *polling*; recuperación tras reconexión | Comunicaciones §1.3 |
| RNF-05 | **Resiliencia** ante caída de Menú (seguir con la última copia local) | Arq. §3.4 |
| RNF-06 | **Rendimiento:** `p95 < 500 ms`, errores `< 1 %`; latencia de push y de outbox acotadas *(propuesto)* | Curso (k6) |
| RNF-07 | **Operabilidad de cocina:** KDS landscape, **solo teclado** (Bump Bar), sin puntero | UI Spec §1 |
| RNF-08 | **Consistencia visual y aislamiento de estilos** (FMAT, sin CSS global) | README FE / FMAT |
| RNF-09 | **Concurrencia optimista** (columna `version`, `409` ante conflicto) | README BE |
| RNF-10 | **Mantenibilidad / calidad estática** (Quality Gate de SonarCloud) | Guidelines / Curso |
| RNF-11 | **Accesibilidad y usabilidad:** foco visible, contraste AA, color + texto, táctil ≥ 44/48 px | FMAT §12 |
| RNF-12 | **Observabilidad:** logs estructurados con `correlation_id` *(propuesto)* | Guidelines §5.9 |
| RNF-13 | **Compatibilidad de contratos** REST y de eventos (esquemas versionados) | Guidelines §6.5, §9 |

---

## 4. Los 6 Aspectos de V&V (equipos expertos)

La asignatura organiza el proyecto como una **"cadena de montaje de calidad"** de 6 herramientas. Cada equipo se vuelve experto en una (masterclass en las semanas 3–8) y, a partir de la semana 9, **todos los equipos deben mantener las 6 corriendo en su *pipeline***. Este plan define cómo se aplica cada aspecto a Orders & KDS.

| # | Aspecto (rol en la cadena) | Herramienta | Unidad | Qué aporta a Orders & KDS | Sección |
|---|---|---|---|---|---|
| 1 | **CI/CD** — *La carretera* | GitHub Actions | 6, 12 | Pipeline donde corre todo lo demás | §4.1 |
| 2 | **Análisis estático y deuda técnica** — *El semáforo* | SonarCloud | 5 | Quality Gate que detiene código "sucio" | §4.2 |
| 3 | **Pruebas de UI (E2E)** — *Los ojos* | Playwright | 10 | Valida que cocina y meseros puedan operar | §4.3 → §8 |
| 4 | **API testing y contratos** — *El corazón* | Postman + Newman | 7, 11 | Valida datos, reglas y contratos REST/eventos | §4.4 → §7 |
| 5 | **Rendimiento** — *El pulso* | k6 | 9 | Garantiza que no colapsa en hora pico | §4.5 → §10 |
| 6 | **Seguridad (DAST) y BDD** — *El escudo* | OWASP ZAP + Cucumber/Gherkin | 8, 9 | Protege contra ataques y alinea con el negocio | §4.6 → §9, §11 |

> **Nota de numeración.** Las diapositivas de la asignatura numeran los equipos de forma distinta en distintos lugares (p. ej. "Equipo 1 = Sonar" en una y "Equipo 1 = GitHub Actions" en otra). Este plan usa el **orden de la cadena de montaje** (CI/CD primero, porque es la infraestructura sobre la que los demás corren) y no depende de los números de equipo.

### 4.0 Responsables y alineación con la rúbrica

| Aspecto | Herramienta | Responsable(s) en este equipo | Suplente |
|---|---|---|---|
| CI/CD | GitHub Actions | *(completar)* | *(completar)* |
| Análisis estático | SonarCloud | *(completar)* | *(completar)* |
| UI / E2E | Playwright | *(completar)* | *(completar)* |
| API y contratos | Postman + Newman | *(completar)* | *(completar)* |
| Rendimiento | k6 | *(completar)* | *(completar)* |
| Seguridad + BDD | OWASP ZAP + Cucumber | *(completar)* | *(completar)* |

**Cómo este plan cubre la rúbrica de evaluación (100 %):**

| Criterio de la rúbrica | Peso | Evidencia que aporta este plan |
|---|---|---|
| Dominio técnico y configuración | 30 % | Cada herramienta integrada en CI con reglas **personalizadas** al proyecto (Quality Gate a medida, umbrales k6, reglas ZAP), no "out of the box" — §4.1–§4.6 |
| Calidad de la masterclass y material didáctico | 30 % | *Quick Start* por herramienta que permita configurarla en **< 20 minutos** — sección "Entregables" de cada §4.x y `CONTRIBUTING.md` |
| Soporte y consultoría | 20 % | Canal de soporte activo hacia los otros 5 equipos; registro de consultas atendidas — §16.4 |
| Informe de métricas y resultados (Unidad 14) | 20 % | Dashboard **antes/después** con DRE, cobertura, tiempos, vulnerabilidades y ROI de la automatización — §16 |

---

### 4.1 Aspecto 1 — CI/CD con GitHub Actions

**Objetivo (reto de la asignatura):** ningún código llega a la rama principal sin haber sido verificado. El pipeline es un **estándar copiable** por los demás equipos.

**Especificación técnica**

| Requisito del reto | Cómo se cumple en Orders & KDS |
|---|---|
| *Triggers* | `push` a `main`/`develop` y `pull_request` hacia esas ramas |
| Entorno limpio | *Runner* `ubuntu-latest` con Python 3.12 (backend) / Node 20 + pnpm 9 (frontend); servicios PostgreSQL/Redis/RabbitMQ como *service containers* o `docker compose` |
| Instalación automatizada | `pip install -r requirements/dev.txt` / `pnpm install --frozen-lockfile`, con caché |
| Ejecución de pruebas | `pytest` / `vitest`; **el pipeline falla si una sola prueba falla** |
| Persistencia de resultados | JUnit XML, cobertura (`coverage.xml`, `lcov.info`) y reportes HTML como **artifacts** |
| Alerta ante fallo | Paso `if: failure()` que notifica por webhook a Slack/Discord |

**Grafo de trabajos (backend)**

```
lint-and-types ─► unit-tests ─┬─► sonarcloud (Quality Gate)
                              ├─► integration-tests (Testcontainers)
                              │        └─► api-and-contract (Newman) ─► bdd
                              │                                     └─► k6-smoke
                              └─► zap-api-scan
                                             └─► notify-on-failure
```

**Esqueleto de `ci.yml` (backend)** — el equipo de CI/CD entrega la versión definitiva y comentada línea por línea como `template.yml`:

```yaml
name: ci

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint-and-types:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -r requirements/dev.txt
      - run: ruff check . && ruff format --check . && mypy app

  unit-tests:
    needs: lint-and-types
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -r requirements/dev.txt
      - run: >
          pytest tests/unit --cov=app --cov-branch
          --cov-report=xml --junitxml=reports/unit.xml
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: unit-reports
          path: |
            reports/unit.xml
            coverage.xml

  sonarcloud:
    needs: unit-tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }          # SonarCloud needs full history for blame/new-code detection
      - uses: actions/download-artifact@v4
        with: { name: unit-reports }
      - uses: SonarSource/sonarqube-scan-action@v5   # pin the latest release/SHA in the real workflow
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
      - uses: SonarSource/sonarqube-quality-gate-action@v1
        timeout-minutes: 5
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}

  integration-tests:
    needs: unit-tests
    runs-on: ubuntu-latest                 # Docker is preinstalled → Testcontainers works
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -r requirements/dev.txt
      - run: pytest tests/integration --junitxml=reports/integration.xml
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: integration-reports, path: reports/ }

  api-and-contract:
    needs: integration-tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f docker-compose.ci.yml up -d --build --wait
      - run: |
          npx --yes newman run tests/api/postman/orders-kds.postman_collection.json \
            -e tests/api/postman/ci.postman_environment.json \
            --reporters cli,junit --reporter-junit-export reports/newman.xml
      - run: pytest tests/contract tests/bdd --junitxml=reports/contract-bdd.xml
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: api-contract-reports, path: reports/ }

  k6-smoke:
    needs: api-and-contract
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f docker-compose.ci.yml up -d --build --wait
      - uses: grafana/setup-k6-action@v1
      - run: k6 run tests/performance/smoke.js --summary-export=reports/k6-summary.json
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: k6-report, path: reports/k6-summary.json }

  zap-api-scan:
    needs: api-and-contract
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f docker-compose.ci.yml up -d --build --wait
      - uses: zaproxy/action-api-scan@v0.9.0          # pin the latest release in the real workflow
        with:
          target: http://localhost:8000/openapi.json
          format: openapi
          rules_file_name: .zap/rules.tsv               # High risk = FAIL, the rest = WARN
      # The action uploads the HTML report as an artifact automatically.

  notify-on-failure:
    needs: [lint-and-types, unit-tests, sonarcloud, integration-tests, api-and-contract, k6-smoke, zap-api-scan]
    if: failure()
    runs-on: ubuntu-latest
    steps:
      - run: |
          curl -X POST -H 'Content-type: application/json' \
            --data "{\"text\":\"CI failed on ${{ github.repository }}@${{ github.ref_name }} — ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}\"}" \
            "${{ secrets.ALERT_WEBHOOK_URL }}"
```

**Frontend (`ci.yml`)** — mismos disparadores y misma estructura; cambian los trabajos:

| Job | Comando principal | Artifact |
|---|---|---|
| `lint-and-types` | `pnpm lint && pnpm typecheck` | — |
| `unit-tests` | `pnpm test --coverage` (Vitest, `lcov`) | `coverage/lcov.info`, JUnit |
| `sonarcloud` | Sonar scan + Quality Gate | — |
| `build-federation` | `pnpm build` (genera `remoteEntry.js`) | `dist/` |
| `e2e` | `pnpm exec playwright install --with-deps chromium webkit && pnpm test:e2e` (*headless*) | `playwright-report/`, videos y trazas |
| `bdd` | `pnpm test:bdd` (Cucumber.js + Playwright) | reporte HTML/JSON de Cucumber |
| `a11y` | `pnpm test:a11y` (axe-core) | reporte axe |
| `zap-baseline` | ZAP *baseline* contra `pnpm preview` | reporte HTML |

**Protección de ramas (obligatorio)**

- `main` y `develop`: sin *push* directo; PR con ≥ 1 aprobación; el autor no se aprueba a sí mismo.
- *Checks* requeridos: `lint-and-types`, `unit-tests`, `sonarcloud` (Quality Gate), `integration-tests`, `api-and-contract`, `zap-api-scan`. En frontend: `e2e`, `a11y`.
- Los secretos (`SONAR_TOKEN`, `ALERT_WEBHOOK_URL`, credenciales de staging) viven **solo** en *GitHub Secrets*.

**Casos de verificación del pipeline**

| ID | Verificación | Resultado esperado |
|---|---|---|
| CI-01 | Abrir un PR / hacer *push* a `main` | El pipeline se dispara automáticamente |
| CI-02 | Entorno limpio + instalación desde cero | Sin dependencias implícitas de la máquina local |
| CI-03 | Romper una prueba a propósito (*Demo de fallo*) | El pipeline marca ❌ y **bloquea el merge** |
| CI-04 | Fallo de cualquier job | Llega la alerta al canal de Slack/Discord |
| CI-05 | Ejecución exitosa | Se publican como *artifacts* JUnit, cobertura y reportes HTML |
| CI-06 | Intentar *merge* con *checks* en rojo | La protección de rama lo impide |
| CI-07 | Ningún secreto impreso en logs ni presente en el repositorio | gitleaks sin hallazgos |
| CI-08 | Tiempo total del pipeline en PR | ≤ 15 min *(propuesto)* — trabajos lentos en paralelo o nocturnos |

**Entregables (masterclass):** guía *Quick Start* de 1 página (5 pasos para activar Actions en un proyecto nuevo) · `template.yml` comentado línea por línea (qué es *job*, *step*, *action*) · demo de fallo · cómo leer los logs de un job fallido · laboratorio para los demás equipos (*fork* → carpeta `.github/workflows/` → pegar el código → *commit* que rompa una prueba y ver la ❌).

---

### 4.2 Aspecto 2 — Análisis estático y deuda técnica (SonarCloud)

**Objetivo (reto):** que el pipeline no solo corra pruebas, sino que **suspenda** el código que no cumple estándares de calidad.

**Configuración objetivo**

| Elemento | Configuración |
|---|---|
| Conexión | `SONAR_TOKEN` en *GitHub Secrets*; `sonar-project.properties` en cada repo |
| Cobertura importada | Backend: `sonar.python.coverage.reportPaths=coverage.xml` · Frontend: `sonar.javascript.lcov.reportPaths=coverage/lcov.info` |
| Exclusiones | Migraciones de Alembic, `tests/`, código generado, mocks |
| Análisis de PR | SonarCloud comenta en GitHub las líneas problemáticas (*PR decoration*) |
| Análisis en cada *push* | Dentro de `ci.yml` (job `sonarcloud`), tras las pruebas unitarias |

**Quality Gate personalizado** (no el "Sonar way" por defecto)

| Condición | Umbral | Sobre |
|---|---|---|
| Vulnerabilidades críticas/bloqueantes | **0** | Todo el código |
| Código duplicado | **< 3 %** | Código nuevo |
| Fiabilidad | Calificación **A** | Código nuevo |
| Seguridad | Calificación **A** | Código nuevo |
| Mantenibilidad | Calificación **A** (ratio de deuda ≤ 5 %) | Código nuevo |
| Cobertura | **≥ 85 %** | Código nuevo *(y ≥ 85 % global, ver §13)* |
| *Security hotspots* revisados | **100 %** | Código nuevo |
| Complejidad cognitiva por función | ≤ 15 (regla S3776) | Todo el código |

**Casos de verificación**

| ID | Verificación | Resultado esperado |
|---|---|---|
| SA-01 | PR que cumple todo | Quality Gate **Passed** (verde) |
| SA-02 | PR con una vulnerabilidad crítica introducida a propósito | Gate **Failed**; el PR no se puede integrar |
| SA-03 | PR con bloque duplicado > 3 % | Gate **Failed** |
| SA-04 | PR con cobertura de código nuevo < 85 % | Gate **Failed** |
| SA-05 | PR con función de complejidad > 15 | *Issue* de mantenibilidad; Gate según calificación |
| SA-06 | Comentarios automáticos en el PR | Aparecen en las líneas afectadas |
| SA-07 | `ruff`/`mypy` (backend) y `eslint`/`tsc` (frontend) | 0 errores antes del análisis de Sonar |
| SA-08 | Dashboard | Ratio de deuda técnica ≤ 5 % (A) y tiempo estimado de remediación reportado |

**Entregables (masterclass):** "Diccionario del Auditor" (diferencia entre *Bug*, *Vulnerabilidad* y *Code Smell*) · guía para vincular un repo a SonarCloud · **Manual de Supervivencia** ("cómo leer un reporte de Sonar" y priorizar lo crítico) · registro de **≥ 3 *code smells*** encontrados en el proyecto y su corrección (`docs/quality/code-smells.md`) · explicación de la **deuda técnica** y cuánto tiempo (días/horas) costaría corregir el proyecto base · laboratorio: cuenta gratuita → vincular repo → añadir el paso Sonar al `ci.yml` → dejar el Dashboard en **verde (Passed)**.

---

### 4.3 Aspecto 3 — Pruebas de interfaz de usuario / E2E (Playwright)

**Objetivo (reto):** ser los "defensores del usuario final": una suite E2E **rápida, no flaky y con evidencia visual**. Los casos concretos están en [§8](#8-pruebas-de-sistema--e2e-playwright).

**Configuración objetivo**

| Requisito del reto | Cómo se cumple |
|---|---|
| *Multi-browser* | Proyectos `chromium` **y** `webkit` en `playwright.config.ts` |
| *Page Object Model* | `tests/e2e/pages/` (`KdsPage`, `OrderTicketPage`, `IntermediateDishesPage`): si un botón cambia, se corrige en **un** archivo |
| Evidencia | `screenshot: 'only-on-failure'`, `video: 'retain-on-failure'`, `trace: 'retain-on-failure'`, reporte HTML |
| Ejecución en CI | Modo *headless* dentro de GitHub Actions, en cada *push*/PR |
| Selectores robustos | Solo `getByRole`/`data-testid` (nunca XPath frágil); *auto-waiting* de Playwright, sin esperas fijas |
| Pruebas visuales | `toHaveScreenshot()` en la tarjeta KDS y el ticket |

```typescript
// playwright.config.ts (essential parts)
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  retries: process.env.CI ? 1 : 0,
  reporter: [['html', { open: 'never' }], ['junit', { outputFile: 'reports/e2e.xml' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
});
```

**Entregables (masterclass):** demo de **Codegen** (grabar un test) · clase de *selectors* (por qué `data-testid` > XPath) · explicación del *auto-waiting* (por qué es mejor que Selenium) · demo de POM · laboratorio: `npm init playwright@latest` → un flujo crítico → ver el reporte HTML → ver el *check* verde en Actions.

---

### 4.4 Aspecto 4 — API testing y pruebas de contrato (Postman + Newman)

**Objetivo (reto):** ser los "guardianes de la lógica": que la API responda **lo que promete**, con el formato correcto y en el tiempo esperado. Los casos están en [§7](#7-pruebas-de-contrato-y-de-api-postman--newman).

**Configuración objetivo**

| Requisito del reto | Cómo se cumple |
|---|---|
| Validación de esquemas | `ajv` (o `tv4`) dentro de los scripts *Tests* de Postman: falla si la API cambia un campo sin avisar |
| Pruebas de flujo | Colección encadenada: crear → agregar ítems → confirmar → listo → cuenta → pagada |
| Variables de entorno | Nada de URLs fijas: `{{url}}`, `{{token_waiter}}`, `{{token_kitchen}}`, `{{token_chef}}`, `{{token_admin}}` en `local`/`ci`/`staging` |
| Newman en CI | `newman run …` como paso del pipeline, con reporte JUnit |
| Colección importable | `tests/api/postman/` exportada para importarla y usarla "en 1 click" |

```javascript
// Postman "Tests" tab — contract check with ajv
const schema = {
  type: 'object',
  required: ['id', 'status', 'items', 'version'],
  properties: {
    id: { type: 'string', format: 'uuid' },
    status: { enum: ['CREATED','IN_PREPARATION','DELIVERED','PAID','CANCELLED','REJECTED','WASTED','VOIDED','EXPIRED'] },
    items: { type: 'array' },
    version: { type: 'integer' },
  },
  additionalProperties: true,
};
pm.test('status is 201', () => pm.response.to.have.status(201));
pm.test('body matches the Order contract', () => {
  const Ajv = require('ajv');
  const validate = new Ajv({ allErrors: true }).compile(schema);
  pm.expect(validate(pm.response.json()), JSON.stringify(validate.errors)).to.be.true;
});
```

**Entregables (masterclass):** anatomía de una petición (verbos HTTP y códigos `400` vs `404` vs `500`) · scripts de test en JS · demo de Newman ("una terminal negra, sin abrir Postman") · laboratorio: importar la colección → crear un **escenario de error** (pedir una orden inexistente → `404`) → instalar Newman → añadir el comando al CI.

---

### 4.5 Aspecto 5 — Rendimiento y resiliencia (k6)

**Objetivo (reto):** descubrir el **punto de ruptura**: una aplicación que funciona para 1 usuario puede fallar catastróficamente para 100. Los escenarios están en [§10](#10-pruebas-no-funcionales-y-de-rendimiento-k6).

**Configuración objetivo**

| Requisito del reto | Cómo se cumple |
|---|---|
| Escenario de carga | *Ramp-up* 0 → 50 VUs en 1 min, mantener y bajar (PT-01) |
| Umbrales (*thresholds*) | `p95 < 500 ms` y errores `< 1 %`: **el pipeline falla** si se incumplen |
| Prueba de estrés | Subir hasta romper e identificar qué falla primero: BD, CPU o memoria (PT-02) |
| CI/CD | Job `k6-smoke` tras cada despliegue a *staging*; la corrida completa, nocturna o por etiqueta de PR (*Performance Regression*) |
| Análisis | Punto exacto en que el tiempo de respuesta supera **2 s** (con 100+ VUs) |

**Entregables (masterclass):** las "métricas de oro" (**latencia**, ***throughput***, ***error rate***) · cómo leer la tabla de resultados de k6 · por qué medir el rendimiento **antes** de un evento de alto tráfico · laboratorio: instalar k6 → ejecutar el script contra su propia API → subir a **200 VUs** y observar el cambio → subir el reporte a su documentación.

---

### 4.6 Aspecto 6 — Seguridad (DAST) y BDD (OWASP ZAP + Cucumber)

**Objetivo (reto):** software **confiable** (hace lo que debe) y **seguro** (no permite lo que no debe). Casos en [§9](#9-pruebas-de-aceptación-bdd-y-uat) (BDD) y [§11](#11-pruebas-de-seguridad-owasp-zap-y-otras) (seguridad).

**Parte A — BDD**

| Requisito del reto | Cómo se cumple |
|---|---|
| Escritura Gherkin | `.feature` con *Given/When/Then* para los flujos críticos (§9.1), casos de éxito **y** de error |
| Automatización (*step definitions*) | Backend: **pytest-bdd** (equivalente Python de Cucumber) contra la API. Frontend: **Cucumber.js + Playwright** contra la UI |
| Documentación viva | Reporte HTML de Cucumber publicado como *artifact* (y opcionalmente en GitHub Pages) legible por quien no programa |
| Buenas prácticas | Lenguaje del negocio: sin detalles técnicos (URLs, selectores) en los `.feature` |

**Parte B — Seguridad DAST**

| Requisito del reto | Cómo se cumple |
|---|---|
| Escaneo automatizado | OWASP ZAP contra la API (`openapi.json`) y contra el frontend (*baseline*) — OWASP Top 10 |
| Detección de riesgos | Informe con ≥ 1 vulnerabilidad común identificada y explicada (cabeceras de seguridad, versiones obsoletas…) |
| Integración en CI | El job falla ante riesgo **Alto** (`.zap/rules.tsv`); el **reporte HTML** se guarda como *artifact* |

**Entregables (masterclass):** "El lenguaje del negocio" (escribir Gherkin correctamente) · demo de ataque de ZAP y su reporte · cultura *shift-left* (por qué es más barato hallar el fallo ahora) · laboratorio: escribir un escenario Gherkin de una funcionalidad nueva → ejecutar ZAP *baseline* contra el servidor local → listar las **3 cosas más importantes** por corregir.

---

## 5. Pruebas Unitarias

Verifican unidades aisladas; solo las **fronteras** (broker, HTTP externo, reloj) se sustituyen por dobles.
**Herramientas:** `pytest` + `pytest-asyncio` + `pytest-cov` (backend) · Vitest + React Testing Library (frontend).
**Cobertura objetivo:** ver [§13.3](#133-cobertura-diferenciada-por-riesgo).

### 5.1 Backend — máquina de estados del dominio (`tests/unit/domain/`)

| ID | Nombre del test | Escenario | Resultado esperado | Req. |
|---|---|---|---|---|
| UT-BE-01 | `test_transition_created_to_in_preparation_is_allowed` | `CREATED → IN_PREPARATION` | Permitido | RF-33 |
| UT-BE-02 | `test_transition_created_to_rejected_is_allowed` | `CREATED → REJECTED` | Permitido | RF-34 |
| UT-BE-03 | `test_transition_created_to_cancelled_is_allowed` | `CREATED → CANCELLED` | Permitido | RF-24 |
| UT-BE-04 | `test_transition_created_to_expired_is_allowed` | `CREATED → EXPIRED` | Permitido | RF-35 |
| UT-BE-05 | `test_transition_in_preparation_to_delivered_is_allowed` | `IN_PREPARATION → DELIVERED` | Permitido | RF-18 |
| UT-BE-06 | `test_transition_in_preparation_to_wasted_is_allowed` | `IN_PREPARATION → WASTED` | Permitido | RF-26 |
| UT-BE-07 | `test_transition_in_preparation_to_voided_is_allowed` | `IN_PREPARATION → VOIDED` | Permitido | RF-28 |
| UT-BE-08 | `test_transition_to_paid_allowed_from_delivered_and_wasted` | *parametrizado* `DELIVERED`, `WASTED` → `PAID` | Permitido | RF-21 |
| UT-BE-09 | `test_every_transition_outside_the_table_raises` | *parametrizado* sobre la matriz completa 9×9 menos las válidas | `InvalidStateTransitionError` | RF-18 |
| UT-BE-10 | `test_final_states_have_no_outgoing_transitions` | `PAID, CANCELLED, REJECTED, VOIDED, EXPIRED` | Sin transiciones salientes | RF-18 |
| UT-BE-11 | `test_cancel_when_in_preparation_raises_error` | Cancelar en `IN_PREPARATION` | Error de dominio | RF-24 |
| UT-BE-12 | `test_edit_items_when_not_created_raises_error` | Editar ítems fuera de `CREATED` | Error de dominio | RF-23 |

### 5.2 Backend — servicios (`tests/unit/services/`)

| ID | Nombre del test | Escenario | Resultado esperado | Req. |
|---|---|---|---|---|
| UT-BE-13 | `test_confirm_order_writes_state_and_outbox_in_same_unit_of_work` | Confirmar | Estado + fila `outbox` en **una** transacción | RF-32, RNF-01 |
| UT-BE-14 | `test_confirm_order_twice_does_not_publish_second_event` | Doble confirmación | Un solo `ORDER_CREATED` *(supuesto OP-05)* | RF-32 |
| UT-BE-15 | `test_cancel_order_in_created_publishes_order_cancelled` | Cancelar en `CREATED` | `CANCELLED` + evento | RF-25 |
| UT-BE-16 | `test_void_administrative_by_waiter_is_forbidden` | Mesero intenta anular | `FORBIDDEN_ROLE` | RF-26, RNF-03 |
| UT-BE-17 | `test_void_administrative_publishes_wasted_and_charge_requested` | Admin anula | `WASTED` + `ORDER_WASTED` + `CHARGE_REQUESTED` | RF-27 |
| UT-BE-18 | `test_void_kitchen_error_allowed_for_chef_and_admin` | Chef y admin | Permitido para ambos | RF-28 |
| UT-BE-19 | `test_void_kitchen_error_by_waiter_or_kitchen_is_forbidden` | Mesero / cocina | `FORBIDDEN_ROLE` | RF-28, RNF-03 |
| UT-BE-20 | `test_void_kitchen_error_without_note_raises_note_required` | Nota vacía / solo espacios | `NOTE_REQUIRED` | RF-29 |
| UT-BE-21 | `test_void_kitchen_error_affects_all_items_of_the_order` | Error en un solo platillo | La comanda **completa** pasa a `VOIDED` | RF-28 |
| UT-BE-22 | `test_void_kitchen_error_publishes_wasted_with_reason_and_no_charge` | Anulación por error de cocina | `ORDER_WASTED(reason=KITCHEN_ERROR)`; **sin** `CHARGE_REQUESTED` | RF-30, RF-31 |
| UT-BE-23 | `test_redo_item_requires_note` | BVA: `""`, `"  "`, 1 carácter, máximo | Rechaza vacío/espacios; acepta ≥ 1 carácter | RF-22 |
| UT-BE-24 | `test_request_bill_publishes_frozen_prices_and_session_id` | Solicitar cuenta | Payload con precios congelados, modificadores y `sessionId` | RF-20 |
| UT-BE-25 | `test_add_item_unavailable_in_catalog_view_is_rejected` | Platillo no disponible en la vista local | `DISH_UNAVAILABLE` | RF-17 |
| UT-BE-26 | `test_add_modifiers_and_notes_to_item` | Agregar modificadores y nota | Persistidos y validados | RF-14 |
| UT-BE-27 | `test_create_order_for_table_and_for_takeaway` | Con mesa / `takeaway` sin mesa | Ambas válidas | RF-12, RF-13 |
| UT-BE-28 | `test_actor_user_id_is_recorded_on_create_and_modify` | Crear y modificar | `created_by`/`updated_by` = usuario del JWT | RF-15 |
| UT-BE-29 | `test_transfer_order_changes_table` | Transferir de mesa | Mesa actualizada | RF-16 |
| UT-BE-30 | `test_mark_item_ready_publishes_dish_prepared` | Ítem "Listo" | `READY` + `DISH_PREPARED` | RF-09, RF-10 |
| UT-BE-31 | `test_mark_order_ready_marks_every_item_ready` | Comanda "Listo" | Todos los ítems `READY` | RF-11 |
| UT-BE-32 | `test_kds_queue_orders_by_priority_then_created_at` | Comandas con distinta prioridad | Orden por prioridad, luego cronológico | RF-02, RF-03 |
| UT-BE-33 | `test_kds_queue_equal_priority_breaks_ties_chronologically` | Igual prioridad, timestamps ±1 s | Gana el más antiguo | RF-03 |
| UT-BE-34 | `test_kds_queue_excludes_paid_orders` | Comanda `PAID` | No aparece | RF-04 |
| UT-BE-35 | `test_kds_queue_filters_items_by_station` | `station=grill` | Solo platillos de esa estación | RF-08 |
| UT-BE-36 | `test_kds_queue_groups_orders_by_status` | Estados mezclados | Agrupadas por estado | RF-01 |
| UT-BE-37 | `test_stale_version_raises_version_conflict` | `UPDATE` con versión obsoleta | `VERSION_CONFLICT` (`409`) | RNF-09 |

### 5.3 Backend — consumers (`tests/unit/messaging/`)

| ID | Nombre del test | Escenario | Resultado esperado | Req. |
|---|---|---|---|---|
| UT-BE-38 | `test_stock_reserved_moves_order_to_in_preparation_and_notifies` | `STOCK_RESERVED` en `CREATED` | `IN_PREPARATION` + push WebSocket | RF-33, RF-18 |
| UT-BE-39 | `test_stock_insufficient_moves_order_to_rejected` | `STOCK_INSUFFICIENT` | `REJECTED` + alerta | RF-34 |
| UT-BE-40 | `test_payment_completed_moves_delivered_or_wasted_to_paid` | *parametrizado* | `PAID` | RF-21 |
| UT-BE-41 | `test_payment_completed_on_invalid_status_is_ignored_with_warning` | Sobre `VOIDED`/`CANCELLED` | Sin cambio de estado; `WARNING` | RF-21 |
| UT-BE-42 | `test_duplicate_event_is_ignored` | *parametrizado* por consumer, mismo `eventId` | Segundo mensaje sin efecto | RNF-02 |
| UT-BE-43 | `test_late_stock_reserved_on_expired_order_is_ignored` | Reserva tras `EXPIRED` | Estado intacto | RF-35 |
| UT-BE-44 | `test_dining_session_started_creates_base_order_with_session_id` | Evento de Sala | Orden en `CREATED` con `session_id` | RF-39 |
| UT-BE-45 | `test_dining_session_started_duplicate_creates_single_order` | Reentrega (`sessionId+eventId`) | Una sola orden | RF-39, RNF-02 |
| UT-BE-46 | `test_menu_events_update_materialized_view` | `DISH_PRICE_UPDATED`, `CATALOG_UPDATED` | Precio, disponibilidad, categoría, estación actualizados | RF-40 |
| UT-BE-47 | `test_malformed_message_is_rejected_without_state_change` | Payload inválido | Va a DLQ; ninguna orden cambia | RNF-02, RNF-13 |

### 5.4 Backend — workers (`tests/unit/workers/`)

| ID | Nombre del test | Escenario | Resultado esperado | Req. |
|---|---|---|---|---|
| UT-BE-48 | `test_outbox_worker_publishes_pending_rows_and_marks_them_published` | Filas pendientes | Publicadas en orden y marcadas | RNF-01 |
| UT-BE-49 | `test_outbox_worker_keeps_row_pending_when_publish_fails` | Falla del *publisher confirm* | La fila **no** se marca; se reintenta | RNF-01 |
| UT-BE-50 | `test_saga_watcher_expires_only_orders_older_than_threshold` | BVA: umbral −1 s / +1 s (reloj congelado) | Solo la de +1 s expira *(comportamiento exacto en umbral: OP-01)* | RF-35 |
| UT-BE-51 | `test_saga_watcher_publishes_order_expired_exactly_once` | Watcher corre dos veces | Un solo `ORDER_EXPIRED` | RF-35, RNF-02 |
| UT-BE-52 | `test_reconciliation_does_not_overwrite_newer_updated_at` | Evento más reciente que el snapshot | Se conserva el dato más nuevo | RF-41 |
| UT-BE-53 | `test_reconciliation_logs_discrepancies_and_alerts_above_threshold` | Muchas discrepancias | Se registran y se alerta | RF-41 |
| UT-BE-54 | `test_reconciliation_tolerates_menu_failure_keeping_last_copy` | Menú no responde | Se conserva la copia local; se reintenta luego | RF-41, RNF-05 |

### 5.5 Backend — capa API (`tests/unit/api/`, `TestClient`)

| ID | Nombre del test | Escenario | Resultado esperado | Req. |
|---|---|---|---|---|
| UT-BE-55 | `test_protected_routes_without_token_return_401` | *parametrizado* sobre todas las rutas | `401 UNAUTHENTICATED` | RNF-03 |
| UT-BE-56 | `test_routes_with_wrong_role_return_403` | Matriz rol × ruta (Guidelines §5.7) | `403 FORBIDDEN_ROLE` | RNF-03 |
| UT-BE-57 | `test_invalid_body_returns_422_with_validation_error` | Body inválido | `422` + `VALIDATION_ERROR` | RNF-13 |
| UT-BE-58 | `test_domain_error_maps_to_error_envelope_with_error_code` | Transición inválida | `409` + `{error:{code,message}}` | RNF-03 |
| UT-BE-59 | `test_error_responses_never_expose_internals` | Provocar excepción interna | Sin `Traceback`, rutas de archivo ni versiones | RNF-03 |
| UT-BE-60 | `test_body_cannot_set_user_id_or_status` | Body con `userId`/`status` | Ignorados o rechazados | RF-15, RNF-03 |
| UT-BE-61 | `test_query_filters_reject_values_outside_allow_list` | `status=' OR 1=1`, `station=unknown` | `422` | RNF-03 |

### 5.6 Frontend (`*.test.ts(x)` junto al código)

| ID | Nombre del test | Componente / módulo | Resultado esperado | Req. |
|---|---|---|---|---|
| UT-FE-01 | `maps Enter to MARK_FOCUSED_READY` | Mapeador Bump Bar | Comando correcto | RF-09, RNF-07 |
| UT-FE-02 | `arrow keys move focus and clamp at the first and last card` | Mapeador Bump Bar | BVA primera/última tarjeta | RNF-07 |
| UT-FE-03 | `ignores unmapped keys` | Mapeador Bump Bar | Sin efecto | RNF-07 |
| UT-FE-04 | `is fully operable without pointer events` | `KdsBoard` | Ninguna acción requiere `mouse*`/`click` | RNF-07 |
| UT-FE-05 | `groups orders into status columns` | `KdsBoard` | Columnas por estado | RF-01 |
| UT-FE-06 | `sorts by priority then by created time` | `sortOrders` | Orden correcto y estable | RF-02, RF-03 |
| UT-FE-07 | `formats elapsed time at 59s, 60s, 59m59s and 1h` | `useElapsedTime` (*fake timers*) | BVA de formato | RF-07 |
| UT-FE-08 | `renders table id, dishes, modifiers and notes` | `OrderCard` | Todos visibles | RF-05, RF-06 |
| UT-FE-09 | `escapes HTML in special notes` | `OrderCard` | Texto plano, sin ejecución | RF-05, RNF-03 |
| UT-FE-10 | `shows only dishes of the selected station` | Filtro de estación | Filtrado correcto | RF-08 |
| UT-FE-11 | `removes the order when a PAID event arrives` | `useKdsQueue` | Sale del tablero | RF-04 |
| UT-FE-12 | `status badge shows text and a non-color cue for all 9 statuses` | `StatusBadge` | Color **+** texto/icono | RNF-11, RF-18 |
| UT-FE-13 | `reconnects with capped exponential backoff` | Cliente WebSocket | Reintentos con tope | RNF-04 |
| UT-FE-14 | `discards messages that fail Zod validation without crashing` | Cliente WebSocket | Descarta y registra | RNF-04 |
| UT-FE-15 | `triggers a full refetch after reconnecting` | Cliente WebSocket | *Refetch* al reconectar | RNF-04 |
| UT-FE-16 | `applies a pushed change to the cache without extra GET requests` | `useKdsQueue` | Sin *polling* | RNF-04 |
| UT-FE-17 | `confirm, cancel and modify are enabled only in CREATED` | `OrderTicketWidget` | Habilitados solo en `CREATED` | RF-23, RF-24 |
| UT-FE-18 | `confirm button shows loading and blocks double click` | `OrderTicketWidget` | Un solo `POST` | RF-32 |
| UT-FE-19 | `shows "pending stock confirmation" after 200 and updates on push` | `OrderTicketWidget` | Estado intermedio visible | RF-32, RF-33 |
| UT-FE-20 | `announces a rejected order via aria-live` | `OrderTicketWidget` | Alerta accesible | RF-34 |
| UT-FE-21 | `redo dialog requires a note` | Diálogo "rehacer" | Bloquea envío sin nota | RF-22 |
| UT-FE-22 | `kitchen-error void requires a note and is visible only to chef and admin` | Diálogo de anulación | Nota obligatoria; visibilidad por rol | RF-28, RF-29 |
| UT-FE-23 | `administrative void action is hidden for non-admin roles` | Acciones del ticket | Oculta *(solo usabilidad)* | RF-26 |
| UT-FE-24 | `maps 409 VERSION_CONFLICT to refetch and 403 to a permission message` | Cliente API | Manejo correcto | RNF-09, RNF-03 |
| UT-FE-25 | `never shows raw server messages` | Cliente API | Solo mensajes de i18n por `ErrorCode` | RNF-03 |
| UT-FE-26 | `OrderStatus, ErrorCode and roles mirror the backend contract` | Tipos compartidos | Coinciden con `contracts/` | RNF-13 |
| UT-FE-27 | `built CSS has no rules on body, html or :root outside the module root` | Build CSS (PostCSS) | Cero reglas globales | RNF-08 |
| UT-FE-28 | `intermediate dish form validates quantity and inputs` *(opc.)* | `IntermediateDishesApp` | Cantidad > 0, ≥ 1 insumo | RF-36, RF-38 |
| UT-FE-29 | `shows a friendly message for DISH_UNAVAILABLE` | Ticket | Mensaje localizado | RF-17 |

---

## 6. Pruebas de Integración

Verifican la interacción entre módulos **reales**. Solo se sustituye lo que pertenece a otros equipos (mediante *stubs de contrato*, §12.3).
**Herramientas:** `pytest` + Testcontainers (PostgreSQL, RabbitMQ, Redis reales) · Vitest + **MSW** (frontend).

### 6.1 Backend (`tests/integration/`)

| ID | Nombre del test | Flujo | Resultado esperado | Req. |
|---|---|---|---|---|
| IT-01 | `test_state_and_outbox_roll_back_together` | Forzar fallo tras actualizar el estado | Ni estado ni `outbox` persisten | RNF-01 |
| IT-02 | `test_outbox_worker_publishes_to_real_broker_with_correct_routing_key` | Worker → RabbitMQ | *Routing key* y payload válidos contra el JSON Schema | RNF-01, RNF-13, RF-10, RF-19 |
| IT-03 | `test_redelivered_message_is_applied_once` | Reentrega real | Efecto único | RNF-02 |
| IT-04 | `test_concurrent_updates_one_succeeds_other_gets_409` | Dos escrituras simultáneas | Una gana, la otra `VERSION_CONFLICT` | RNF-09 |
| IT-05 | `test_saga_happy_path` | Confirmar → (stub Inventario) `STOCK_RESERVED` | `IN_PREPARATION`; aparece en la cola KDS; push enviado | RF-32, RF-33, RF-18 |
| IT-06 | `test_saga_insufficient_stock` | `STOCK_INSUFFICIENT` | `REJECTED`; no aparece en el KDS | RF-34 |
| IT-07 | `test_saga_timeout_expires_order_and_ignores_late_reservation` | Sin respuesta > umbral, luego `STOCK_RESERVED` | `EXPIRED`; `ORDER_EXPIRED` publicado; reserva tardía ignorada | RF-35 |
| IT-08 | `test_administrative_void_flow_and_payment` | Admin anula → `PAYMENT_COMPLETED` | `WASTED`; `ORDER_WASTED` + `CHARGE_REQUESTED`; luego `PAID` | RF-26, RF-27, RF-21 |
| IT-09 | `test_kitchen_error_void_flow_never_charges` | Chef anula por error de cocina | `VOIDED`; `ORDER_WASTED(KITCHEN_ERROR)`; **ninguna** fila `CHARGE_REQUESTED` en `outbox` | RF-28…RF-31 |
| IT-10 | `test_payment_completed_removes_order_from_kds_queue` | `PAYMENT_COMPLETED` | `PAID`; ya no está en `/kds/queue` | RF-04, RF-21 |
| IT-11 | `test_cancel_in_created_and_reject_in_preparation` | Cancelar en ambos estados | `CANCELLED` + evento / `409` | RF-24, RF-25 |
| IT-12 | `test_dining_session_started_creates_order_once_with_real_broker` | Evento real (duplicado) | Una orden | RF-39 |
| IT-13 | `test_menu_events_update_redis_view_and_affect_item_validation` | `CATALOG_UPDATED` (no disponible) → agregar ítem | `DISH_UNAVAILABLE` | RF-40, RF-17 |
| IT-14 | `test_catalog_reconciliation_with_stubbed_menu` | Snapshot HTTP simulado; Menú caído | *Upsert* con `updated_at`; con Menú caído conserva la copia | RF-41, RNF-05 |
| IT-15 | `test_poison_message_goes_to_dlq_and_consumer_stays_healthy` | Mensaje inválido N veces | A la DLQ; el consumer sigue vivo | RNF-02 |
| IT-16 | `test_jwt_signature_and_role_validation` | JWT válido/expirado/manipulado (`dev_token`) | `200` / `401` / `401` | RNF-03, RF-15 |
| IT-17 | `test_websocket_auth_and_role_scoped_messages` | Con/sin token; distintos roles | Sin token rechazado; solo canales autorizados *(alcance: OP-02)* | RNF-03, RNF-04, RF-18 |
| IT-18 | `test_bill_request_payload_matches_schema` | Solicitar cuenta | Payload con precios congelados, modificadores y `sessionId` válido contra el esquema | RF-20 |
| IT-19 | `test_alembic_upgrade_and_downgrade_on_empty_database` | `upgrade head` / `downgrade base` | Sin errores | RNF-10 |

### 6.2 Frontend (Vitest + MSW)

| ID | Nombre del test | Flujo | Resultado esperado | Req. |
|---|---|---|---|---|
| IT-FE-01 | `ticket confirm flow: 200 then websocket push` | `POST /confirm` → 200 → mensaje WS | "Pendiente" → "En cocina" | RF-32, RF-33 |
| IT-FE-02 | `kds loads the queue and applies pushes without repeated GETs` | `GET /kds/queue` + mensajes WS | Redibuja sola; **conteo de GET no crece** | RF-01, RNF-04 |
| IT-FE-03 | `websocket drop and reconnect resynchronizes state` | Corte y reconexión | *Refetch* y estado consistente | RNF-04 |
| IT-FE-04 | `401 and 403 are handled` | Respuestas de auth | Aviso de sesión/permiso; sin fuga de detalle | RNF-03 |
| IT-FE-05 | `409 conflict refetches and informs the user` | `VERSION_CONFLICT` | Datos frescos + mensaje | RNF-09 |
| IT-FE-06 | `federation smoke: remote loads inside a host stub without style leakage` | Build → host stub carga `./KdsApp` y `./OrderTicketWidget` | Los elementos del host **no** cambian de estilo computado | RNF-08 |
| IT-FE-07 | `keyboard-only flow marks an item ready` | Solo `keyboard` | Se dispara `PATCH …/ready` | RF-09, RNF-07 |

---

## 7. Pruebas de Contrato y de API (Postman + Newman)

Se ejecutan contra el stack levantado con `docker compose` (API + PostgreSQL + Redis + RabbitMQ + *stubs de contrato*). Los esquemas viven en `contracts/` y se validan con `ajv`.

### 7.1 Contrato REST (una prueba por endpoint)

| ID | Endpoint | Verifica | Req. |
|---|---|---|---|
| CT-01 | `GET /orders?status=` | `200`, esquema de lista, filtro por estado | RF-18 |
| CT-02 | `POST /orders` (mesa y `takeaway`) | `201`, esquema `Order`, estado `CREATED` | RF-12, RF-13 |
| CT-03 | `PATCH /orders/{id}/items` | `200` en `CREATED`; `409` fuera de `CREATED`; `DISH_UNAVAILABLE` | RF-14, RF-17, RF-23 |
| CT-04 | `PATCH /orders/{id}/transfer` | `200`, mesa cambiada | RF-16 |
| CT-05 | `POST /orders/{id}/confirm` | `200`, sin esperar a Inventario | RF-32 |
| CT-06 | `PATCH /orders/{id}/cancel` | `200` en `CREATED`; `409` si no | RF-24, RF-25 |
| CT-07 | `PATCH /orders/{id}/items/{itemId}/redo` | `422`/`NOTE_REQUIRED` sin `note`; `200` con `note` | RF-22 |
| CT-08 | `POST /orders/{id}/request-bill` | `200`; evento con precios congelados | RF-20 |
| CT-09 | `GET /kds/queue?station=` | Orden por prioridad/cronología; filtro por estación; excluye `PAID` | RF-01…RF-04, RF-08 |
| CT-10 | `PATCH /orders/{id}/items/{itemId}/ready` | `200`; `DISH_PREPARED` encolado | RF-09, RF-10 |
| CT-11 | `PATCH /orders/{id}/ready` | `200` | RF-11 |
| CT-12 | `PATCH /orders/{id}/void` | Solo `admin`; `WASTED` | RF-26, RF-27 |
| CT-13 | `PATCH /orders/{id}/void-kitchen-error` | `admin`/`chef`; `note` obligatoria; `VOIDED` | RF-28…RF-31 |
| CT-14 | `POST /intermediate-dishes` *(opc.)* | Solo `chef`; `201` | RF-36, RF-37 |

### 7.2 Pruebas de flujo (encadenadas)

| ID | Flujo | Resultado esperado | Req. |
|---|---|---|---|
| CT-15 | Ciclo de vida completo: crear → ítems → confirmar → *(helper de staging publica `STOCK_RESERVED`)* → listo → cuenta → *(helper publica `PAYMENT_COMPLETED`)* | Termina en `PAID` | RF-12…RF-21, RF-32, RF-33 |
| CT-16 | Cancelar y luego intentar editar | `cancel 200` → `items 409` | RF-23, RF-24 |
| CT-17 | Anulación administrativa vs. por error de cocina | Estados y eventos terminales distintos (`WASTED` cobra; `VOIDED` no) | RF-26…RF-31 |

### 7.3 Escenarios de error y seguridad de contrato

| ID | Escenario | Resultado esperado | Req. |
|---|---|---|---|
| CT-18 | Toda la colección sin token | `401` en cada ruta protegida | RNF-03 |
| CT-19 | Matriz de roles (`waiter`/`kitchen`/`chef`/`admin` × endpoint) | `403` donde no corresponde | RNF-03 |
| CT-20 | Orden inexistente / transición inválida / body inválido | `404` / `409` / `422` | RNF-03, RNF-13 |
| CT-21 | Todo error cumple el envoltorio `{error:{code,message}}` y `code ∈ ErrorCode` | Esquema válido | RNF-13 |
| CT-22 | Tiempo de respuesta por petición | `pm.response.responseTime < 500` | RNF-06 |

### 7.4 Contratos de eventos (asíncronos)

| ID | Verificación | Resultado esperado | Req. |
|---|---|---|---|
| CT-23 | Todo payload **publicado** valida contra `contracts/events/*.json` | Sin discrepancias | RNF-13 |
| CT-24 | Muestras de payloads **consumidos** (`STOCK_RESERVED`, `STOCK_INSUFFICIENT`, `PAYMENT_COMPLETED`, `DINING_SESSION_STARTED`, `DISH_PRICE_UPDATED`, `CATALOG_UPDATED`) parsean en los modelos de la capa anticorrupción | Sin errores; detecta diferencias de nombres (ver §15) | RNF-13, RF-39, RF-40 |
| CT-25 | Compatibilidad hacia atrás: *diff* de esquemas en cada PR | Campo quitado/renombrado ⇒ el CI falla | RNF-13 |
| CT-26 | Instantánea de `/openapi.json`: *diff* de cambios rompientes | Cambio rompiente sin versión ⇒ falla | RNF-13 |
| CT-27 | Paridad de enums frontend ↔ backend (`OrderStatus`, `ErrorCode`, roles) | Idénticos | RNF-13 |

---

## 8. Pruebas de Sistema / E2E (Playwright)

Validan el sistema completo levantado con `docker compose` + *stubs de contrato* de Inventario, Pagos, Sala y Menú. **Se ejecutan en Chromium y WebKit, en modo *headless* en CI.** La cocina se prueba **solo con `page.keyboard`**.

| ID | Caso | Pasos clave | Resultado esperado | Req. |
|---|---|---|---|---|
| ST-01 | El KDS muestra la cola (landscape 1920×1080) | Abrir KDS con comandas sembradas | Tarjetas con mesa, cronómetro, platillos, modificadores y notas | RF-05, RF-06, RF-07, RF-01 |
| ST-02 | Marcar un ítem "Listo" solo con teclado | Flechas para enfocar; `Enter` | El ítem pasa a listo; sin usar el mouse | RF-09, RNF-07 |
| ST-03 | Marcar la comanda completa "Listo" con teclado | Atajo de comanda completa | Comanda lista | RF-11, RNF-07 |
| ST-04 | Orden por prioridad y cronológico | Comandas con prioridades iguales y distintas | Orden visual correcto | RF-02, RF-03 |
| ST-05 | Filtro por estación | Cambiar de estación con teclado | Solo platillos de la estación | RF-08 |
| ST-06 | La comanda desaparece al pagarse | *Stub* de Pagos publica `PAYMENT_COMPLETED` | Desaparece del KDS sin recargar | RF-04, RF-21 |
| ST-07 | Widget del mesero: crear y confirmar | Crear → agregar → confirmar → *stub* reserva stock | "Pendiente" → aparece en el KDS | RF-12…RF-14, RF-32, RF-33 |
| ST-08 | Alerta de rechazo | *Stub* responde `STOCK_INSUFFICIENT` | Alerta visible y anunciada (`aria-live`) | RF-34 |
| ST-09 | Cancelar solo en `CREATED` | Cancelar en `CREATED` y tras pasar a cocina | Permitido / deshabilitado | RF-24 |
| ST-10 | Rehacer un platillo con nota obligatoria | Intentar sin nota, luego con nota | Bloquea / permite | RF-22 |
| ST-11 | Anulación administrativa vs. error de cocina | `admin` anula; `chef` anula por error | Aviso de cobro / aviso de **no** cobro | RF-26…RF-31 |
| ST-12 | *(Opc.)* Lote de platillo intermedio | Chef registra un lote | Confirmación; evento publicado | RF-36, RF-38 |
| ST-13 | Caída y recuperación de red | `context.setOffline(true)` → volver online | Indicador de conexión; estado resincronizado | RNF-04 |
| ST-14 | Multi-navegador | Toda la suite en Chromium **y** WebKit | Mismos resultados | RNF-07 |
| ST-15 | Regresión visual | `toHaveScreenshot()` de tarjeta KDS y ticket | Sin diferencias no aprobadas | RNF-08 |
| ST-16 | Convivencia en el *host* | Montar el remoto en un *host stub* con estilos propios | Los estilos del host no cambian; tokens acotados | RNF-08 |
| ST-17 | Accesibilidad | axe-core en KDS, ticket y vista del chef | 0 violaciones críticas; foco visible; contraste AA | RNF-11 |
| ST-18 | Responsivo por dispositivo | Handheld portrait (360×640), tablet 8" (800×1280), KDS 1920×1080 | Layout correcto; botones ≥ 44/48 px | RNF-07, RNF-11 |
| ST-19 | XSS en notas | Nota `<script>alert(1)</script>` | Se muestra como texto | RF-05, RNF-03 |
| ST-20 | Expiración de sesión | Token vencido durante el uso | Se notifica al Shell; sin pantalla rota | RNF-03 |

> **Reglas de estabilidad (no *flaky*):** `data-testid` o `getByRole`, nunca XPath frágil; *auto-waiting*, nunca `waitForTimeout`; datos sembrados y limpiados por prueba; reintento máx. 1 en CI.

---

## 9. Pruebas de Aceptación (BDD y UAT)

### 9.1 BDD — Gherkin (Cucumber.js en frontend, `pytest-bdd` en backend)

Los `.feature` describen el **comportamiento de negocio** (sin URLs ni selectores) y sirven como **documentación viva**. Se escriben en inglés (regla del código), con los estados del diccionario oficial.

| ID | Feature (`tests/bdd/features/`) | Escenarios principales | Req. |
|---|---|---|---|
| AT-01 | `order_lifecycle.feature` | Camino feliz `CREATED → IN_PREPARATION → DELIVERED → PAID`; la sesión de Sala crea la orden base | RF-09…RF-15, RF-19, RF-32, RF-39 |
| AT-02 | `cancellation.feature` | Cancelar en `CREATED` ✔; cancelar/editar en `IN_PREPARATION` ✘ | RF-23, RF-24, RF-25 |
| AT-03 | `inventory_saga.feature` | Stock reservado; stock insuficiente → `REJECTED`; sin respuesta → `EXPIRED`; reserva tardía ignorada | RF-32…RF-35 |
| AT-04 | `admin_void.feature` | Admin anula → `WASTED` **y se cobra** como entrega normal; mesero no puede | RF-26, RF-27 |
| AT-05 | `kitchen_error_void.feature` | Chef/admin anulan por error de cocina → `VOIDED`, **comanda completa**, **no se cobra**, nota obligatoria | RF-28…RF-31 |
| AT-06 | `kds_queue.feature` | Prioridad, desempate cronológico, filtro por estación, campos de la tarjeta, se retira al pagarse | RF-01…RF-08, RF-11 |
| AT-07 | `redo_dish.feature` | Rehacer un platillo entregado exige nota | RF-22 |
| AT-08 | `table_transfer.feature` | Transferir comanda de mesa | RF-16 |
| AT-09 | `unavailable_dish.feature` | Platillo no disponible no se puede pedir; la vista se actualiza por eventos de Menú | RF-17, RF-40, RF-41 |
| AT-10 | `payment_close.feature` | Cuenta con precios congelados; `PAID` solo tras el pago | RF-20, RF-21 |
| AT-11 | `intermediate_dishes.feature` *(opc.)* | El chef registra un lote | RF-36…RF-38 |
| AT-12 | `realtime_updates.feature` | La pantalla se actualiza sola; recuperación tras reconexión | RF-18, RNF-04 |

**Ejemplo (documentación viva)**

```gherkin
@RF-28 @RF-30 @RF-31 @critical
Feature: Void an order because of a kitchen error
  As a chef or an administrator
  I want to void an order that the kitchen ruined
  So that the customer is not charged for the restaurant's mistake

  Background:
    Given an order in status IN_PREPARATION with 3 dishes

  Scenario: The whole order is voided and the customer is not charged
    When the chef voids the order with the note "Burnt dish"
    Then the order status is VOIDED
    And all 3 dishes are voided, not only the faulty one
    And Inventory is informed that the ingredients are lost
    And no charge is requested from Billing

  Scenario: A note is mandatory
    When the chef voids the order without a note
    Then the request is rejected with error "NOTE_REQUIRED"
    And the order status is still IN_PREPARATION

  Scenario: A waiter cannot void for a kitchen error
    When a waiter tries to void the order for a kitchen error
    Then the request is rejected with error "FORBIDDEN_ROLE"
```

```gherkin
@RF-26 @RF-27
Scenario: An administrative void is charged like a normal delivery
  Given an order in status IN_PREPARATION
  When an administrator voids the order for an administrative reason
  Then the order status is WASTED
  And a charge is requested from Billing with the full order detail
```

### 9.2 UAT — validación con el personal real (manual con hardware)

| ID | Criterio de aceptación | Cómo se verifica | Automatizable |
|---|---|---|---|
| UAT-01 | Un cocinero opera el KDS **solo con el Bump Bar físico** (atajos numéricos, flechas, `Enter`) sin tocar mouse ni pantalla | Sesión con el hardware real | ❌ |
| UAT-02 | Un mesero opera el ticket con **una sola mano** en el handheld/tablet 8" en vertical | Sesión con dispositivo real | ❌ |
| UAT-03 | El KDS es legible **a distancia** (alto contraste, tipografía expansiva) en el monitor de pared | Prueba en sitio | ❌ |
| UAT-04 | Simulación de **hora pico** con personal: varias comandas simultáneas fluyen sin confusión | Ensayo cronometrado | Parcial (k6 + Playwright) |
| UAT-05 | El personal entiende los estados **sin depender solo del color** | Cuestionario breve + observación | ❌ |
| UAT-06 | Una comanda anulada por error de cocina **no genera cobro** en Pagos (extremo a extremo con los otros equipos) | Prueba de integración entre equipos | Parcial |

---

## 10. Pruebas No Funcionales y de Rendimiento (k6)

### 10.1 Escenarios de carga

**Mezcla de tráfico (propuesta):** 55 % `GET /kds/queue` · 20 % `GET /orders` · 10 % flujo *crear → ítems → confirmar* · 10 % `PATCH …/ready` · 5 % `request-bill`. La intención es reflejar que las pantallas leen mucho más de lo que escriben.

| ID | Prueba | Configuración | Umbral / criterio | Req. |
|---|---|---|---|---|
| PT-01 | **Carga** | *Ramp-up* 0 → 50 VUs en 1 min · mantener 3 min · bajar 1 min | `http_req_duration p(95) < 500 ms` · `http_req_failed < 1 %` | RNF-06 |
| PT-02 | **Estrés** | 50 → 100 → 200 → 300 VUs por etapas | Documentar **en qué punto `p95` supera 2 s** y **qué falla primero** (BD, pool de conexiones, CPU, memoria, cola de RabbitMQ) | RNF-06 |
| PT-03 | **Pico** (hora de servicio) | 0 → 80 VUs en 10 s | Sin errores `5xx`; recuperación en < 30 s | RNF-06 |
| PT-04 | **Resistencia** | 20 VUs durante 30 min | Memoria y conexiones estables; *lag* del outbox estable | RNF-06, RNF-01 |
| PT-05 | **Fan-out de WebSocket** | 20 sockets KDS + 30 de mesero, cambios continuos | Latencia de push `p95 < 1 s` *(propuesto)*; 0 mensajes perdidos | RNF-04, RNF-06 |
| PT-06 | **Lag del outbox** | Bajo PT-01 | Tiempo *commit → publicación* `p95 < 1 s` *(propuesto)* | RNF-01, RNF-06 |
| PT-07 | **Carrera por stock** | ≥ 3 confirmaciones simultáneas del mismo platillo con *stub* de Inventario atómico | Exactamente 1 `IN_PREPARATION`, el resto `REJECTED` | RF-33, RF-34 |
| PT-08 | **Regresión de rendimiento en CI** | *Smoke*: 10 VUs, 30 s tras cada despliegue a staging | Mismos umbrales que PT-01; **bloquea el pipeline** | RNF-06 |

```javascript
// tests/performance/load.js (essential parts)
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '1m', target: 50 },  // ramp-up
    { duration: '3m', target: 50 },  // steady state
    { duration: '1m', target: 0 },   // ramp-down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
  },
};

export default function () {
  const res = http.get(`${__ENV.BASE_URL}/kds/queue?station=grill`, {
    headers: { Authorization: `Bearer ${__ENV.TOKEN_KITCHEN}` },
  });
  check(res, { 'status is 200': (r) => r.status === 200 });
  sleep(1);
}
```

**Métricas de oro** que se reportan siempre: **latencia** (`p50/p95/p99`), ***throughput*** (req/s) y ***error rate***, más métricas propias: lag del outbox, profundidad de cola de RabbitMQ y latencia de push.

### 10.2 Otras pruebas no funcionales

| ID | Caso | Umbral | Herramienta | Req. |
|---|---|---|---|---|
| NF-01 | El `correlation_id` viaja del request → outbox → evento → log del consumer | Presente en toda la cadena | Integración + logs | RNF-12 |
| NF-02 | Logs estructurados sin secretos ni JWT completos | 0 hallazgos | Revisión automatizada de logs (gitleaks / regex) | RNF-12, RNF-03 |
| NF-03 | Respuesta de teclado del KDS con 30 tarjetas activas | Respuesta visual < 100 ms *(propuesto)* | Playwright + trazas | RNF-06, RNF-07 |
| NF-04 | INP del KDS con 50 tarjetas | ≤ 200 ms | Lighthouse/Web Vitals | RNF-06 |
| NF-05 | Tamaño del remoto federado | ≤ 250 KB comprimido *(propuesto)* | Salida del build | RNF-06, RNF-08 |

---

## 11. Pruebas de Seguridad (OWASP ZAP y otras)

| ID | Caso | Método | Resultado esperado | Req. |
|---|---|---|---|---|
| SEC-01 | **Escaneo DAST de la API** | OWASP ZAP *API scan* sobre `/openapi.json` (OWASP Top 10) | Sin alertas de riesgo **Alto**; reporte HTML como *artifact* | RNF-03 |
| SEC-02 | **Escaneo DAST del frontend** | ZAP *baseline* sobre `pnpm preview` | Sin riesgo Alto; cabeceras de seguridad revisadas | RNF-03 |
| SEC-03 | JWT: ausente, expirado, firma alterada, algoritmo `none` | Petición manual/automatizada | `401` en todos | RNF-03 |
| SEC-04 | **Matriz de roles** en cada endpoint | Colección Newman (CT-19) | `waiter` no anula; `kitchen` no anula; `chef` solo `void-kitchen-error`; `admin` ambas | RNF-03 |
| SEC-05 | Inyección SQL en `status`/`station` y campos de texto | Payloads `' OR 1=1 --`, `; DROP TABLE` | Rechazados (`422`) o tratados como texto; consultas parametrizadas | RNF-03 |
| SEC-06 | Errores sin información interna | Provocar excepciones | Sin trazas, rutas, nombres de tablas ni versiones | RNF-03 |
| SEC-07 | *Mass assignment* | Body con `userId`, `status`, `version` | No se aceptan | RF-15, RNF-03 |
| SEC-08 | XSS almacenado en notas/modificadores | Nota con `<script>` / `<img onerror>` | Escapado en KDS y ticket | RF-05, RNF-03 |
| SEC-09 | Autenticación de WebSocket | Conexión sin token / con token vencido / rol no autorizado | Rechazada; sin datos de otros canales | RNF-03, RNF-04 |
| SEC-10 | Secretos | `gitleaks` sobre el historial; búsqueda en `dist/` del frontend | 0 secretos | RNF-03 |
| SEC-11 | Dependencias | `pip-audit` (backend) y `pnpm audit` (frontend) | 0 vulnerabilidades críticas | RNF-03, RNF-10 |
| SEC-12 | Mensajes envenenados | Eventos malformados, tipos erróneos, campos enormes | A la DLQ; ningún cambio de estado | RNF-02, RNF-03 |
| SEC-13 | Aislamiento del remoto federado | Revisar que no lee ni escribe `localStorage`/cookies del Shell | Sin acceso a tokens del Shell | RNF-03, RNF-08 |
| SEC-14 | Imagen Docker | Escaneo con Trivy *(propuesto)* | 0 críticas | RNF-03 |

**Configuración de ZAP (`.zap/rules.tsv`):** las alertas de riesgo **Alto** se marcan `FAIL` (bloquean el despliegue); las demás, `WARN`. Los falsos positivos se documentan con justificación, nunca se silencian sin ella.

---

## 12. Entornos, Datos y Herramientas

### 12.1 Entornos

| Entorno | Propósito | Infraestructura | Datos |
|---|---|---|---|
| **Local (dev)** | Desarrollo y pruebas unitarias/integración | `docker compose up -d postgres redis rabbitmq` | Semilla local (`scripts/seed`) |
| **CI (efímero)** | Todas las pruebas automáticas | *Service containers* / `docker-compose.ci.yml` | Semilla mínima por prueba |
| **Staging** | E2E, rendimiento *smoke*, ZAP, UAT | Despliegue equivalente a producción | Datos sintéticos representativos |
| **Producción** | Solo *smoke tests* de **solo lectura** tras un despliegue | — | Reales; **nunca** se ejecutan pruebas destructivas ni de carga |

> **Regla crítica:** integración, sistema, rendimiento y seguridad **jamás** corren contra producción.

### 12.2 Herramientas por nivel

| Nivel | Backend | Frontend |
|---|---|---|
| Unitarias | `pytest`, `pytest-asyncio`, `pytest-cov` | Vitest, React Testing Library |
| Integración | Testcontainers (PostgreSQL/RabbitMQ/Redis) | MSW (REST + WebSocket) |
| Contrato / API | Postman + Newman, `ajv`, JSON Schema (Pact opcional) | Verificación del contrato REST (Newman/Pact) |
| BDD | `pytest-bdd` (Gherkin) | Cucumber.js + Playwright |
| E2E | — | Playwright (Chromium + WebKit), POM |
| Accesibilidad | — | axe-core (en Playwright) |
| Rendimiento | k6 | k6 (WebSocket fan-out) |
| Seguridad | OWASP ZAP (API scan), `pip-audit`, gitleaks | OWASP ZAP (baseline), `pnpm audit`, gitleaks |
| Estática | `ruff`, `mypy`, SonarCloud | `eslint`, `tsc`, SonarCloud |
| CI/CD | GitHub Actions | GitHub Actions |

### 12.3 *Stubs de contrato* de otros microservicios

Como Inventario, Pagos, Sala y Menú son de otros equipos, sus respuestas se simulan con un *stub* que **publica/consume eventos según los esquemas de `contracts/events/`** (nunca según suposiciones sueltas):

| Stub | Simula | Comportamientos configurables |
|---|---|---|
| Inventory stub | `inventario.stock.reservado` / `insuficiente` | Reservar, rechazar, **no responder** (para `EXPIRED`), responder tarde, reserva atómica |
| Billing stub | `pagos.pago.completado` | Pagar por `orderId`; consumir `cuenta.solicitada` y `cobro.solicitado` |
| Floor stub | `sala.dining_session.iniciada` | Crear sesiones (incluida reentrega) |
| Menu stub | `menu.*` y `GET /menu/catalogo/completo` | Cambios de precio/disponibilidad; snapshot; **caída** del servicio |

---

## 13. Criterios de Entrada y Salida

### 13.1 Criterios de entrada

| Nivel | Criterios de entrada |
|---|---|
| Unitarias | El código compila; `ruff`/`mypy` (o `eslint`/`tsc`) sin errores |
| Integración | Docker disponible; migraciones aplicadas; *stubs* de contrato listos |
| Contrato / API | Stack levantado y saludable; esquemas en `contracts/` actualizados |
| Sistema / E2E | Backend + frontend desplegados en el entorno; datos sembrados |
| Rendimiento | Entorno estable (staging); línea base medida |
| Aceptación / UAT | Staging idéntico a producción; personal del restaurante disponible |

### 13.2 Criterios de salida

> **Umbral mínimo global:** ≥ **85 %** de cobertura de sentencias, verificado por SonarCloud y aplicado como *Quality Gate* en CI.

| Nivel | Criterios de salida |
|---|---|
| **Estática** | Quality Gate **Passed**; 0 vulnerabilidades críticas; duplicación < 3 %; calificaciones A |
| **Unitarias** | 0 tests fallidos; cobertura por módulo según §13.3 |
| **Integración** | 100 % de IT-01…IT-19 e IT-FE-01…IT-FE-07 pasan |
| **Contrato / API** | 100 % de CT-01…CT-27 pasan; ningún cambio rompiente sin versión |
| **Sistema / E2E** | 100 % de ST-01…ST-20 (sin opcionales) en Chromium **y** WebKit; 0 violaciones críticas de accesibilidad |
| **Aceptación** | 100 % de AT-01…AT-12 (sin opcionales) pasan; UAT-01…UAT-06 aprobados; 0 defectos Alto/Crítico abiertos |
| **Rendimiento** | PT-01 y PT-08 cumplen `p95 < 500 ms` y errores `< 1 %`; punto de ruptura (PT-02) documentado |
| **Seguridad** | 100 % de SEC-01…SEC-14 pasan; 0 alertas ZAP de riesgo Alto; 0 vulnerabilidades críticas en dependencias |

### 13.3 Cobertura diferenciada por riesgo

| Módulo | Riesgo | Objetivo | Tipo | Justificación |
|---|---|---|---|---|
| `domain/` (máquina de estados) | Crítico | ≥ 95 % | Ramas | Un estado inválido corrompe el negocio y el cobro |
| `services/` (transiciones, outbox, roles) | Crítico | ≥ 90 % | Ramas | Reglas de cobro/anulación y autorización |
| `messaging/consumers/`, `workers/` | Crítico | ≥ 90 % | Ramas | Idempotencia, *timeout*, publicación fiable |
| `api/routers/` | Alto | ≥ 85 % | Ramas | Puerta de entrada pública |
| `repositories/` | Alto | Integración (Testcontainers) | — | SQL real, no mocks |
| FE: mapeador Bump Bar, cliente WS, ordenamiento | Alto | ≥ 90 % | Ramas | Cocina depende solo del teclado y del push |
| FE: hooks | Medio | ≥ 75 % | Sentencias | Lógica con dependencias externas |
| FE: componentes de presentación | Bajo | ≥ 50 % | Sentencias | UI declarativa |

### 13.4 Clasificación de defectos

| Severidad | Definición | ¿Bloquea el release? |
|---|---|---|
| **Crítico** | Cobro indebido (p. ej. `VOIDED` con cobro), pérdida de eventos/estados, fallo de seguridad, KDS inoperable | ✅ Sí |
| **Alto** | RF obligatorio roto, transición inválida permitida, evento duplicado con efecto | ✅ Sí |
| **Medio** | Funcionalidad parcial, problema visual/accesibilidad significativo, RF opcional roto | ⚠️ Depende |
| **Bajo** | Cosmético o mejora de UX | ❌ No |

---

## 14. Matriz de Riesgos

`Prioridad = Probabilidad (1-5) × Impacto (1-5)`. **Umbral de acción:** prioridad ≥ 10 exige cobertura de ramas ≥ 85 %, al menos una prueba de integración y una de seguridad dedicadas.

| ID | Riesgo | P | I | Prio. | Mitigación / pruebas | Req. |
|---|---|:-:|:-:|:-:|---|---|
| R-01 | Evento perdido o publicado sin que el estado se haya guardado (o al revés) | 3 | 5 | **15** | Outbox transaccional; UT-BE-13, 48, 49; IT-01, IT-02; PT-06 | RNF-01 |
| R-02 | Procesar dos veces un evento reentregado (doble reserva/cobro) | 4 | 4 | **16** | Idempotencia; UT-BE-42, 45; IT-03, IT-15 | RNF-02 |
| R-03 | Transición de estado inválida permitida | 3 | 5 | **15** | Máquina de estados; UT-BE-09, 10; IT-05…IT-11 | RF-18 |
| R-04 | Anulación por un rol no autorizado | 3 | 5 | **15** | UT-BE-16, 19; CT-19; SEC-04 | RF-26, RF-28, RNF-03 |
| R-05 | Cobro indebido al cliente por error de cocina (`VOIDED` que publica `CHARGE_REQUESTED`) | 2 | 5 | 10 | UT-BE-22; IT-09; AT-05; UAT-06 | RF-31 |
| R-06 | Sobreventa por carrera de stock (atomicidad de Inventario, ERS §10.6) | 3 | 5 | **15** | PT-07; IT-05, IT-06; validación con Inventario | RF-33, RF-34 |
| R-07 | Reserva "huérfana" tras `EXPIRED` | 3 | 4 | **12** | UT-BE-43; IT-07 | RF-35 |
| R-08 | El KDS pierde actualizaciones tras una caída de red | 4 | 4 | **16** | UT-FE-13…16; IT-FE-03; ST-13; PT-05 | RNF-04 |
| R-09 | Vista de catálogo desalineada (*drift*) | 3 | 3 | 9 | UT-BE-52…54; IT-13, IT-14 | RF-40, RF-41, RNF-05 |
| R-10 | Cocina no puede operar solo con teclado | 3 | 5 | **15** | UT-FE-01…04; IT-FE-07; ST-02, ST-03; UAT-01 | RNF-07 |
| R-11 | Falsificación de JWT / rol | 2 | 5 | 10 | IT-16; SEC-03 | RNF-03 |
| R-12 | Estilos globales del remoto rompen otros micro frontends | 3 | 4 | **12** | UT-FE-27; IT-FE-06; ST-16 | RNF-08 |
| R-13 | Pérdida de actualización por concurrencia (dos meseros editan) | 3 | 3 | 9 | UT-BE-37; IT-04; IT-FE-05 | RNF-09 |
| R-14 | XSS por notas de texto libre | 3 | 4 | **12** | UT-FE-09; ST-19; SEC-08 | RNF-03 |
| R-15 | Contratos de eventos desalineados con otros equipos | 4 | 4 | **16** | CT-23…CT-27; §15 | RNF-13 |
| R-16 | Degradación de rendimiento en hora pico | 3 | 4 | **12** | PT-01…PT-05, PT-08 | RNF-06 |

---

## 15. Riesgos de Integración entre Equipos (hallazgos de contrato)

Al contrastar la documentación de los distintos microservicios se detectaron **inconsistencias** que las pruebas de contrato (CT-23…CT-27) deben confirmar o cerrar. **No se resuelven en silencio:** cada una requiere acuerdo con el equipo dueño.

| # | Hallazgo | Documentos | Impacto | Acción |
|---|---|---|---|---|
| H-01 | El documento de **Menú** (`menu-diagramas-secuencia-v3`, §1, §2, §8) muestra a Órdenes consultando a Menú de forma **síncrona** (`GET /menu`, `GET /menu/platillos/{id}/snapshot`), mientras que la arquitectura de Órdenes es **100 % por eventos** con una única excepción (bootstrap/reconciliación) | Menú vs. Arquitectura §0/§3.4 | RF-17, RF-40, RF-41 | Acordar con Menú si el snapshot síncrono desaparece o se documenta como segunda excepción |
| H-02 | Menú publica además `menu.platillo.disponibilidad_actualizada` y `menu.platillo.estado_actualizado`, que **no** figuran entre los eventos que Órdenes consume (Arquitectura §4.3) | Menú vs. Arquitectura §4.3 | RF-17: la disponibilidad por Inventario o la desactivación comercial podrían no llegar | Confirmar si `menu.catalogo.actualizado` los cubre o añadir *consumers* |
| H-03 | **Pagos** nombra sus eventos `DinningSessionCreated` / `PagoCompletado` (sin prefijo de dominio), frente a `sala.dining_session.iniciada` / `pagos.pago.completado` | Pagos vs. diagramas | RF-21, RF-39 | Confirmar los nombres definitivos con Pagos; CT-24 usa los confirmados |
| H-04 | Pagos correlaciona el cobro por `dinning_session_id`; Órdenes espera `pagos.pago.completado {orderId, montoTotal}` | Pagos vs. diagramas §6 | RF-21 | Acordar la clave de correlación (`orderId` vs. `sessionId`) en el payload |
| H-05 | Sala publica `comensales`; el diagrama de Órdenes usa `numComensales` | Sala vs. diagramas §1 | RF-39 | Acordar el nombre del campo (CT-24 lo detecta) |
| H-06 | Inventario aún no documenta su suscripción a `ordenes.comanda.mermada`, `ordenes.orden.expirada` ni `platillo_intermedio.preparado`, ni la **atomicidad** de su reserva | Inventario vs. Arquitectura §8 | RF-30, RF-35, RF-37; PT-07 | Validar con Inventario antes de cerrar IT-07…IT-09 |
| H-07 | Auth aún no fija los *claims* definitivos del JWT ni si los valores de rol coinciden con `waiter/kitchen/chef/admin` | Auth §"Notas" | RNF-03; CT-19; SEC-03/04 | Confirmar nombre del claim y valores de rol con Auth |

---

## 16. Métricas y Dashboard de Calidad

Es el entregable de la **Unidad 14** y del criterio "Informe de métricas y resultados" (20 % de la rúbrica): un dashboard (Excel, Grafana o Jira) que compara el estado del software **ANTES y DESPUÉS** de aplicar las 6 herramientas y calcula el **ROI de la automatización**.

### 16.1 Métricas

| Métrica | Fórmula / fuente | Meta |
|---|---|---|
| **Cobertura** (sentencias y ramas) | SonarCloud / `coverage.xml` / `lcov` | ≥ 85 % global; por módulo según §13.3 |
| **DRE** (*Defect Removal Efficiency*) | `defectos hallados antes de release ÷ (antes + después) × 100` | ≥ 90 % |
| **Densidad de defectos** | `defectos ÷ KLOC` (o por RF) | Tendencia decreciente |
| **Deuda técnica** | Ratio y horas estimadas de SonarCloud | Ratio ≤ 5 % (A) |
| **Vulnerabilidades** | ZAP + `pip-audit`/`pnpm audit` + Sonar | 0 críticas/altas abiertas |
| **Rendimiento** | k6: `p95`, *throughput*, *error rate*; punto de ruptura | `p95 < 500 ms`; errores `< 1 %` |
| **Estabilidad de pruebas** | % de pruebas *flaky* | < 2 % |
| **Tiempo de pipeline** | Duración media en PR | ≤ 15 min *(propuesto)* |
| **Trazabilidad** | % de RF con ≥ 1 prueba por nivel aplicable | 100 % |

### 16.2 Tablero ANTES / DESPUÉS *(completar durante el semestre)*

| Métrica | Línea base (antes de aplicar las 6 herramientas) | Actual (después) | Δ |
|---|---|---|---|
| Cobertura global | *(medir)* | *(medir)* | |
| Vulnerabilidades críticas/altas | *(medir)* | *(medir)* | |
| Code smells / deuda técnica (h) | *(medir)* | *(medir)* | |
| Duplicación | *(medir)* | *(medir)* | |
| `p95` bajo 50 VUs | *(medir)* | *(medir)* | |
| Punto de ruptura (VUs con `p95 > 2 s`) | *(medir)* | *(medir)* | |
| Defectos hallados en producción/staging tardío | *(medir)* | *(medir)* | |
| Tiempo de regresión manual por release | *(medir)* | *(medir)* | |

### 16.3 ROI de la automatización

```
Ahorro  = (horas de regresión manual evitadas por release × nº de releases × costo/hora)
Costo   = (horas de construir y mantener la automatización × costo/hora) + costo de infraestructura CI
ROI (%) = (Ahorro − Costo) ÷ Costo × 100
```

### 16.4 Soporte y consultoría entre equipos

Se lleva un registro (`docs/quality/support-log.md`) de las consultas atendidas a los otros 5 equipos (fecha, equipo, herramienta, problema, resolución, tiempo). Es la evidencia del criterio "Soporte y consultoría" (20 %).

---

## 17. Cronograma

Alineado con la hoja de ruta de la asignatura. **Ajustar al calendario definitivo del docente.**

| Semanas | Fase | Actividad en Orders & KDS | Aspecto / herramienta |
|---|---|---|---|
| 1–2 | Preparación y base común | Fijar código base y `docs/`; *pipeline* mínimo; asignar responsables | CI/CD (§4.1) |
| 3 | Unitarias I (TDD) | Suite unitaria v1: dominio y máquina de estados (UT-BE-01…12) | `pytest` / Vitest |
| 4 | Unitarias II (cobertura y *mocks*) | Servicios, consumers, workers; informe de cobertura | Cobertura |
| 5 | Análisis estático | Quality Gate personalizado; ≥ 3 *code smells* corregidos | SonarCloud (§4.2) |
| 6 | Integración I | Testcontainers; integración continua completa | GitHub Actions |
| 7 | Integración II (contratos) | Esquemas de eventos, colección Postman, informe de contratos (§7, §15) | Postman + Newman (§4.4) |
| 8 | Sistema y aceptación (BDD) | Features Gherkin y *step definitions*; **Parcial 1** | Cucumber (§4.6) |
| 9 | No funcionales | Carga y estrés; DAST | k6 (§4.5), ZAP |
| 10 | Automatización de UI | Suite E2E con POM en Chromium + WebKit | Playwright (§4.3) |
| 11 | API y móvil | Colección Newman completa en CI | Postman + Newman |
| 12 | Ágil y DevOps | *Quadrants* de testing; *retro testing* | CI/CD |
| 13 | Gestión de la calidad | Gestión de defectos; severidades; tablero | Jira / GitHub Projects |
| 14 | Métricas | Dashboard v1 (DRE, cobertura, ROI) | §16 |
| 15 | Tendencias y dashboard | Presentación del **Dashboard de Calidad** | §16 |
| 16 | Presentación final | *Pitch* + demo con **todos los *checks* en verde**; Examen final | Todo |

> **Criterio de cierre sugerido por la asignatura:** el *pipeline* de GitHub Actions con las 6 herramientas en **verde** demuestra la V&V profesional del software.

---

## 18. Matriz de Trazabilidad RF/RNF → Pruebas

Cada requisito debe tener al menos una prueba en cada nivel que le aplique. Un guion (—) indica que el nivel no aplica.

### 18.1 Requisitos funcionales

| Req. | Unitarias | Integración | Contrato/API | Sistema (E2E) | Aceptación (BDD/UAT) |
|---|---|---|---|---|---|
| RF-01 | UT-BE-36, UT-FE-05 | IT-FE-02 | CT-09 | ST-01 | AT-06 |
| RF-02 | UT-BE-32, UT-FE-06 | — | CT-09 | ST-04 | AT-06 |
| RF-03 | UT-BE-32, 33, UT-FE-06 | — | CT-09 | ST-04 | AT-06 |
| RF-04 | UT-BE-34, UT-FE-11 | IT-10 | CT-09 | ST-06 | AT-06, AT-10 |
| RF-05 | UT-FE-08, 09 | — | — | ST-01, ST-19 | AT-06 |
| RF-06 | UT-FE-08 | — | — | ST-01 | AT-06 |
| RF-07 | UT-FE-07 | — | — | ST-01 | AT-06 |
| RF-08 | UT-BE-35, UT-FE-10 | — | CT-09 | ST-05 | AT-06 |
| RF-09 | UT-BE-30, UT-FE-01 | IT-FE-07 | CT-10 | ST-02 | AT-01, UAT-01 |
| RF-10 | UT-BE-30 | IT-02 | CT-10, CT-23 | ST-02 | AT-01 |
| RF-11 | UT-BE-31 | — | CT-11 | ST-03 | AT-06 |
| RF-12 | UT-BE-27 | — | CT-02, CT-15 | ST-07 | AT-01 |
| RF-13 | UT-BE-27 | — | CT-02 | ST-07 | AT-01 |
| RF-14 | UT-BE-26 | — | CT-03 | ST-07 | AT-01 |
| RF-15 | UT-BE-28, 60 | IT-16 | CT-15 | — | AT-01 |
| RF-16 | UT-BE-29 | — | CT-04 | — | AT-08 |
| RF-17 | UT-BE-25, UT-FE-29 | IT-13 | CT-03 | — | AT-09 |
| RF-18 | UT-BE-05, 09, 10, 38, UT-FE-12 | IT-05, IT-17 | CT-01 | ST-07 | AT-12 |
| RF-19 | UT-BE-13, 15 | IT-02 | CT-23 | — | AT-01 |
| RF-20 | UT-BE-24 | IT-18 | CT-08, CT-15 | — | AT-10 |
| RF-21 | UT-BE-08, 40, 41 | IT-08, IT-10 | CT-15 | ST-06 | AT-10 |
| RF-22 | UT-BE-23, UT-FE-21 | — | CT-07 | ST-10 | AT-07 |
| RF-23 | UT-BE-12, UT-FE-17 | — | CT-03, CT-16 | ST-09 | AT-02 |
| RF-24 | UT-BE-03, 11, UT-FE-17 | IT-11 | CT-06, CT-16 | ST-09 | AT-02 |
| RF-25 | UT-BE-15 | IT-11 | CT-06, CT-23 | — | AT-02 |
| RF-26 | UT-BE-06, 16, UT-FE-23 | IT-08 | CT-12, CT-19 | ST-11 | AT-04 |
| RF-27 | UT-BE-17 | IT-08 | CT-12, CT-17 | ST-11 | AT-04 |
| RF-28 | UT-BE-07, 18, 19, 21, UT-FE-22 | IT-09 | CT-13, CT-17 | ST-11 | AT-05 |
| RF-29 | UT-BE-20, UT-FE-22 | — | CT-13 | ST-11 | AT-05 |
| RF-30 | UT-BE-22 | IT-09 | CT-13, CT-23 | — | AT-05 |
| RF-31 | UT-BE-22 | IT-09 | CT-13, CT-17 | ST-11 | AT-05, UAT-06 |
| RF-32 | UT-BE-13, 14, UT-FE-18, 19 | IT-05, IT-FE-01 | CT-05, CT-15 | ST-07 | AT-01, AT-03 |
| RF-33 | UT-BE-01, 38, UT-FE-19 | IT-05, IT-FE-01 | CT-15 | ST-07 | AT-03 |
| RF-34 | UT-BE-02, 39, UT-FE-20 | IT-06 | — | ST-08 | AT-03 |
| RF-35 | UT-BE-04, 43, 50, 51 | IT-07 | — | — | AT-03 |
| RF-36 *(opc.)* | UT-FE-28 | — | CT-14 | ST-12 | AT-11 |
| RF-37 *(opc.)* | — | IT-02 | CT-14, CT-23 | ST-12 | AT-11 |
| RF-38 *(opc.)* | UT-FE-28 | — | — | ST-12 | AT-11 |
| RF-39 | UT-BE-44, 45 | IT-12 | CT-24 | — | AT-01 |
| RF-40 | UT-BE-46 | IT-13 | CT-24 | — | AT-09 |
| RF-41 | UT-BE-52, 53, 54 | IT-14 | — | — | AT-09 |

### 18.2 Requisitos no funcionales

| Req. | Unitarias | Integración | Contrato/API | Sistema (E2E) | Otras (NF / PT / SEC / SA / UAT) |
|---|---|---|---|---|---|
| RNF-01 | UT-BE-13, 48, 49 | IT-01, IT-02 | CT-23 | — | PT-04, PT-06 |
| RNF-02 | UT-BE-42, 45, 47, 51 | IT-03, IT-15 | — | — | SEC-12 |
| RNF-03 | UT-BE-16, 19, 55…61, UT-FE-09, 24, 25 | IT-16, IT-17, IT-FE-04 | CT-18, 19, 20 | ST-19, ST-20 | SEC-01…SEC-14 |
| RNF-04 | UT-FE-13…16 | IT-17, IT-FE-02, IT-FE-03 | — | ST-13 | PT-05, AT-12 |
| RNF-05 | UT-BE-54 | IT-14 | — | — | — |
| RNF-06 | — | — | CT-22 | ST-14 | PT-01…PT-08, NF-03…NF-05 |
| RNF-07 | UT-FE-01…04 | IT-FE-07 | — | ST-02, 03, 14, 18 | UAT-01…03, NF-03 |
| RNF-08 | UT-FE-27 | IT-FE-06 | — | ST-15, ST-16 | NF-05, SEC-13 |
| RNF-09 | UT-BE-37 | IT-04, IT-FE-05 | — | — | UT-FE-24 |
| RNF-10 | — | IT-19 | — | — | SA-01…SA-08, SEC-11 |
| RNF-11 | UT-FE-12 | — | — | ST-17, ST-18 | UAT-02, UAT-05 |
| RNF-12 | — | — | — | — | NF-01, NF-02 |
| RNF-13 | UT-BE-57, UT-FE-26 | IT-02 | CT-21, CT-23…CT-27 | — | — |

---

*Última actualización: Septiembre 2026 · Aplica a Orders & KDS — ERS v4*
*Este plan debe revisarse cuando cambie el ERS, la arquitectura, los contratos con otros equipos o los umbrales acordados. Los valores marcados "(propuesto)" deben validarse con el equipo.*
