import assert from 'node:assert/strict';
import { Given, When, Then } from '@cucumber/cucumber';
import jwt from 'jsonwebtoken';

const STAFF_PASS = 'StaffFmat2026!';

When('consulto el estado público de inicialización', async function () {
  await this.api('/api/v1/auth/setup-status');
});

Then('la respuesta HTTP debe ser {int} y el campo {string} debe ser verdadero', function (status, field) {
  assert.equal(this.lastResponse.status, status);
  assert.equal(this.lastResponse.json?.[field], true);
});

When('intento ejecutar un segundo setup-admin con correo {string}', async function (email) {
  await this.api('/api/v1/auth/setup-admin', {
    method: 'POST',
    body: { email, password: 'AdminFmat2026!', firstName: 'Intruso', lastName: 'BDD' },
  });
});

Then('la respuesta HTTP debe ser {int}', function (status) {
  assert.equal(this.lastResponse.status, status);
});

When('inicio sesión {string} con el correo del administrador sembrado y contraseña válida', async function (loginType) {
  await this.api('/api/v1/auth/login', {
    method: 'POST',
    body: { identifier: 'admin@fmat.edu.mx', password: 'AdminFmat2026!', loginType },
  });
});

Then('la respuesta HTTP debe ser {int} y entregar un AccessToken JWT de tipo {string}', async function (status, expectedType) {
  assert.equal(this.lastResponse.status, status);
  const accessToken = this.lastResponse.json?.accessToken;
  assert.ok(accessToken);
  const loginResp = this.lastResponse;
  const me = await this.api('/api/v1/auth/me', { token: accessToken });
  this.lastResponse = loginResp;
  assert.equal(me.status, 200);
  assert.equal(me.json?.userType, expectedType);
});

When('inicio sesión {string} con el StaffID del Mesero sembrado y contraseña válida', async function (loginType) {
  const meseroId = this.runtimeState?.staff?.mesero?.staffId;
  assert.ok(meseroId, 'Debe existir mesero en .runtime_state.json');
  await this.api('/api/v1/auth/login', {
    method: 'POST',
    body: { identifier: meseroId, password: STAFF_PASS, loginType },
  });
});

When('intento iniciar sesión {string} enviando el StaffID {string}', async function (loginType, identifier) {
  const res = await this.api('/api/v1/auth/login', {
    method: 'POST',
    body: { identifier, password: 'AdminFmat2026!', loginType },
  });
  this.antiEnumBodies.push(res);
});

When('intento iniciar sesión {string} enviando el correo {string}', async function (loginType, identifier) {
  const res = await this.api('/api/v1/auth/login', {
    method: 'POST',
    body: { identifier, password: STAFF_PASS, loginType },
  });
  this.antiEnumBodies.push(res);
});

Then('la respuesta HTTP debe ser {int} rechazando el cruce de canal sin acceso', function (status) {
  assert.equal(this.lastResponse.status, status);
  assert.ok(this.lastResponse.json?.message);
  assert.equal(this.lastResponse.json?.accessToken, undefined);
});

When('intento login {string} con credenciales inexistentes {string}', async function (loginType, identifier) {
  this.antiEnumBodies = [];
  this.antiEnumBodies.push(
    await this.api('/api/v1/auth/login', {
      method: 'POST',
      body: { identifier, password: STAFF_PASS, loginType },
    }),
  );
  this.antiEnumBodies.push(
    await this.api('/api/v1/auth/login', {
      method: 'POST',
      body: {
        identifier: this.runtimeState.staff.mesero.staffId,
        password: 'WrongPassword99!',
        loginType,
      },
    }),
  );
  this.antiEnumBodies.push(
    await this.api('/api/v1/auth/login', {
      method: 'POST',
      body: {
        identifier: this.runtimeState.staff.inactiveOne.staffId,
        password: STAFF_PASS,
        loginType,
      },
    }),
  );
});

Then('el rechazo {int} debe coincidir exactamente con el de contraseña incorrecta y cuenta inactiva', function (status) {
  for (const res of this.antiEnumBodies) {
    assert.equal(res.status, status);
    assert.equal(res.json?.message, this.antiEnumBodies[0].json?.message);
    assert.equal(res.json?.error, this.antiEnumBodies[0].json?.error);
  }
});

Given('que el administrador crea un colaborador temporal BDD con rol {string}', async function (roleCode) {
  const adminToken = await this.ensureAdminToken();
  const res = await this.api('/api/v1/staff', {
    method: 'POST',
    token: adminToken,
    body: { firstName: 'TempBdd', lastName: 'User', roles: [roleCode] },
  });
  assert.equal(res.status, 201);
  this.tempStaff = res.json.user;
  this.tempStaffPass = res.json.temporaryPassword;
  this.createdStaffIds.push(this.tempStaff.id);
});

When('ese colaborador inicia sesión por primera vez con su contraseña temporal', async function () {
  const res = await this.api('/api/v1/auth/login', {
    method: 'POST',
    body: {
      identifier: this.tempStaff.staffId,
      password: this.tempStaffPass,
      loginType: 'STAFF',
    },
  });
  assert.equal(res.status, 200);
  this.tempAccessToken = res.json.accessToken;
});

Then('el claim JWT {string} debe ser verdadero', function (claimName) {
  const decoded = jwt.decode(this.tempAccessToken);
  assert.equal(decoded?.[claimName], true);
});

When('ese colaborador temporal consulta {string}', async function (routeDesc) {
  const [, route] = routeDesc.split(' ');
  await this.api(route, { token: this.tempAccessToken });
});

Then('debe recibir código HTTP {int} con código de error {string}', function (status, code) {
  assert.equal(this.lastResponse.status, status);
  assert.equal(this.lastResponse.json?.code, code);
});

When('ese colaborador intenta establecer la contraseña límite inválida {string} de 7 caracteres', async function (newPass) {
  await this.api('/api/v1/auth/change-initial-password', {
    method: 'POST',
    token: this.tempAccessToken,
    body: { currentPassword: this.tempStaffPass, newPassword: newPass },
  });
});

When('ese colaborador cambia su contraseña inicial por una válida compleja de 8 caracteres {string}', async function (newPass) {
  const res = await this.api('/api/v1/auth/change-initial-password', {
    method: 'POST',
    token: this.tempAccessToken,
    body: { currentPassword: this.tempStaffPass, newPassword: newPass },
  });
  assert.equal(res.status, 200);
  this.activeStaffTokens = res.json;
  this.lastResponse = res;
});

Then('la respuesta HTTP debe ser {int} y su estado en {string} debe pasar a {string} conservando el mismo staffId', async function (status, mePath, expectedStatus) {
  assert.equal(this.lastResponse.status, status);
  const me = await this.api(mePath, { token: this.activeStaffTokens.accessToken });
  assert.equal(me.status, 200);
  assert.equal(me.json?.passwordStatus, expectedStatus);
  assert.equal(me.json?.staffId, this.tempStaff.staffId);
});

Given('que estoy autenticado como administrador principal', async function () {
  await this.ensureAdminToken();
});

When('intento crear un rol dinámico con permiso wildcard {string}', async function (perm) {
  await this.api('/api/v1/roles', {
    method: 'POST',
    token: this.adminToken,
    body: { code: 'ROL_BAD_BDD', name: 'Rol Bad', permissions: [perm] },
  });
});

When('intento crear un colaborador con roles vacíos', async function () {
  await this.api('/api/v1/staff', {
    method: 'POST',
    token: this.adminToken,
    body: { firstName: 'Sin', lastName: 'Rol', roles: [] },
  });
});

When('intento crear un colaborador asignando el rol reservado {string}', async function (roleCode) {
  await this.api('/api/v1/staff', {
    method: 'POST',
    token: this.adminToken,
    body: { firstName: 'Escala', lastName: 'Admin', roles: [roleCode] },
  });
});

When('creo el rol dinámico {string} con permiso {string} y un colaborador asignado', async function (roleCode, perm) {
  const r = await this.api('/api/v1/roles', {
    method: 'POST',
    token: this.adminToken,
    body: { code: roleCode, name: 'Eventos BDD', permissions: [perm] },
  });
  assert.equal(r.status, 201);
  this.createdRoleCodes.push(roleCode);

  const s = await this.api('/api/v1/staff', {
    method: 'POST',
    token: this.adminToken,
    body: { firstName: 'Bdd', lastName: 'Eventos', roles: [roleCode] },
  });
  if (s.status === 201 && s.json?.user?.id) {
    this.createdStaffIds.push(s.json.user.id);
  }
});

Then('la respuesta HTTP debe ser {int} y el staffId generado debe iniciar con {string} cumpliendo {string}', function (status, prefix, pattern) {
  assert.equal(this.lastResponse.status, status);
  const sid = this.lastResponse.json?.user?.staffId;
  assert.ok(sid && sid.startsWith(prefix), `Se esperaba prefijo ${prefix}, recibido ${sid}`);
  assert.match(sid, new RegExp(pattern));
});

When('un colaborador {string} autenticado consulta {string}', async function (_role, routeDesc) {
  const [, route] = routeDesc.split(' ');
  const login = await this.api('/api/v1/auth/login', {
    method: 'POST',
    body: {
      identifier: this.runtimeState.staff.mesero.staffId,
      password: STAFF_PASS,
      loginType: 'STAFF',
    },
  });
  assert.equal(login.status, 200);
  await this.api(route, { token: login.json.accessToken });
});

Then('la respuesta HTTP debe ser {int} sin exponer el campo {string}', function (status, forbiddenKey) {
  assert.equal(this.lastResponse.status, status);
  assert.ok(!JSON.stringify(this.lastResponse.json).includes(`"${forbiddenKey}"`));
});

Given('que el administrador crea y activa un colaborador temporal BDD de sesión corta', async function () {
  const adminToken = await this.ensureAdminToken();
  const create = await this.api('/api/v1/staff', {
    method: 'POST',
    token: adminToken,
    body: { firstName: 'Session', lastName: 'ShortBdd', roles: ['MESERO'] },
  });
  assert.equal(create.status, 201);
  this.tempStaff = create.json.user;
  this.createdStaffIds.push(this.tempStaff.id);

  const loginTemp = await this.api('/api/v1/auth/login', {
    method: 'POST',
    body: {
      identifier: this.tempStaff.staffId,
      password: create.json.temporaryPassword,
      loginType: 'STAFF',
    },
  });
  assert.equal(loginTemp.status, 200);

  const changed = await this.api('/api/v1/auth/change-initial-password', {
    method: 'POST',
    token: loginTemp.json.accessToken,
    body: { currentPassword: create.json.temporaryPassword, newPassword: STAFF_PASS },
  });
  assert.equal(changed.status, 200);
  this.activeStaffTokens = changed.json;
  this.firstRefreshToken = changed.json.refreshToken;
});

When('el colaborador activo rota su sesión con {string}', async function (endpoint) {
  await this.api(endpoint, {
    method: 'POST',
    body: { refreshToken: this.firstRefreshToken },
  });
});

Then('la rotación debe devolver HTTP {int} con un nuevo par de tokens distinto al anterior', function (status) {
  assert.equal(this.lastResponse.status, status);
  assert.ok(this.lastResponse.json?.accessToken);
  assert.ok(this.lastResponse.json?.refreshToken);
  assert.notEqual(this.lastResponse.json.refreshToken, this.firstRefreshToken);
  this.activeStaffTokens = this.lastResponse.json;
});

When('un atacante reutiliza el RefreshToken previo ya rotado', async function () {
  await this.api('/api/v1/auth/refresh', {
    method: 'POST',
    body: { refreshToken: this.firstRefreshToken },
  });
});

Then('el replay attack debe responder HTTP {int}', function (status) {
  assert.equal(this.lastResponse.status, status);
});

When('el administrador desactiva al colaborador vía {string}', async function (routeTemplate) {
  const parts = routeTemplate.trim().split(/\s+/);
  const method = parts.length > 1 ? parts[0] : 'PATCH';
  const rawRoute = parts.length > 1 ? parts[1] : parts[0];
  const route = rawRoute.replace(':id', this.tempStaff.id);
  const res = await this.api(route, {
    method,
    token: this.adminToken,
    body: { isActive: false },
  });
  assert.equal(res.status, 200);
});

Then('el AccessToken previamente emitido debe responder HTTP {int} en {string}', async function (status, route) {
  const me = await this.api(route, { token: this.activeStaffTokens.accessToken });
  assert.equal(me.status, status);
});
