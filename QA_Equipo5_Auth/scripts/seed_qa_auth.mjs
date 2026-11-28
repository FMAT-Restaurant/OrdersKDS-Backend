import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const BASE_URL = process.env.BASE_URL || 'http://localhost:4000';
export const QA_ADMIN_EMAIL = 'admin@fmat.edu.mx';
export const QA_ADMIN_PASS = 'AdminFmat2026!';
export const QA_ACTIVE_STAFF_PASS = 'StaffFmat2026!';

async function api(urlPath, { method = 'GET', token, body } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${BASE_URL}${urlPath}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = text;
  }
  return { status: res.status, data };
}

async function ensureAdminLogin() {
  const statusRes = await api('/api/v1/auth/setup-status');
  if (statusRes.status === 200 && statusRes.data?.configured === false) {
    const setupRes = await api('/api/v1/auth/setup-admin', {
      method: 'POST',
      body: {
        email: QA_ADMIN_EMAIL,
        password: QA_ADMIN_PASS,
        firstName: 'Admin',
        lastName: 'Principal',
      },
    });
    if (setupRes.status !== 201) {
      throw new Error(`setup-admin falló: ${setupRes.status} ${JSON.stringify(setupRes.data)}`);
    }
  }

  const loginRes = await api('/api/v1/auth/login', {
    method: 'POST',
    body: {
      identifier: QA_ADMIN_EMAIL,
      password: QA_ADMIN_PASS,
      loginType: 'ADMIN',
    },
  });
  if (loginRes.status !== 200 || !loginRes.data?.accessToken) {
    throw new Error(`Admin login falló: ${loginRes.status} ${JSON.stringify(loginRes.data)}`);
  }
  return loginRes.data;
}

async function ensureCustomRole(adminToken) {
  const code = 'ROL_ENCARGADO_OP';
  const check = await api(`/api/v1/roles/${code}`, { token: adminToken });
  if (check.status === 200) return check.data;

  const create = await api('/api/v1/roles', {
    method: 'POST',
    token: adminToken,
    body: {
      code,
      name: 'Encargado Operativo',
      description: 'Rol personalizado PBAC para pruebas de Iteracion 3 (prefijo E)',
      permissions: ['inventory:items:read', 'orders:orders:view', 'staff:view'],
    },
  });
  if (create.status !== 201) {
    throw new Error(`createRole ${code} falló: ${create.status} ${JSON.stringify(create.data)}`);
  }
  return create.data;
}

async function createAndMaybeActivateStaff(adminToken, spec, existingByName) {
  const key = `${spec.firstName}|${spec.lastName}`;
  let existing = existingByName.get(key);
  let temporaryPassword = null;

  if (!existing) {
    const created = await api('/api/v1/staff', {
      method: 'POST',
      token: adminToken,
      body: {
        firstName: spec.firstName,
        lastName: spec.lastName,
        phone: spec.phone || '9990001122',
        roles: spec.roles,
      },
    });
    if (created.status !== 201) {
      throw new Error(`createStaff ${key} falló: ${created.status} ${JSON.stringify(created.data)}`);
    }
    existing = created.data.user;
    temporaryPassword = created.data.temporaryPassword;
  }

  if (spec.activate && temporaryPassword) {
    const loginTemp = await api('/api/v1/auth/login', {
      method: 'POST',
      body: {
        identifier: existing.staffId,
        password: temporaryPassword,
        loginType: 'STAFF',
      },
    });
    if (loginTemp.status !== 200) {
      throw new Error(`loginTemp ${existing.staffId} falló: ${loginTemp.status}`);
    }
    const chg = await api('/api/v1/auth/change-initial-password', {
      method: 'POST',
      token: loginTemp.data.accessToken,
      body: {
        currentPassword: temporaryPassword,
        newPassword: QA_ACTIVE_STAFF_PASS,
      },
    });
    if (chg.status !== 200) {
      throw new Error(`change-initial-password ${existing.staffId} falló: ${chg.status} ${JSON.stringify(chg.data)}`);
    }
  }

  if (spec.deactivate) {
    const st = await api(`/api/v1/staff/${existing.id}/status`, {
      method: 'PATCH',
      token: adminToken,
      body: { isActive: false },
    });
    if (st.status !== 200) {
      throw new Error(`deactivate ${existing.staffId} falló: ${st.status}`);
    }
  }

  return {
    id: existing.id,
    staffId: existing.staffId,
    firstName: spec.firstName,
    lastName: spec.lastName,
    roles: spec.roles,
    expectedPrefix: spec.expectedPrefix,
    passwordStatus: spec.activate ? 'ACTIVE' : 'TEMPORARY',
    isActive: !spec.deactivate,
    initialTemporaryPassword: spec.activate ? undefined : temporaryPassword,
  };
}

async function main() {
  const adminSession = await ensureAdminLogin();
  const adminToken = adminSession.accessToken;
  const customRole = await ensureCustomRole(adminToken);

  const currentStaffRes = await api('/api/v1/staff', { token: adminToken });
  const existingByName = new Map();
  if (Array.isArray(currentStaffRes.data)) {
    for (const s of currentStaffRes.data) {
      existingByName.set(`${s.firstName}|${s.lastName}`, s);
    }
  }

  const specs = [
    // Intento de orden mínimo legal para obtener E000001 inmediatamente después de ADM000001:
    // 1 rol dinámico personalizado PBAC (prefijo E por ROL_ENCARGADO_OP)
    { key: 'customPbac', firstName: 'Elena', lastName: 'Encargada', roles: ['ROL_ENCARGADO_OP'], expectedPrefix: 'E', activate: true },
    // 6 colaboradores activos cubriendo los roles base (H/A/G/M/C/J)
    { key: 'host', firstName: 'Sofia', lastName: 'Host', roles: ['HOST'], expectedPrefix: 'H', activate: true },
    { key: 'almacenista', firstName: 'Diego', lastName: 'Almacen', roles: ['ALMACENISTA'], expectedPrefix: 'A', activate: true },
    { key: 'gestorMenu', firstName: 'Valeria', lastName: 'Menu', roles: ['GESTOR_MENU'], expectedPrefix: 'G', activate: true },
    { key: 'mesero', firstName: 'Mateo', lastName: 'Mesero', roles: ['MESERO'], expectedPrefix: 'M', activate: true },
    { key: 'chef', firstName: 'Camila', lastName: 'Chef', roles: ['CHEF_MASTER'], expectedPrefix: 'C', activate: true },
    { key: 'cajero', firstName: 'Lucas', lastName: 'Cajero', roles: ['CAJERO'], expectedPrefix: 'J', activate: true },
    // 2 cuentas en estado TEMPORARY (mustChangePassword: true)
    { key: 'tempOne', firstName: 'Tomas', lastName: 'TemporalUno', roles: ['MESERO'], expectedPrefix: 'M', activate: false },
    { key: 'tempTwo', firstName: 'Nadia', lastName: 'TemporalDos', roles: ['CHEF_MASTER'], expectedPrefix: 'C', activate: false },
    // 1 cuenta desactivada (isActive: false)
    { key: 'inactiveOne', firstName: 'Bruno', lastName: 'Inactivo', roles: ['MESERO'], expectedPrefix: 'M', activate: true, deactivate: true },
  ];

  const seeded = {};
  for (const spec of specs) {
    seeded[spec.key] = await createAndMaybeActivateStaff(adminToken, spec, existingByName);
  }

  const customStaffId = seeded.customPbac.staffId;
  const summary = {
    generatedAt: new Date().toISOString(),
    baseUrl: BASE_URL,
    staffIdSequenceObservation: {
      attemptedFirstOrderFor: 'E000001',
      actualAssignedStaffId: customStaffId,
      matchesE000001: customStaffId === 'E000001',
      reason:
        customStaffId === 'E000001'
          ? 'Secuencia por prefijo generó E000001.'
          : `StaffService.generateUniqueStaffId usa conteo global (count + 1) sobre staff_profiles; como ADM000001 ocupa la fila 1 tras setup-admin, el primer colaborador creado vía API recibe ${customStaffId} (prefijo E válido).`,
    },
    admin: {
      id: adminSession.user.id,
      staffId: adminSession.user.staffId,
      email: adminSession.user.email,
      roles: adminSession.user.roles,
    },
    customRole: {
      code: customRole.code,
      name: customRole.name,
      permissions: customRole.permissions,
    },
    staff: seeded,
    counts: {
      admins: 1,
      activeBaseRoles: 6,
      customPbac: 1,
      temporary: 2,
      inactive: 1,
      totalUsers: 11,
    },
  };

  fs.mkdirSync(path.resolve('evidencias'), { recursive: true });
  fs.writeFileSync(
    path.resolve('evidencias/seed_summary.json'),
    JSON.stringify(
      {
        ...summary,
        // No exponer contraseñas temporales activas fuera del archivo runtime gitignorado
        staff: Object.fromEntries(
          Object.entries(seeded).map(([k, v]) => [k, { ...v, initialTemporaryPassword: v.initialTemporaryPassword ? '[SEEDED_TEMP_REDACTADA]' : undefined }]),
        ),
      },
      null,
      2,
    ),
  );

  // Estado runtime (gitignorado) con contraseñas temporales reales para Newman/BDD/Security
  fs.writeFileSync(path.resolve('evidencias/.runtime_state.json'), JSON.stringify(summary, null, 2));

  console.log('[SEED] Datos semilla deterministas listos:', summary.counts);
  console.log('[SEED] Prefijos asignados:', Object.entries(seeded).map(([k, v]) => `${k}=${v.staffId}`).join(', '));
}

const isMain =
  Boolean(process.argv[1]) &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  main().catch((err) => {
    console.error('[SEED ERROR]', err);
    process.exit(1);
  });
}
