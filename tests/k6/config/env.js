/**
 * Entorno y endpoints base para la suite k6 de Orders & KDS Backend.
 * Soporta ejecucion directa (:8000) o a traves de Kong (:8082 orders / :8086 kitchen).
 */
function trimTrailingSlash(value) {
  let clean = value;
  while (clean.endsWith('/')) {
    clean = clean.slice(0, -1);
  }
  return clean;
}

function httpToWs(url) {
  const lower = url.toLowerCase();
  if (lower.startsWith('https://')) {
    return `wss://${url.slice(8)}`;
  }
  if (lower.startsWith('http://')) {
    return `ws://${url.slice(7)}`;
  }
  return url;
}

const BASE_HTTP = trimTrailingSlash(__ENV.BASE_URL || 'http://127.0.0.1:8000');
const BASE_WS = trimTrailingSlash(__ENV.WS_BASE_URL || httpToWs(BASE_HTTP));

export const ENV = {
  baseHttp: BASE_HTTP,
  baseWs: BASE_WS,
  ordersBaseUrl: trimTrailingSlash(__ENV.K6_ORDERS_BASE_URL || `${BASE_HTTP}/api/v1/orders`),
  kitchenBaseUrl: trimTrailingSlash(__ENV.K6_KITCHEN_BASE_URL || `${BASE_HTTP}/api/v1/kitchen`),
  ordersWsUrl: trimTrailingSlash(__ENV.K6_ORDERS_WS_URL || `${BASE_WS}/api/v1/orders/ws/waiter`),
  kitchenWsUrl: trimTrailingSlash(__ENV.K6_KITCHEN_WS_URL || `${BASE_WS}/api/v1/kitchen/ws/kds`),

  authMode: (__ENV.K6_AUTH_MODE || 'jwt').toLowerCase(),
  waiterToken: __ENV.K6_WAITER_TOKEN || '',
  kitchenToken: __ENV.K6_KITCHEN_TOKEN || '',
  adminToken: __ENV.K6_ADMIN_TOKEN || '',
  devSigningMaterial: __ENV.K6_DEV_JWT_SIGNING_MATERIAL || '',

  sagaPollTimeoutMs: Number(__ENV.K6_SAGA_TIMEOUT_MS || 20000),
  sagaPollIntervalSec: Number(__ENV.K6_SAGA_POLL_INTERVAL_SEC || 0.5),
  wsHoldMs: Number(__ENV.K6_WS_HOLD_MS || 60000),
  summaryDir: trimTrailingSlash(__ENV.K6_SUMMARY_DIR || 'tests/k6/out'),

  scarceDishId: __ENV.K6_SCARCE_DISH_ID || 'PLATE-BURGER-01',
  scarceOfferId: __ENV.K6_SCARCE_OFFER_ID || 'OFFER-BURGER-01',
  scarceQtyPerOrder: Number(__ENV.K6_SCARCE_QTY_PER_ORDER || 1),

  smokeVus: Number(__ENV.K6_SMOKE_VUS || 10),
  smokeDuration: __ENV.K6_SMOKE_DURATION || '30s',
  soakVus: Number(__ENV.K6_SOAK_VUS || 75),
  soakDuration: __ENV.K6_SOAK_DURATION || '45m',
  wsDuration: __ENV.K6_WS_DURATION || '2m',
  wsMutatorDuration: __ENV.K6_WS_MUTATOR_DURATION || '90s',
};
