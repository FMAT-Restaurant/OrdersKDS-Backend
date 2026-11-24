import http from 'k6/http';
import { ENV } from '../config/env.js';
import { authHeaders } from './auth.js';
import { kdsReadDuration, orderWriteDuration, trackServerError } from './metrics.js';

function recordWriteMetrics(res) {
  orderWriteDuration.add(res.timings.duration);
  trackServerError(res);
  return res;
}

export function checkHealth() {
  const r1 = http.get(`${ENV.ordersBaseUrl}/health`, {
    tags: { name: 'GET /orders/health' },
  });
  const r2 = http.get(`${ENV.kitchenBaseUrl}/health`, {
    tags: { name: 'GET /kitchen/health' },
  });
  trackServerError(r1);
  trackServerError(r2);
  return { r1, r2 };
}

export function listOrders(vuNumber, query = '') {
  const suffix = query ? `?${query}` : '';
  const res = http.get(`${ENV.ordersBaseUrl}/orders${suffix}`, {
    headers: authHeaders('waiter', vuNumber),
    tags: { name: 'GET /orders' },
  });
  trackServerError(res);
  return res;
}

export function getKdsQueue(vuNumber, station = '') {
  const qs = station ? `?view=active&station=${encodeURIComponent(station)}` : '?view=active';
  const res = http.get(`${ENV.kitchenBaseUrl}/kds/queue${qs}`, {
    headers: authHeaders('kitchen', vuNumber),
    tags: { name: station ? 'GET /kds/queue?station' : 'GET /kds/queue' },
  });
  kdsReadDuration.add(res.timings.duration);
  trackServerError(res);
  return res;
}

export function createDraftOrder(vuNumber, tableNumber) {
  const payload = JSON.stringify({
    tableNumber,
    isTakeaway: false,
  });
  const res = http.post(`${ENV.ordersBaseUrl}/orders`, payload, {
    headers: authHeaders('waiter', vuNumber),
    responseType: 'text',
    tags: { name: 'POST /orders' },
  });
  return recordWriteMetrics(res);
}

export function replaceOrderItems(vuNumber, orderId, items) {
  const res = http.patch(
    `${ENV.ordersBaseUrl}/orders/${orderId}/items`,
    JSON.stringify({ items }),
    {
      headers: authHeaders('waiter', vuNumber),
      tags: { name: 'PATCH /orders/{orderId}/items' },
    }
  );
  return recordWriteMetrics(res);
}

export function confirmOrder(vuNumber, orderId, expectedStatuses = [200]) {
  const res = http.post(`${ENV.ordersBaseUrl}/orders/${orderId}/confirm`, null, {
    headers: authHeaders('waiter', vuNumber),
    responseCallback: http.expectedStatuses(...expectedStatuses),
    tags: { name: 'POST /orders/{orderId}/confirm' },
  });
  return recordWriteMetrics(res);
}

export function getOrderDetail(vuNumber, orderId) {
  const res = http.get(`${ENV.ordersBaseUrl}/orders/${orderId}`, {
    headers: authHeaders('waiter', vuNumber),
    responseType: 'text',
    tags: { name: 'GET /orders/{orderId}' },
  });
  trackServerError(res);
  return res;
}

export function startPreparation(vuNumber, orderId, expectedStatuses = [200, 409]) {
  const res = http.patch(`${ENV.kitchenBaseUrl}/orders/${orderId}/start-preparation`, null, {
    headers: authHeaders('kitchen', vuNumber),
    responseCallback: http.expectedStatuses(...expectedStatuses),
    tags: { name: 'PATCH /orders/{orderId}/start-preparation' },
  });
  trackServerError(res);
  return res;
}

export function markItemReady(vuNumber, orderId, itemId, expectedStatuses = [200, 409]) {
  const res = http.patch(
    `${ENV.kitchenBaseUrl}/orders/${orderId}/items/${itemId}/ready`,
    null,
    {
      headers: authHeaders('kitchen', vuNumber),
      responseCallback: http.expectedStatuses(...expectedStatuses),
      tags: { name: 'PATCH /orders/{orderId}/items/{itemId}/ready' },
    }
  );
  trackServerError(res);
  return res;
}
