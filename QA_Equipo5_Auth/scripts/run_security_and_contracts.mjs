import fs from 'node:fs';
import path from 'node:path';
import jwt from 'jsonwebtoken';
import amqp from 'amqplib';
import pg from 'pg';
import { QA_ADMIN_EMAIL, QA_ADMIN_PASS, QA_ACTIVE_STAFF_PASS } from './seed_qa_auth.mjs';

const { Client } = pg;

const BASE_URL = process.env.BASE_URL || 'http://localhost:4000';
const PG_CONNECTION_STRING =
  process.env.PG_CONNECTION_STRING ||
  'postgresql://postgres:postgres@localhost:5433/fmat_auth';
const RABBITMQ_AMQP_URL =
  process.env.RABBITMQ_AMQP_URL || 'amqp://guest:guest@localhost:5672';
const RABBITMQ_MGMT_URL =
  process.env.RABBITMQ_MGMT_URL || 'http://localhost:15672';
const SERVER_JWT_SECRET = process.env.JWT_SECRET || '';

const EVIDENCIAS_DIR = path.resolve(process.cwd(), 'evidencias');

const CASE_CATALOG = {
  'CP-AUTH-01': {
    titulo: 'Bootstrap de Gerente inicial y bloqueo de concurrencia (pg_advisory_xact_lock)',
    requisitos: ['REQ-AUTH-001', 'BR-AUTH-001', 'INV-AUTH-001'],
  },
  'CP-AUTH-02': {
    titulo: 'Autenticación dual ADMIN (email) y STAFF (XYYYYYY) sin cruce de canales',
    requisitos: ['REQ-AUTH-002', 'REQ-AUTH-009', 'BR-AUTH-002'],
  },
  'CP-AUTH-03': {
    titulo: 'Anti-enumeración en login (401 uniforme y comparación en tiempo constante)',
    requisitos: ['REQ-AUTH-009', 'BR-AUTH-016', 'NFR-AUTH-SEC-003'],
  },
  'CP-AUTH-04': {
    titulo: 'Alta de personal y generación automática de StaffID ^[A-Z][0-9]{6}$ por prefijo',
    requisitos: ['REQ-AUTH-003', 'BR-AUTH-004', 'INV-AUTH-002'],
  },
  'CP-AUTH-05': {
    titulo: 'Integridad PBAC: rechazo roles vacío, bloqueo anti-escalación, SQLi y Mass Assignment',
    requisitos: ['REQ-AUTH-004', 'REQ-AUTH-006', 'INV-AUTH-003', 'INV-AUTH-005'],
  },
  'CP-AUTH-06': {
    titulo: 'Roles dinámicos PBAC (catálogo 56 permisos) y lectura operativa GET /staff + H-AUTH-16',
    requisitos: ['REQ-AUTH-005', 'REQ-AUTH-007', 'BR-AUTH-006'],
  },
  'CP-AUTH-07': {
    titulo: 'Contención estricta TEMPORARY (TemporaryPasswordGuard) y Header Spoofing directo :4000',
    requisitos: ['REQ-AUTH-012', 'REQ-AUTH-013', 'NFR-AUTH-SEC-004', 'INV-AUTH-006'],
  },
  'CP-AUTH-08': {
    titulo: 'Análisis de Valores Límite (BVA) y complejidad en contraseñas (8..72 chars)',
    requisitos: ['REQ-AUTH-013', 'BR-AUTH-011', 'BR-AUTH-012'],
  },
  'CP-AUTH-09': {
    titulo: 'Transición irreversible TEMPORARY -> ACTIVE e inmutabilidad de staffId',
    requisitos: ['REQ-AUTH-013', 'INV-AUTH-002', 'INV-AUTH-006'],
  },
  'CP-AUTH-10': {
    titulo: 'Manipulación criptográfica JWT (alg:none, alg:"", firma ajena, sin exp, URL) y claims',
    requisitos: ['REQ-AUTH-010', 'NFR-AUTH-SEC-002', 'NFR-AUTH-SEC-004'],
  },
  'CP-AUTH-11': {
    titulo: 'Rotación de RefreshToken, rechazo de replay attack e incremento de tokenVersion (tv)',
    requisitos: ['REQ-AUTH-011', 'BR-AUTH-013', 'NFR-AUTH-SEC-002'],
  },
  'CP-AUTH-12': {
    titulo: 'Consulta de sesión activa GET /api/v1/auth/me y proyección de allowedViews',
    requisitos: ['REQ-AUTH-014', 'REQ-AUTH-021'],
  },
  'CP-AUTH-13': {
    titulo: 'Actualización de roles PUT /api/v1/staff/:id/roles con trazabilidad sin mutar staffId',
    requisitos: ['REQ-AUTH-006', 'REQ-AUTH-007', 'INV-AUTH-002'],
  },
  'CP-AUTH-14': {
    titulo: 'Desactivación de personal e invalidación inmediata de AccessToken (tv)',
    requisitos: ['REQ-AUTH-015', 'BR-AUTH-015', 'INV-AUTH-007'],
  },
  'CP-AUTH-15': {
    titulo: 'Publicación de eventos CloudEvents en RabbitMQ (restaurant.staff.events) y H-AUTH-B01/B04',
    requisitos: ['REQ-AUTH-016', 'REQ-AUTH-017', 'NFR-AUTH-AVAIL-002'],
  },
  'CP-AUTH-17': {
    titulo: 'Solicitud homogénea request-password-reset y reset administrativo -> TEMPORARY',
    requisitos: ['REQ-AUTH-017', 'BR-AUTH-014', 'BR-AUTH-016'],
  },
  'CP-AUTH-18': {
    titulo: 'Autogestión restringida en /auth/profile y /auth/account y protección último ADMINISTRADOR',
    requisitos: ['REQ-AUTH-020', 'BR-AUTH-017', 'INV-AUTH-004'],
  },
  'CP-AUTH-21': {
    titulo: 'Auditoría persistente en audit_logs, bcrypt $2b$12$, SHA-256 y headers de seguridad HTTP',
    requisitos: ['REQ-AUTH-022', 'NFR-AUTH-AUD-001', 'NFR-AUTH-SEC-001', 'NFR-AUTH-SEC-004'],
  },
};

function parsePhase() {
  const arg = process.argv.find((a) => a.startsWith('--phase='));
  return arg ? arg.split('=')[1] : 'full';
}

async function httpReq(urlPath, { method = 'GET', token, headers = {}, body, rawBody } = {}) {
  const reqHeaders = { ...headers };
  if (rawBody === undefined && body !== undefined && !reqHeaders['Content-Type']) {
    reqHeaders['Content-Type'] = 'application/json';
  }
  if (token) {
    reqHeaders.Authorization = `Bearer ${token}`;
  }

  const start = performance.now();
  const res = await fetch(`${BASE_URL}${urlPath}`, {
    method,
    headers: reqHeaders,
    body: rawBody !== undefined ? rawBody : body !== undefined ? JSON.stringify(body) : undefined,
  });
  const elapsedMs = Number((performance.now() - start).toFixed(2));
  const text = await res.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    json = null;
  }
  const respHeaders = {};
  res.headers.forEach((v, k) => {
    respHeaders[k.toLowerCase()] = v;
  });
  return { status: res.status, headers: respHeaders, text, json, elapsedMs };
}

function makeVerif(id, origen, nombre, passed, esperado, observado) {
  return {
    id,
    origen,
    nombre,
    estado: passed ? 'PASA' : 'FALLA',
    esperado,
    observado,
  };
}

function writeJson(relPath, obj) {
  const full = path.join(EVIDENCIAS_DIR, relPath);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, JSON.stringify(obj, null, 2));
}

function loadJsonIfExists(relPath, fallback = null) {
  const full = path.join(EVIDENCIAS_DIR, relPath);
  if (!fs.existsSync(full)) return fallback;
  return JSON.parse(fs.readFileSync(full, 'utf8'));
}

async function runBootstrapPhase() {
  const health = await httpReq('/health');
  const before = await httpReq('/api/v1/auth/setup-status');

  const payload = {
    email: QA_ADMIN_EMAIL,
    password: QA_ADMIN_PASS,
    firstName: 'Admin',
    lastName: 'Principal',
  };

  const parallelCalls = Array.from({ length: 12 }, (_, idx) =>
    httpReq('/api/v1/auth/setup-admin', { method: 'POST', body: payload }).then((r) => ({
      requestIndex: idx + 1,
      status: r.status,
      elapsedMs: r.elapsedMs,
      staffId: r.json?.user?.staffId || null,
      code: r.json?.code || null,
      message: r.json?.message || null,
    })),
  );
  const results = await Promise.all(parallelCalls);
  const after = await httpReq('/api/v1/auth/setup-status');

  const count201 = results.filter((r) => r.status === 201).length;
  const count409 = results.filter((r) => r.status === 409).length;
  const createdStaffId = results.find((r) => r.status === 201)?.staffId || null;

  const summary = {
    executedAtUtc: new Date().toISOString(),
    baseUrl: BASE_URL,
    healthCheck: {
      status: health.status,
      body: health.json,
    },
    setupStatusBefore: {
      status: before.status,
      body: before.json,
    },
    concurrentRequests: 12,
    count201,
    count409,
    otherStatuses: results.filter((r) => r.status !== 201 && r.status !== 409),
    createdAdminStaffId: createdStaffId,
    setupStatusAfter: {
      status: after.status,
      body: after.json,
    },
    results,
    passed:
      health.status === 200 &&
      health.json?.dependencies?.rabbitmq === 'connected' &&
      before.status === 200 &&
      before.json?.configured === false &&
      count201 === 1 &&
      count409 === 11 &&
      createdStaffId === 'ADM000001' &&
      after.status === 200 &&
      after.json?.configured === true,
  };

  writeJson('CP-AUTH-01/bootstrap_concurrency.json', summary);
  console.log(
    `[QA-I3][BOOTSTRAP] setup-status before=${before.json?.configured} after=${after.json?.configured} | 12 concurrent calls -> 201:${count201}, 409:${count409}, staffId:${createdStaffId}`,
  );
  if (!summary.passed) {
    process.exitCode = 1;
  }
}

async function loginUser(identifier, password, loginType) {
  const res = await httpReq('/api/v1/auth/login', {
    method: 'POST',
    body: { identifier, password, loginType },
  });
  if (res.status !== 200 || !res.json?.accessToken) {
    throw new Error(`Login falló para ${identifier} (${loginType}): ${res.status} ${res.text}`);
  }
  return res.json;
}

function stats(numbers) {
  const min = Math.min(...numbers);
  const max = Math.max(...numbers);
  const avg = Number((numbers.reduce((a, b) => a + b, 0) / numbers.length).toFixed(2));
  return { samplesMs: numbers, minMs: min, avgMs: avg, maxMs: max };
}

function b64UrlJson(obj) {
  return Buffer.from(JSON.stringify(obj)).toString('base64url');
}

async function runAntiEnumeration(meseroStaffId, inactiveStaffId) {
  const buckets = {
    nonExistent: [],
    wrongPassword: [],
    inactiveAccount: [],
  };

  for (let i = 0; i < 5; i++) {
    buckets.nonExistent.push(
      await httpReq('/api/v1/auth/login', {
        method: 'POST',
        body: { identifier: `Z99999${i}`, password: QA_ACTIVE_STAFF_PASS, loginType: 'STAFF' },
      }),
    );
    buckets.wrongPassword.push(
      await httpReq('/api/v1/auth/login', {
        method: 'POST',
        body: { identifier: meseroStaffId, password: 'WrongPassword99!', loginType: 'STAFF' },
      }),
    );
    buckets.inactiveAccount.push(
      await httpReq('/api/v1/auth/login', {
        method: 'POST',
        body: { identifier: inactiveStaffId, password: QA_ACTIVE_STAFF_PASS, loginType: 'STAFF' },
      }),
    );
  }

  const normalize = (arr) => ({
    statuses: [...new Set(arr.map((r) => r.status))],
    messages: [...new Set(arr.map((r) => r.json?.message))],
    errors: [...new Set(arr.map((r) => r.json?.error))],
    timing: stats(arr.map((r) => r.elapsedMs)),
  });

  const nonEx = normalize(buckets.nonExistent);
  const wrongPw = normalize(buckets.wrongPassword);
  const inactive = normalize(buckets.inactiveAccount);

  const all401 =
    nonEx.statuses.length === 1 &&
    nonEx.statuses[0] === 401 &&
    wrongPw.statuses.length === 1 &&
    wrongPw.statuses[0] === 401 &&
    inactive.statuses.length === 1 &&
    inactive.statuses[0] === 401;

  const sameMsg =
    nonEx.messages.length === 1 &&
    wrongPw.messages.length === 1 &&
    inactive.messages.length === 1 &&
    nonEx.messages[0] === wrongPw.messages[0] &&
    wrongPw.messages[0] === inactive.messages[0];

  const maxDiffMs = Number(
    (
      Math.max(nonEx.timing.avgMs, wrongPw.timing.avgMs, inactive.timing.avgMs) -
      Math.min(nonEx.timing.avgMs, wrongPw.timing.avgMs, inactive.timing.avgMs)
    ).toFixed(2),
  );

  return {
    nonExistent: nonEx,
    wrongPassword: wrongPw,
    inactiveAccount: inactive,
    identicalStatusAndMessage: all401 && sameMsg,
    avgTimingSpreadMs: maxDiffMs,
    indicativeLatencyConsistent:
      nonEx.timing.avgMs > 30 &&
      wrongPw.timing.avgMs > 30 &&
      inactive.timing.avgMs > 30 &&
      maxDiffMs < 150,
    statisticalCaveat:
      'Muestra exploratoria de 5 peticiones por rama (15 totales); no constituye prueba estadística definitiva de tiempo constante. La evaluación estadística bajo carga corresponde a Grafana k6 (Iteración 6).',
  };
}

async function runJwtCryptoAbuse(validToken) {
  const decodedComplete = jwt.decode(validToken, { complete: true });
  const parts = validToken.split('.');
  const rawHeader = JSON.parse(Buffer.from(parts[0], 'base64url').toString('utf8'));
  const rawPayload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));

  const payloadWithoutExp = { ...rawPayload };
  delete payloadWithoutExp.exp;
  delete payloadWithoutExp.iat;

  const foreignToken = jwt.sign(payloadWithoutExp, 'foreign-qa-secret-never-valid-32bytes!', {
    algorithm: 'HS256',
    expiresIn: '15m',
  });
  const garbageSigToken = `${parts[0]}.${parts[1]}.GARBAGE_INVALID_SIGNATURE_XXXX`;
  const algNoneToken = `${b64UrlJson({ alg: 'none', typ: 'JWT' })}.${parts[1]}.`;
  const missingAlgToken = `${b64UrlJson({ typ: 'JWT' })}.${parts[1]}.${parts[2]}`;
  const emptyAlgToken = `${b64UrlJson({ alg: '', typ: 'JWT' })}.${parts[1]}.${parts[2]}`;

  const nowSec = Math.floor(Date.now() / 1000);
  const expiredToken = SERVER_JWT_SECRET
    ? jwt.sign({ ...payloadWithoutExp, iat: nowSec - 1800, exp: nowSec - 60 }, SERVER_JWT_SECRET, {
        algorithm: 'HS256',
      })
    : `${parts[0]}.${b64UrlJson({ ...rawPayload, exp: nowSec - 120 })}.${parts[2]}`;

  const missingExpToken = SERVER_JWT_SECRET
    ? jwt.sign({ ...payloadWithoutExp, iat: nowSec }, SERVER_JWT_SECRET, {
        algorithm: 'HS256',
        noTimestamp: true,
      })
    : null;

  const cases = [
    {
      id: 'ABUSE-JWT-01-FOREIGN-SECRET',
      label: 'JWT firmado con secreto ajeno',
      req: () => httpReq('/api/v1/auth/me', { token: foreignToken }),
      expectedStatus: 401,
    },
    {
      id: 'ABUSE-JWT-02-GARBAGE-SIG',
      label: 'JWT con firma basura alterada',
      req: () => httpReq('/api/v1/auth/me', { token: garbageSigToken }),
      expectedStatus: 401,
    },
    {
      id: 'ABUSE-JWT-03-ALG-NONE',
      label: 'JWT alg: "none" sin firma',
      req: () => httpReq('/api/v1/auth/me', { token: algNoneToken }),
      expectedStatus: 401,
    },
    {
      id: 'ABUSE-JWT-04-MISSING-ALG',
      label: 'JWT cabecera sin campo alg',
      req: () => httpReq('/api/v1/auth/me', { token: missingAlgToken }),
      expectedStatus: 401,
    },
    {
      id: 'ABUSE-JWT-05-EMPTY-ALG',
      label: 'JWT cabecera con alg: ""',
      req: () => httpReq('/api/v1/auth/me', { token: emptyAlgToken }),
      expectedStatus: 401,
    },
    {
      id: 'ABUSE-JWT-06-EXPIRED',
      label: 'JWT expirado (exp en el pasado)',
      req: () => httpReq('/api/v1/auth/me', { token: expiredToken }),
      expectedStatus: 401,
    },
    {
      id: 'ABUSE-JWT-07-NO-EXP-CLAIM',
      label: 'JWT sin claim exp firmado con secreto real (NFR-AUTH-SEC-002 / passport-jwt)',
      req: () =>
        missingExpToken
          ? httpReq('/api/v1/auth/me', { token: missingExpToken })
          : Promise.resolve({ status: -1, json: { error: 'JWT_SECRET not provided to runner' } }),
      expectedStatus: 401,
    },
    {
      id: 'ABUSE-JWT-08-QUERY-TOKEN',
      label: 'JWT enviado por URL (?token=<jwt>) sin header Authorization',
      req: () => httpReq(`/api/v1/auth/me?token=${encodeURIComponent(validToken)}`),
      expectedStatus: 401,
    },
    {
      id: 'ABUSE-JWT-09-QUERY-ACCESS-TOKEN',
      label: 'JWT enviado por URL (?access_token=<jwt>) sin header Authorization',
      req: () => httpReq(`/api/v1/auth/me?access_token=${encodeURIComponent(validToken)}`),
      expectedStatus: 401,
    },
  ];

  const results = [];
  for (const c of cases) {
    const r = await c.req();
    results.push({
      id: c.id,
      label: c.label,
      expectedStatus: c.expectedStatus,
      actualStatus: r.status,
      passed: r.status === c.expectedStatus,
      responseSnippet: r.json || r.text?.slice(0, 200),
    });
  }

  return {
    claimsInspection: {
      alg: decodedComplete?.header?.alg,
      typ: decodedComplete?.header?.typ,
      iss: rawPayload.iss,
      sub: rawPayload.sub,
      aud: rawPayload.aud,
      staffId: rawPayload.staffId,
      roles: rawPayload.roles,
      permissionsCount: Array.isArray(rawPayload.permissions) ? rawPayload.permissions.length : 0,
      tv: rawPayload.tv,
      mustChangePassword: rawPayload.mustChangePassword,
      ttlSeconds: rawPayload.exp && rawPayload.iat ? rawPayload.exp - rawPayload.iat : null,
    },
    results,
  };
}

async function runPerimeterAndTempGuardAbuse(meseroToken, meseroId, tempToken) {
  const spoofHeaders = {
    'X-User-Id': '00000000-0000-4000-8000-000000000001',
    'X-User-Roles': 'ADMINISTRADOR',
    'X-User-Permissions': '*',
    'X-User-Type': 'ADMIN',
  };

  const checks = [];

  const noTokenStaff = await httpReq('/api/v1/staff', { headers: spoofHeaders });
  checks.push({
    id: 'ABUSE-SPOOF-01-NOTOKEN-STAFF',
    label: 'Header spoofing X-User-* sin Bearer token en GET /api/v1/staff -> 401',
    expectedStatus: 401,
    actualStatus: noTokenStaff.status,
    passed: noTokenStaff.status === 401,
    body: noTokenStaff.json,
  });

  const noTokenRoles = await httpReq('/api/v1/roles', { headers: spoofHeaders });
  checks.push({
    id: 'ABUSE-SPOOF-02-NOTOKEN-ROLES',
    label: 'Header spoofing X-User-* sin Bearer token en GET /api/v1/roles -> 401',
    expectedStatus: 401,
    actualStatus: noTokenRoles.status,
    passed: noTokenRoles.status === 401,
    body: noTokenRoles.json,
  });

  const meseroSpoofRoles = await httpReq('/api/v1/roles', {
    token: meseroToken,
    headers: spoofHeaders,
  });
  checks.push({
    id: 'ABUSE-SPOOF-03-MESERO-ROLES',
    label: 'Header spoofing X-User-Roles: ADMINISTRADOR con JWT de MESERO en GET /api/v1/roles -> 403',
    expectedStatus: 403,
    actualStatus: meseroSpoofRoles.status,
    passed: meseroSpoofRoles.status === 403,
    body: meseroSpoofRoles.json,
  });

  const meseroSpoofCreateStaff = await httpReq('/api/v1/staff', {
    method: 'POST',
    token: meseroToken,
    headers: spoofHeaders,
    body: { firstName: 'Spoof', lastName: 'Admin', roles: ['MESERO'] },
  });
  checks.push({
    id: 'ABUSE-SPOOF-04-MESERO-CREATE-STAFF',
    label: 'Header spoofing con JWT de MESERO en POST /api/v1/staff -> 403',
    expectedStatus: 403,
    actualStatus: meseroSpoofCreateStaff.status,
    passed: meseroSpoofCreateStaff.status === 403,
    body: meseroSpoofCreateStaff.json,
  });

  const meseroSpoofEscalateSelf = await httpReq(`/api/v1/staff/${meseroId}/roles`, {
    method: 'PUT',
    token: meseroToken,
    headers: spoofHeaders,
    body: { roles: ['ADMINISTRADOR'] },
  });
  checks.push({
    id: 'ABUSE-SPOOF-05-MESERO-SELF-ROLES',
    label: 'Header spoofing con JWT de MESERO en PUT /api/v1/staff/:id/roles -> 403',
    expectedStatus: 403,
    actualStatus: meseroSpoofEscalateSelf.status,
    passed: meseroSpoofEscalateSelf.status === 403,
    body: meseroSpoofEscalateSelf.json,
  });

  const tempStaff = await httpReq('/api/v1/staff', { token: tempToken });
  checks.push({
    id: 'ABUSE-TEMP-01-GET-STAFF',
    label: 'TEMPORARY bloqueado directo en :4000 GET /api/v1/staff -> 403 PASSWORD_CHANGE_REQUIRED',
    expectedStatus: '403 + code=PASSWORD_CHANGE_REQUIRED',
    actualStatus: tempStaff.status,
    actualCode: tempStaff.json?.code || null,
    passed: tempStaff.status === 403 && tempStaff.json?.code === 'PASSWORD_CHANGE_REQUIRED',
    body: tempStaff.json,
  });

  const tempRoles = await httpReq('/api/v1/roles', { token: tempToken });
  checks.push({
    id: 'ABUSE-TEMP-02-GET-ROLES',
    label: 'TEMPORARY bloqueado directo en :4000 GET /api/v1/roles -> 403 PASSWORD_CHANGE_REQUIRED',
    expectedStatus: '403 + code=PASSWORD_CHANGE_REQUIRED',
    actualStatus: tempRoles.status,
    actualCode: tempRoles.json?.code || null,
    passed: tempRoles.status === 403 && tempRoles.json?.code === 'PASSWORD_CHANGE_REQUIRED',
    body: tempRoles.json,
  });

  const tempProfile = await httpReq('/api/v1/auth/profile', {
    method: 'PATCH',
    token: tempToken,
    body: { firstName: 'Temp', lastName: 'Bypass' },
  });
  checks.push({
    id: 'ABUSE-TEMP-03-PATCH-PROFILE',
    label: 'TEMPORARY en PATCH /api/v1/auth/profile -> esperado 403 PASSWORD_CHANGE_REQUIRED (TemporaryPasswordGuard)',
    expectedStatus: '403 + code=PASSWORD_CHANGE_REQUIRED',
    actualStatus: tempProfile.status,
    actualCode: tempProfile.json?.code || null,
    passed: tempProfile.status === 403 && tempProfile.json?.code === 'PASSWORD_CHANGE_REQUIRED',
    http403Observed: tempProfile.status === 403,
    body: tempProfile.json,
  });

  const tempAccount = await httpReq('/api/v1/auth/account', {
    method: 'DELETE',
    token: tempToken,
  });
  checks.push({
    id: 'ABUSE-TEMP-04-DELETE-ACCOUNT',
    label: 'TEMPORARY en DELETE /api/v1/auth/account -> esperado 403 PASSWORD_CHANGE_REQUIRED (TemporaryPasswordGuard)',
    expectedStatus: '403 + code=PASSWORD_CHANGE_REQUIRED',
    actualStatus: tempAccount.status,
    actualCode: tempAccount.json?.code || null,
    passed: tempAccount.status === 403 && tempAccount.json?.code === 'PASSWORD_CHANGE_REQUIRED',
    http403Observed: tempAccount.status === 403,
    body: tempAccount.json,
  });

  return checks;
}

async function runCorsSqliMassAssignment(adminToken) {
  const results = [];

  // 1. CORS Malicious Origin
  const corsMalOpt = await httpReq('/api/v1/auth/me', {
    method: 'OPTIONS',
    headers: {
      Origin: 'https://sitio-malicioso.example',
      'Access-Control-Request-Method': 'GET',
      'Access-Control-Request-Headers': 'Authorization,Content-Type',
    },
  });
  const corsMalGet = await httpReq('/api/v1/auth/me', {
    method: 'GET',
    token: adminToken,
    headers: { Origin: 'https://sitio-malicioso.example' },
  });
  const acaoMal = corsMalOpt.headers['access-control-allow-origin'] || corsMalGet.headers['access-control-allow-origin'] || null;
  const acacMal = corsMalOpt.headers['access-control-allow-credentials'] || corsMalGet.headers['access-control-allow-credentials'] || null;

  results.push({
    id: 'ABUSE-CORS-01-MALICIOUS',
    label: 'CORS rechaza https://sitio-malicioso.example sin reflejar origen ni Allow-Credentials: true',
    passed: acaoMal !== 'https://sitio-malicioso.example' && !(acaoMal === '*' && acacMal === 'true'),
    observed: {
      optionsStatus: corsMalOpt.status,
      getStatus: corsMalGet.status,
      allowOrigin: acaoMal,
      allowCredentials: acacMal,
    },
  });

  // 2. CORS cocinaApp (:5179) vs docker-compose.yml default
  const cors5179Opt = await httpReq('/api/v1/auth/me', {
    method: 'OPTIONS',
    headers: {
      Origin: 'http://localhost:5179',
      'Access-Control-Request-Method': 'GET',
      'Access-Control-Request-Headers': 'Authorization,Content-Type',
    },
  });
  const acao5179 = cors5179Opt.headers['access-control-allow-origin'] || null;
  results.push({
    id: 'ABUSE-CORS-02-COCINA-5179',
    label: 'Verificación CORS para http://localhost:5179 (cocinaApp en docker-compose.yml)',
    passed: acao5179 === 'http://localhost:5179',
    observed: {
      optionsStatus: cors5179Opt.status,
      allowOrigin: acao5179,
      note:
        acao5179 === 'http://localhost:5179'
          ? '5179 permitido'
          : 'http://localhost:5179 omitido en CORS_ORIGINS por defecto de Auth-Backend/docker-compose.yml (solo 3000 y 5173..5178)',
    },
  });

  // 3. Mass Assignment en POST /api/v1/staff + verificación directa en PostgreSQL
  const massRes = await httpReq('/api/v1/staff', {
    method: 'POST',
    token: adminToken,
    body: {
      firstName: 'Mass',
      lastName: 'Assignment',
      phone: '9998887766',
      roles: ['MESERO'],
      isActive: false,
      passwordStatus: 'ACTIVE',
      tokenVersion: 99,
      userType: 'ADMIN',
      permissions: ['*'],
      staffId: 'ADM999999',
    },
  });

  let dbRow = null;
  if (massRes.status === 201 && massRes.json?.user?.id) {
    const createdId = massRes.json.user.id;
    const client = new Client({ connectionString: PG_CONNECTION_STRING });
    await client.connect();
    try {
      const q = await client.query(
        'SELECT u."userType", u."passwordStatus", u."isActive", u."tokenVersion", sp."staffId" FROM users u JOIN staff_profiles sp ON sp."userId" = u.id WHERE u.id = $1',
        [createdId],
      );
      dbRow = q.rows[0] || null;
    } finally {
      await client.end();
    }
    await httpReq(`/api/v1/staff/${createdId}`, { method: 'DELETE', token: adminToken });
  }

  const massProtected =
    massRes.status === 201 &&
    dbRow !== null &&
    dbRow.userType === 'STAFF' &&
    dbRow.passwordStatus === 'TEMPORARY' &&
    dbRow.isActive === true &&
    dbRow.tokenVersion === 0 &&
    dbRow.staffId !== 'ADM999999';

  results.push({
    id: 'ABUSE-MASS-ASSIGNMENT-01',
    label: 'Mass Assignment en POST /api/v1/staff no sobreescribe userType/passwordStatus/isActive/tokenVersion/staffId',
    passed: massProtected,
    observed: {
      httpStatus: massRes.status,
      persistedRow: dbRow,
    },
  });

  // 4. SQL Injection en login y filtro GET /staff
  const sqliLogin = await httpReq('/api/v1/auth/login', {
    method: 'POST',
    body: {
      identifier: "admin@fmat.edu.mx' OR '1'='1",
      password: "' OR '1'='1",
      loginType: 'ADMIN',
    },
  });
  const sqliFilter = await httpReq(
    `/api/v1/staff?search=${encodeURIComponent("' UNION SELECT id,email FROM users--")}&role=${encodeURIComponent("' OR 1=1--")}`,
    { token: adminToken },
  );

  results.push({
    id: 'ABUSE-SQLI-01',
    label: 'Payloads SQLi en login (401) y filtros GET /staff (sin 500 ni fuga de esquema SQL)',
    passed:
      sqliLogin.status === 401 &&
      sqliFilter.status < 500 &&
      !JSON.stringify(sqliFilter.json || {}).toLowerCase().includes('syntax error'),
    observed: {
      loginStatus: sqliLogin.status,
      filterStatus: sqliFilter.status,
      filterTotalReturned: Array.isArray(sqliFilter.json?.data) ? sqliFilter.json.data.length : null,
    },
  });

  return results;
}

async function runReplayTokenVersionAudit(adminToken) {
  // Crea un colaborador temporal, lo activa, rota su refreshToken, ejecuta replay y mide tv antes/después en DB
  const created = await httpReq('/api/v1/staff', {
    method: 'POST',
    token: adminToken,
    body: { firstName: 'Replay', lastName: 'TvCheck', roles: ['MESERO'] },
  });
  const staffUser = created.json.user;
  const loginTemp = await loginUser(staffUser.staffId, created.json.temporaryPassword, 'STAFF');
  const changeRes = await httpReq('/api/v1/auth/change-initial-password', {
    method: 'POST',
    token: loginTemp.accessToken,
    body: { currentPassword: created.json.temporaryPassword, newPassword: QA_ACTIVE_STAFF_PASS },
  });
  if (changeRes.status !== 200) {
    throw new Error(`No se pudo activar usuario temporal para replay check: ${changeRes.status}`);
  }

  const activeLogin = await loginUser(staffUser.staffId, QA_ACTIVE_STAFF_PASS, 'STAFF');
  const initialClaims = jwt.decode(activeLogin.accessToken);
  const firstRefresh = activeLogin.refreshToken;

  const rotate1 = await httpReq('/api/v1/auth/refresh', {
    method: 'POST',
    body: { refreshToken: firstRefresh },
  });
  const rotatedClaims = rotate1.json?.accessToken ? jwt.decode(rotate1.json.accessToken) : null;

  // Replay del primer refreshToken ya rotado
  const replayRes = await httpReq('/api/v1/auth/refresh', {
    method: 'POST',
    body: { refreshToken: firstRefresh },
  });

  // Consultar DB para verificar tokenVersion tras replay
  const client = new Client({ connectionString: PG_CONNECTION_STRING });
  await client.connect();
  let dbAfterReplay = null;
  try {
    const q = await client.query(
      'SELECT "tokenVersion" FROM users WHERE id = $1',
      [staffUser.id],
    );
    dbAfterReplay = q.rows[0]?.tokenVersion ?? null;
  } finally {
    await client.end();
  }

  // Probar si el segundo accessToken sigue vivo o si tv se incrementó
  const checkAfterReplay = rotate1.json?.accessToken
    ? await httpReq('/api/v1/auth/me', { token: rotate1.json.accessToken })
    : { status: -1 };

  await httpReq(`/api/v1/staff/${staffUser.id}`, { method: 'DELETE', token: adminToken });

  return {
    initialTv: initialClaims?.tv,
    rotatedTv: rotatedClaims?.tv,
    replayStatus: replayRes.status,
    replayBody: replayRes.json,
    dbTokenVersionAfterReplay: dbAfterReplay,
    tvIncrementedOnReplay: dbAfterReplay !== null && dbAfterReplay > initialClaims?.tv,
    rotatedAccessTokenStatusAfterReplay: checkAfterReplay.status,
  };
}

async function runDeleteRevocationCheck(adminToken) {
  const created = await httpReq('/api/v1/staff', {
    method: 'POST',
    token: adminToken,
    body: { firstName: 'Delete', lastName: 'RevokeCheck', roles: ['MESERO'] },
  });
  if (created.status !== 201 || !created.json?.user?.id) {
    throw new Error(`No se pudo crear colaborador para revocación tras DELETE: ${created.status}`);
  }
  const staffUser = created.json.user;
  const loginTemp = await loginUser(staffUser.staffId, created.json.temporaryPassword, 'STAFF');
  const changeRes = await httpReq('/api/v1/auth/change-initial-password', {
    method: 'POST',
    token: loginTemp.accessToken,
    body: { currentPassword: created.json.temporaryPassword, newPassword: QA_ACTIVE_STAFF_PASS },
  });
  if (changeRes.status !== 200) {
    throw new Error(`No se pudo activar usuario temporal para DELETE check: ${changeRes.status}`);
  }

  const activeLogin = await loginUser(staffUser.staffId, QA_ACTIVE_STAFF_PASS, 'STAFF');
  const meBeforeDelete = await httpReq('/api/v1/auth/me', { token: activeLogin.accessToken });
  const deleteRes = await httpReq(`/api/v1/staff/${staffUser.id}`, {
    method: 'DELETE',
    token: adminToken,
  });
  const meAfterDelete = await httpReq('/api/v1/auth/me', { token: activeLogin.accessToken });

  return {
    staffId: staffUser.staffId,
    meBeforeDeleteStatus: meBeforeDelete.status,
    deleteStatus: deleteRes.status,
    meAfterDeleteStatus: meAfterDelete.status,
    meAfterDeleteBody: meAfterDelete.json,
    passed:
      meBeforeDelete.status === 200 &&
      deleteRes.status === 200 &&
      meAfterDelete.status === 401,
  };
}

async function runLiveRabbitMqAudit(adminToken) {
  const conn = await amqp.connect(RABBITMQ_AMQP_URL);
  const ch = await conn.createChannel();
  const exchange = 'restaurant.staff.events';
  const captured = [];

  try {
    await ch.checkExchange(exchange);
    const { queue } = await ch.assertQueue('', { exclusive: true, autoDelete: true });
    await ch.bindQueue(queue, exchange, '#');

    await ch.consume(
      queue,
      (msg) => {
        if (!msg) return;
        let parsed = null;
        try {
          parsed = JSON.parse(msg.content.toString('utf8'));
        } catch {
          parsed = msg.content.toString('utf8');
        }
        captured.push({
          receivedAtUtc: new Date().toISOString(),
          routingKey: msg.fields.routingKey,
          deliveryMode: msg.properties.deliveryMode,
          contentType: msg.properties.contentType,
          payload: parsed,
        });
      },
      { noAck: true },
    );

    const waitAndDrain = async (ms) => {
      await new Promise((r) => setTimeout(r, ms));
    };

    const startIdx0 = captured.length;
    const createRes = await httpReq('/api/v1/staff', {
      method: 'POST',
      token: adminToken,
      body: { firstName: 'Rabbit', lastName: 'Audit', roles: ['MESERO'] },
    });
    const staffIdUuid = createRes.json?.user?.id;
    await waitAndDrain(1200);
    const eventsAfterCreate = captured.slice(startIdx0);

    // Trigger H-AUTH-B01: PATCH /api/v1/staff/:id cambiando roles e isActive
    const startIdx1 = captured.length;
    const patchStaffRes = await httpReq(`/api/v1/staff/${staffIdUuid}`, {
      method: 'PATCH',
      token: adminToken,
      body: { firstName: 'RabbitPatch', roles: ['CAJERO'], isActive: false },
    });
    await waitAndDrain(1500);
    const eventsAfterPatchStaff = captured.slice(startIdx1);

    // Trigger PUT /api/v1/staff/:id/roles (para ver evento real y contrastar H-AUTH-B04)
    const startIdx2 = captured.length;
    const putRolesRes = await httpReq(`/api/v1/staff/${staffIdUuid}/roles`, {
      method: 'PUT',
      token: adminToken,
      body: { roles: ['MESERO'] },
    });
    await waitAndDrain(1200);
    const eventsAfterPutRoles = captured.slice(startIdx2);

    // Trigger PATCH /api/v1/staff/:id/status y DELETE /api/v1/staff/:id
    const startIdx3 = captured.length;
    const patchStatusRes = await httpReq(`/api/v1/staff/${staffIdUuid}/status`, {
      method: 'PATCH',
      token: adminToken,
      body: { isActive: true },
    });
    await waitAndDrain(1200);
    const eventsAfterPatchStatus = captured.slice(startIdx3);

    const startIdx4 = captured.length;
    const deleteRes = await httpReq(`/api/v1/staff/${staffIdUuid}`, {
      method: 'DELETE',
      token: adminToken,
    });
    await waitAndDrain(1200);
    const eventsAfterDelete = captured.slice(startIdx4);

    // Consultar RabbitMQ Management API para confirmar metadatos del exchange
    let mgmtExchangeInfo = null;
    try {
      const authB64 = Buffer.from('guest:guest').toString('base64');
      const mgmtRes = await fetch(
        `${RABBITMQ_MGMT_URL}/api/exchanges/%2F/${encodeURIComponent(exchange)}`,
        { headers: { Authorization: `Basic ${authB64}` } },
      );
      if (mgmtRes.ok) mgmtExchangeInfo = await mgmtRes.json();
    } catch {
      mgmtExchangeInfo = null;
    }

    const firstEventData = captured.find((e) => e.payload?.data)?.payload?.data || {};
    const hAuthB01EmitsOnPatchStaff = eventsAfterPatchStaff.length > 0;
    const hAuthB04HasPermissionsAndRoleLabel =
      Object.prototype.hasOwnProperty.call(firstEventData, 'permissions') &&
      Object.prototype.hasOwnProperty.call(firstEventData, 'roleLabel');

    const rabbitReport = {
      executedAtUtc: new Date().toISOString(),
      exchange,
      managementExchange: mgmtExchangeInfo
        ? {
            name: mgmtExchangeInfo.name,
            type: mgmtExchangeInfo.type,
            durable: mgmtExchangeInfo.durable,
            auto_delete: mgmtExchangeInfo.auto_delete,
          }
        : null,
      totalEventsCaptured: captured.length,
      triggers: {
        createStaffStatus: createRes.status,
        eventsAfterCreateCount: eventsAfterCreate.length,
        patchStaffStatus: patchStaffRes.status,
        eventsAfterPatchStaffCount: eventsAfterPatchStaff.length,
        putRolesStatus: putRolesRes.status,
        eventsAfterPutRolesCount: eventsAfterPutRoles.length,
        patchStatusHttp: patchStatusRes.status,
        eventsAfterPatchStatusCount: eventsAfterPatchStatus.length,
        deleteStatus: deleteRes.status,
        eventsAfterDeleteCount: eventsAfterDelete.length,
      },
      hallazgos: {
        'H-AUTH-B01': {
          descripcion: 'PATCH /api/v1/staff/:id cambiando roles e isActive emite evento RabbitMQ',
          corregido: hAuthB01EmitsOnPatchStaff,
          estado: hAuthB01EmitsOnPatchStaff ? 'RESUELTO' : 'ABIERTO',
          eventosEmitidosEnPatchStaff: eventsAfterPatchStaff.length,
        },
        'H-AUTH-B04': {
          descripcion: 'CloudEvent data incluye permissions y roleLabel conforme a architecture.md',
          corregido: hAuthB04HasPermissionsAndRoleLabel,
          estado: hAuthB04HasPermissionsAndRoleLabel ? 'RESUELTO' : 'ABIERTO',
          clavesObservadasEnData: Object.keys(firstEventData),
        },
      },
      capturedEvents: captured,
    };

    writeJson('CP-AUTH-15/rabbitmq_events.json', rabbitReport);
    return rabbitReport;
  } finally {
    await ch.close();
    await conn.close();
  }
}

async function runSecurityHeadersAudit(adminToken) {
  const health = await httpReq('/health');
  const me = await httpReq('/api/v1/auth/me', { token: adminToken });

  return {
    noXPoweredBy:
      health.headers['x-powered-by'] === undefined && me.headers['x-powered-by'] === undefined,
    cacheControlNoStore:
      (me.headers['cache-control'] || '').includes('no-store') &&
      (health.headers['cache-control'] || '').includes('no-store'),
    pragmaNoCache: (me.headers['pragma'] || '').includes('no-cache'),
    xFrameOptionsDeny: (me.headers['x-frame-options'] || '').toUpperCase() === 'DENY',
    xContentTypeOptionsNosniff:
      (me.headers['x-content-type-options'] || '').toLowerCase() === 'nosniff',
    hasCsp: Boolean(me.headers['content-security-policy']),
    sampleHeadersMe: {
      'x-powered-by': me.headers['x-powered-by'] || null,
      'cache-control': me.headers['cache-control'] || null,
      pragma: me.headers['pragma'] || null,
      'x-frame-options': me.headers['x-frame-options'] || null,
      'x-content-type-options': me.headers['x-content-type-options'] || null,
      'content-security-policy': me.headers['content-security-policy'] || null,
    },
  };
}

function collectNewmanAssertions(newmanReport) {
  const byCase = {};
  for (const caseId of Object.keys(CASE_CATALOG)) {
    byCase[caseId] = [];
  }

  const executions = newmanReport?.run?.executions || [];
  for (const ex of executions) {
    const itemName = ex.item?.name || '';
    let targetCase = null;

    if (itemName.startsWith('H-AUTH-16:')) {
      targetCase = 'CP-AUTH-06';
    } else if (itemName.includes('CP-AUTH-08/09')) {
      targetCase = 'CP-AUTH-08';
    } else {
      const m = itemName.match(/CP-AUTH-(\d{2})/);
      if (m) targetCase = `CP-AUTH-${m[1]}`;
    }

    if (!targetCase || !byCase[targetCase]) continue;

    const assertions = (ex.assertions || []).map((a) => ({
      assertion: a.assertion,
      passed: !a.error,
      error: a.error ? a.error.message : null,
    }));

    byCase[targetCase].push({
      requestName: itemName,
      method: ex.request?.method,
      statusCode: ex.response?.code,
      responseTimeMs: ex.response?.responseTime,
      passed: assertions.length > 0 && assertions.every((a) => a.passed),
      assertions,
    });
  }

  return byCase;
}

function buildRegistro({
  caseId,
  newmanItems,
  extraVerifs,
  meta,
  extraPayloads = {},
  nota = null,
}) {
  const cat = CASE_CATALOG[caseId];
  const verifs = [];

  for (const item of newmanItems) {
    verifs.push({
      id: `NEWMAN-${verifs.length + 1}`,
      origen: 'newman',
      nombre: item.requestName,
      estado: item.passed ? 'PASA' : 'FALLA',
      statusCode: item.statusCode,
      tiempoMs: item.responseTimeMs,
      assertionsTotal: item.assertions.length,
      assertionsFallidas: item.assertions.filter((a) => !a.passed),
    });
  }
  verifs.push(...extraVerifs);

  const hasFail = verifs.some((v) => v.estado === 'FALLA');
  const hasPass = verifs.some((v) => v.estado === 'PASA');
  const estadoGlobal = !hasFail && hasPass ? 'PASA' : hasFail && hasPass ? 'FALLA_PARCIAL' : hasFail ? 'FALLA' : 'SIN_VERIFICACIONES';

  return {
    casoId: caseId,
    titulo: cat.titulo,
    requisitos: cat.requisitos,
    ejecutadoEnUtc: new Date().toISOString(),
    commits: meta?.repositorios || {},
    estadoGlobal,
    resumenVerificaciones: {
      total: verifs.length,
      pasan: verifs.filter((v) => v.estado === 'PASA').length,
      fallan: verifs.filter((v) => v.estado === 'FALLA').length,
    },
    verificaciones: verifs,
    nota,
    evidenciaAdicional: extraPayloads,
  };
}

async function runFullPhase() {
  const runtimeState = loadJsonIfExists('.runtime_state.json');
  if (!runtimeState) {
    throw new Error('Falta evidencias/.runtime_state.json (ejecute primero seed_qa_auth.mjs)');
  }
  const meta = loadJsonIfExists('metadata_ejecucion.json', {});
  const bootstrapData = loadJsonIfExists('CP-AUTH-01/bootstrap_concurrency.json');
  const newmanReport = loadJsonIfExists('newman-report.json');
  const newmanByCase = collectNewmanAssertions(newmanReport);

  const adminLogin = await loginUser(QA_ADMIN_EMAIL, QA_ADMIN_PASS, 'ADMIN');
  const meseroLogin = await loginUser(runtimeState.staff.mesero.staffId, QA_ACTIVE_STAFF_PASS, 'STAFF');
  const tempLogin = await loginUser(
    runtimeState.staff.tempOne.staffId,
    runtimeState.staff.tempOne.initialTemporaryPassword || 'TempQA123!',
    'STAFF',
  );

  // Ejecutar baterías en vivo
  const antiEnum = await runAntiEnumeration(
    runtimeState.staff.mesero.staffId,
    runtimeState.staff.inactiveOne.staffId,
  );
  const jwtAbuse = await runJwtCryptoAbuse(adminLogin.accessToken);
  const perimeterAbuse = await runPerimeterAndTempGuardAbuse(
    meseroLogin.accessToken,
    runtimeState.staff.mesero.id,
    tempLogin.accessToken,
  );
  const corsSqliMass = await runCorsSqliMassAssignment(adminLogin.accessToken);
  const replayAudit = await runReplayTokenVersionAudit(adminLogin.accessToken);
  const deleteRevocationAudit = await runDeleteRevocationCheck(adminLogin.accessToken);
  const rabbitAudit = await runLiveRabbitMqAudit(adminLogin.accessToken);
  const secHeaders = await runSecurityHeadersAudit(adminLogin.accessToken);

  // Guardar logs detallados de Abuse Cases
  writeJson('security_abuse_results.json', {
    executedAtUtc: new Date().toISOString(),
    antiEnumeration: antiEnum,
    jwtCryptoAbuse: jwtAbuse,
    perimeterAndTempGuard: perimeterAbuse,
    corsSqliAndMassAssignment: corsSqliMass,
    replayTokenVersion: replayAudit,
    deleteRevocation: deleteRevocationAudit,
    securityHeaders: secHeaders,
  });

  // Consolidar los 18 registros CP-AUTH-XX/registro.json
  const extraByCase = {
    'CP-AUTH-01': [
      makeVerif(
        'SEC-BOOT-01',
        'script-concurrencia',
        '12 peticiones concurrentes POST /setup-admin sobre BD vacía -> exactamente 1x201 y 11x409 (pg_advisory_xact_lock)',
        Boolean(bootstrapData?.passed),
        'configured: false -> true, 1x201 (ADM000001), 11x409',
        bootstrapData
          ? {
              configuredBefore: bootstrapData.setupStatusBefore?.body?.configured,
              configuredAfter: bootstrapData.setupStatusAfter?.body?.configured,
              count201: bootstrapData.count201,
              count409: bootstrapData.count409,
              createdAdminStaffId: bootstrapData.createdAdminStaffId,
            }
          : 'No ejecutado',
      ),
    ],
    'CP-AUTH-02': [
      makeVerif(
        'SEED-DUAL-01',
        'seed-verificacion',
        `Credenciales sembradas diferenciadas: ADMIN por email y STAFF activo MESERO por ${runtimeState.staff.mesero.staffId}`,
        Boolean(adminLogin.accessToken && meseroLogin.accessToken),
        'Ambos obtienen JWT HS256 válidos por sus canales exclusivos',
        { adminStaffId: adminLogin.user?.staffId, meseroStaffId: meseroLogin.user?.staffId },
      ),
    ],
    'CP-AUTH-03': [
      makeVerif(
        'ANTI-ENUM-01',
        'timing-script',
        'Anti-enumeración 15 muestras (5 inexistente, 5 pass incorrecto, 5 inactiva): código 401 y mensaje idéntico',
        antiEnum.identicalStatusAndMessage,
        '401 + mensaje idéntico en las 3 ramas',
        {
          nonExistent: antiEnum.nonExistent.messages,
          wrongPassword: antiEnum.wrongPassword.messages,
          inactiveAccount: antiEnum.inactiveAccount.messages,
        },
      ),
      makeVerif(
        'ANTI-ENUM-02',
        'timing-script',
        'Verificación indicativa de latencia (5 muestras/rama; compatible con bcrypt $2b$12$, no prueba estadística definitiva de tiempo constante)',
        antiEnum.indicativeLatencyConsistent,
        'Muestra exploratoria compatible con hashing bcrypt en las 3 ramas (sin reemplazar prueba estadística k6)',
        {
          avgNonExistentMs: antiEnum.nonExistent.timing.avgMs,
          avgWrongPassMs: antiEnum.wrongPassword.timing.avgMs,
          avgInactiveMs: antiEnum.inactiveAccount.timing.avgMs,
          spreadMs: antiEnum.avgTimingSpreadMs,
          statisticalCaveat: antiEnum.statisticalCaveat,
        },
      ),
    ],
    'CP-AUTH-04': [
      makeVerif(
        'PREFIX-SEED-01',
        'seed-verificacion',
        'Todos los colaboradores sembrados (H/A/G/M/C/J/E) cumplen prefijo por rol y ^[A-Z][0-9]{6}$',
        Object.values(runtimeState.staff).every((s) =>
          new RegExp(`^${s.expectedPrefix}[0-9]{6}$`).test(s.staffId),
        ),
        'Prefijo exacto según rol base y rol dinámico E',
        Object.fromEntries(
          Object.entries(runtimeState.staff).map(([k, v]) => [k, v.staffId]),
        ),
      ),
    ],
    'CP-AUTH-05': corsSqliMass
      .filter((r) => r.id.startsWith('ABUSE-MASS') || r.id.startsWith('ABUSE-SQLI'))
      .map((r) =>
        makeVerif(r.id, 'security-abuse', r.label, r.passed, 'Rechazo o saneamiento estricto sin escalación', r.observed),
      ),
    'CP-AUTH-06': [
      makeVerif(
        'SEED-CUSTOM-ROLE-01',
        'seed-verificacion',
        `Rol dinámico ROL_ENCARGADO_OP (prefijo E) creado en orden mínimo y asignado a ${runtimeState.staff?.customPbac?.staffId} (intento E000001)`,
        runtimeState.customRole?.code === 'ROL_ENCARGADO_OP' &&
          runtimeState.staff?.customPbac?.staffId?.startsWith('E'),
        'ROL_ENCARGADO_OP y staffId prefijo E',
        {
          roleCode: runtimeState.customRole?.code,
          staffId: runtimeState.staff?.customPbac?.staffId,
          staffIdSequenceObservation: runtimeState.staffIdSequenceObservation || null,
        },
      ),
    ],
    'CP-AUTH-07': perimeterAbuse.map((p) =>
      makeVerif(p.id, 'security-abuse', p.label, p.passed, p.expectedStatus, {
        actualStatus: p.actualStatus,
        actualCode: p.actualCode || null,
        body: p.body,
      }),
    ),
    'CP-AUTH-08': [],
    'CP-AUTH-09': [],
    'CP-AUTH-10': [
      makeVerif(
        'JWT-CLAIMS-01',
        'jwt-inspection',
        'Claims obligatorios presentes (iss, sub, aud, staffId, roles, permissions, tv, mustChangePassword, exp=900s)',
        jwtAbuse.claimsInspection.iss === 'urn:fmat:restaurant:auth' &&
          jwtAbuse.claimsInspection.aud === 'urn:fmat:restaurant' &&
          jwtAbuse.claimsInspection.ttlSeconds === 900 &&
          typeof jwtAbuse.claimsInspection.tv === 'number',
        'iss=urn:fmat:restaurant:auth, aud=urn:fmat:restaurant, TTL=900s',
        jwtAbuse.claimsInspection,
      ),
      ...jwtAbuse.results.map((r) =>
        makeVerif(r.id, 'security-abuse', r.label, r.passed, r.expectedStatus, {
          actualStatus: r.actualStatus,
          body: r.responseSnippet,
        }),
      ),
    ],
    'CP-AUTH-11': [
      makeVerif(
        'REPLAY-TV-01',
        'security-abuse',
        'Replay con RefreshToken rotado responde 401 e incrementa tokenVersion (tv) en usuario',
        replayAudit.replayStatus === 401 && replayAudit.tvIncrementedOnReplay,
        'replayStatus=401 y tv incremented en replay',
        replayAudit,
      ),
    ],
    'CP-AUTH-12': [],
    'CP-AUTH-13': [],
    'CP-AUTH-14': [
      makeVerif(
        'REVOKE-DELETE-01',
        'security-abuse',
        'AccessToken previo queda revocado inmediatamente tras DELETE /api/v1/staff/:id -> 401',
        deleteRevocationAudit.passed,
        '200 antes de DELETE -> 200 DELETE /staff/:id -> 401 en GET /auth/me inmediato',
        deleteRevocationAudit,
      ),
    ],
    'CP-AUTH-15': [
      makeVerif(
        'RMQ-EXCHANGE-01',
        'rabbitmq-live',
        'Exchange durable restaurant.staff.events (topic) activo en RabbitMQ real',
        rabbitAudit.managementExchange?.durable === true && rabbitAudit.managementExchange?.type === 'topic',
        'topic durable=true',
        rabbitAudit.managementExchange,
      ),
      makeVerif(
        'RMQ-EVENT-CREATE-DELETE',
        'rabbitmq-live',
        'Eventos reales capturados en cola temporal al crear/cambiar estado/roles/eliminar staff',
        rabbitAudit.triggers.eventsAfterCreateCount >= 1 &&
          rabbitAudit.triggers.eventsAfterPutRolesCount >= 1 &&
          rabbitAudit.triggers.eventsAfterPatchStatusCount >= 1 &&
          rabbitAudit.triggers.eventsAfterDeleteCount >= 1,
        '>= 1 evento por operación',
        rabbitAudit.triggers,
      ),
      makeVerif(
        'RMQ-H-AUTH-B01',
        'rabbitmq-live',
        'H-AUTH-B01: PATCH /api/v1/staff/:id cambiando roles e isActive emite evento RabbitMQ',
        rabbitAudit.hallazgos['H-AUTH-B01'].corregido,
        'Evento emitido al actualizar roles/isActive vía PATCH /staff/:id',
        rabbitAudit.hallazgos['H-AUTH-B01'],
      ),
      makeVerif(
        'RMQ-H-AUTH-B04',
        'rabbitmq-live',
        'H-AUTH-B04: CloudEvent data incluye permissions y roleLabel conforme a architecture.md',
        rabbitAudit.hallazgos['H-AUTH-B04'].corregido,
        'Claves permissions y roleLabel presentes en payload.data',
        rabbitAudit.hallazgos['H-AUTH-B04'],
      ),
    ],
    'CP-AUTH-17': [],
    'CP-AUTH-18': [],
    'CP-AUTH-21': [
      makeVerif(
        'SEC-HEADERS-01',
        'http-headers',
        'Headers de seguridad HTTP: ausencia X-Powered-By, Cache-Control: no-store, Pragma: no-cache, X-Frame-Options: DENY, CSP',
        secHeaders.noXPoweredBy &&
          secHeaders.cacheControlNoStore &&
          secHeaders.pragmaNoCache &&
          secHeaders.xFrameOptionsDeny &&
          secHeaders.xContentTypeOptionsNosniff &&
          secHeaders.hasCsp,
        'Todos los headers Helmet y no-store activos',
        secHeaders.sampleHeadersMe,
      ),
      ...corsSqliMass
        .filter((r) => r.id.startsWith('ABUSE-CORS'))
        .map((r) =>
          makeVerif(r.id, 'security-abuse', r.label, r.passed, 'Política CORS estricta', r.observed),
        ),
    ],
  };

  const consolidatedSummary = [];
  for (const caseId of Object.keys(CASE_CATALOG)) {
    const registro = buildRegistro({
      caseId,
      newmanItems: newmanByCase[caseId] || [],
      extraVerifs: extraByCase[caseId] || [],
      meta,
      extraPayloads:
        caseId === 'CP-AUTH-01'
          ? { bootstrapPath: 'evidencias/CP-AUTH-01/bootstrap_concurrency.json' }
          : caseId === 'CP-AUTH-15'
            ? { rabbitmqEventsPath: 'evidencias/CP-AUTH-15/rabbitmq_events.json' }
            : {},
    });
    writeJson(`${caseId}/registro.json`, registro);
    consolidatedSummary.push({
      casoId: caseId,
      estadoGlobal: registro.estadoGlobal,
      pasan: registro.resumenVerificaciones.pasan,
      fallan: registro.resumenVerificaciones.fallan,
      total: registro.resumenVerificaciones.total,
    });
  }

  writeJson('resumen_casos_i3.json', {
    generatedAtUtc: new Date().toISOString(),
    cases: consolidatedSummary,
  });

  console.log('[QA-I3][SECURITY+CONTRACTS] Resumen de los 18 casos:');
  for (const row of consolidatedSummary) {
    console.log(
      `  - ${row.casoId}: ${row.estadoGlobal} (${row.pasan}/${row.total} verificaciones OK)`,
    );
  }
}

async function main() {
  const phase = parsePhase();
  if (phase === 'bootstrap') {
    await runBootstrapPhase();
  } else if (phase === 'full') {
    await runFullPhase();
  } else {
    throw new Error(`Fase desconocida: ${phase}`);
  }
}

main().catch((err) => {
  console.error('[QA-I3][RUN_SECURITY] Error:', err);
  process.exit(1);
});
