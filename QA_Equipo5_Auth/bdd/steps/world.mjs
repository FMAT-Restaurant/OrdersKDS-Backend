import fs from 'node:fs';
import path from 'node:path';
import { setWorldConstructor, After } from '@cucumber/cucumber';

const BASE_URL = process.env.BASE_URL || 'http://localhost:4000';

class AuthQaWorld {
  constructor() {
    this.baseUrl = BASE_URL;
    const statePath = path.resolve(process.cwd(), 'evidencias/.runtime_state.json');
    this.runtimeState = fs.existsSync(statePath)
      ? JSON.parse(fs.readFileSync(statePath, 'utf8'))
      : null;
    this.adminToken = null;
    this.lastResponse = null;
    this.tempStaff = null;
    this.tempStaffPass = null;
    this.tempAccessToken = null;
    this.activeStaffTokens = null;
    this.firstRefreshToken = null;
    this.createdStaffIds = [];
    this.createdRoleCodes = [];
    this.antiEnumBodies = [];
  }

  async api(urlPath, { method = 'GET', token, body } = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${this.baseUrl}${urlPath}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let json = null;
    try {
      json = JSON.parse(text);
    } catch {
      json = null;
    }
    this.lastResponse = { status: res.status, text, json };
    return this.lastResponse;
  }

  async ensureAdminToken() {
    if (this.adminToken) return this.adminToken;
    const res = await this.api('/api/v1/auth/login', {
      method: 'POST',
      body: {
        identifier: 'admin@fmat.edu.mx',
        password: 'AdminFmat2026!',
        loginType: 'ADMIN',
      },
    });
    if (res.status !== 200 || !res.json?.accessToken) {
      throw new Error(`No se pudo autenticar admin en BDD: ${res.status} ${res.text}`);
    }
    this.adminToken = res.json.accessToken;
    return this.adminToken;
  }
}

setWorldConstructor(AuthQaWorld);

After(async function () {
  if (this.createdStaffIds.length === 0 && this.createdRoleCodes.length === 0) return;
  const token = await this.ensureAdminToken().catch(() => null);
  if (!token) return;
  for (const id of this.createdStaffIds) {
    await this.api(`/api/v1/staff/${id}`, { method: 'DELETE', token }).catch(() => {});
  }
  for (const code of this.createdRoleCodes) {
    await this.api(`/api/v1/roles/${code}`, { method: 'DELETE', token }).catch(() => {});
  }
});
