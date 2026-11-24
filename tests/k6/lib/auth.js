import crypto from 'k6/crypto';
import encoding from 'k6/encoding';
import { SharedArray } from 'k6/data';
import { ENV } from '../config/env.js';

/**
 * Pools inmutables compartidos de actores alineados con app/domain/access.py.
 */
export const WAITERS = new SharedArray('waiters', () =>
  Array.from({ length: 40 }, (_, i) => ({
    userId: `00000000-0000-4000-8000-${String(i + 1).padStart(12, '0')}`,
    staffId: `staff-waiter-${i + 1}`,
    role: 'waiter',
    permissions: [
      'orders:orders:view',
      'orders:orders:create',
      'orders:orders:update',
      'orders:orders:confirm',
      'orders:orders:cancel',
      'orders:orders:request_bill',
    ],
  }))
);

export const KITCHEN_USERS = new SharedArray('kitchen_users', () =>
  Array.from({ length: 20 }, (_, i) => ({
    userId: `00000000-0000-4000-9000-${String(i + 1).padStart(12, '0')}`,
    staffId: `staff-kitchen-${i + 1}`,
    role: i % 4 === 0 ? 'chef' : 'kitchen',
    permissions: [
      'kitchen:queues:view',
      'kitchen:orders:start_preparation',
      'kitchen:items:ready',
      'kitchen:orders:ready',
      'orders:orders:view',
    ],
  }))
);

function resolveSigningMaterial() {
  if (ENV.devSigningMaterial) {
    return ENV.devSigningMaterial;
  }
  return ['k6', 'local', 'orders', 'bench'].join('-');
}

function buildDevJwt(actor) {
  const header = encoding.b64encode(JSON.stringify({ alg: 'HS256', typ: 'JWT' }), 'rawurl');
  const nowSec = Math.floor(Date.now() / 1000);
  const payload = encoding.b64encode(
    JSON.stringify({
      sub: actor.userId,
      user_id: actor.userId,
      staffId: actor.staffId,
      role: actor.role,
      roles: [actor.role],
      permissions: actor.permissions,
      exp: nowSec + 7200,
    }),
    'rawurl'
  );
  const signingInput = `${header}.${payload}`;
  const hasher = crypto.createHMAC('sha256', resolveSigningMaterial());
  hasher.update(signingInput);
  const sig = hasher.digest('base64').replaceAll('/', '_').replaceAll('+', '-').replaceAll('=', '');
  return `${signingInput}.${sig}`;
}

export function getActor(kind, vuNumber) {
  const pool = kind === 'kitchen' ? KITCHEN_USERS : WAITERS;
  const index = Math.max(0, vuNumber - 1) % pool.length;
  return pool[index];
}

export function getTokenFor(kind, vuNumber) {
  if (kind === 'kitchen' && ENV.kitchenToken) {
    return ENV.kitchenToken;
  }
  if (kind === 'waiter' && ENV.waiterToken) {
    return ENV.waiterToken;
  }
  if (kind === 'admin' && ENV.adminToken) {
    return ENV.adminToken;
  }
  return buildDevJwt(getActor(kind, vuNumber));
}

export function authHeaders(kind, vuNumber) {
  const actor = getActor(kind, vuNumber);
  if (ENV.authMode === 'gateway') {
    return {
      'Content-Type': 'application/json',
      'x-user-id': actor.userId,
      'x-staff-id': actor.staffId,
      'x-user-roles': actor.role,
      'x-user-permissions': actor.permissions.join(','),
    };
  }
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${getTokenFor(kind, vuNumber)}`,
  };
}
