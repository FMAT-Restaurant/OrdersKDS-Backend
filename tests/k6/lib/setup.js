import http from 'k6/http';
import { SharedArray } from 'k6/data';
import { ENV } from '../config/env.js';
import { authHeaders } from './auth.js';

/**
 * Platillos base alineados con scripts/seed_demo.py e Inventory TEST_DATA.md.
 */
export const CATALOG_ITEMS = new SharedArray('catalog_items', () => [
  {
    dishId: 'PLATE-BURGER-01',
    sourceId: 'PLATE-BURGER-01',
    offerId: 'OFFER-BURGER-01',
    name: 'Hamburguesa clasica',
    quantity: 1,
    unitPrice: '165.00',
    modifiers: ['Sin cebolla'],
    notes: 'Prueba carga',
  },
  {
    dishId: 'PLATE-QUES-01',
    sourceId: 'PLATE-QUES-01',
    offerId: 'OFFER-QUES-01',
    name: 'Quesadilla de Oaxaca con jamon',
    quantity: 1,
    unitPrice: '115.00',
    modifiers: [],
    notes: null,
  },
  {
    dishId: 'PLATE-CESAR-01',
    sourceId: 'PLATE-CESAR-01',
    offerId: 'OFFER-CESAR-01',
    name: 'Ensalada Cesar con pollo',
    quantity: 1,
    unitPrice: '135.00',
    modifiers: ['Aderezo aparte'],
    notes: null,
  },
  {
    dishId: 'PLATE-SIM-0001',
    sourceId: 'PLATE-SIM-0001',
    offerId: 'OFFER-SIM-0001',
    name: 'Platillo del Simulador',
    quantity: 1,
    unitPrice: '120.00',
    modifiers: [],
    notes: null,
  },
]);

export const KDS_STATIONS = ['parrilla', 'fria', 'plancha', ''];

export const KDS_WS_CHANNELS = [
  'kds:all',
  'kds:station:parrilla',
  'kds:station:fria',
  'kds:station:plancha',
  'kds:station:horno',
];

export function pickOrderItems(vuNumber) {
  const first = CATALOG_ITEMS[vuNumber % CATALOG_ITEMS.length];
  const second = CATALOG_ITEMS[(vuNumber + 1) % CATALOG_ITEMS.length];
  return [first, second];
}

function parseOrderList(body) {
  try {
    const parsed = JSON.parse(body);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function collectPendingItemTarget(order) {
  if (!Array.isArray(order.items)) {
    return null;
  }
  const pendingItem = order.items.find((it) => it.status === 'PENDING');
  if (!pendingItem) {
    return null;
  }
  return {
    orderId: order.orderId || order.publicId,
    itemId: pendingItem.itemId || pendingItem.publicId || pendingItem.id,
  };
}

/**
 * Descubre ordenes reales en RESERVED o IN_PREPARATION para evitar HTTP 409 en KDS.
 */
export function discoverKdsActiveOrders() {
  const res = http.get(`${ENV.kitchenBaseUrl}/kds/queue?view=active`, {
    headers: authHeaders('kitchen', 1),
    responseType: 'text',
    tags: { name: 'GET /kds/queue (setup)' },
  });

  if (res.status !== 200) {
    return { reservedIds: [], inPrepTargets: [] };
  }

  const orders = parseOrderList(res.body);
  const reservedIds = [];
  const inPrepTargets = [];

  for (const order of orders) {
    if (order.status === 'RESERVED') {
      reservedIds.push(order.orderId || order.publicId);
    } else if (order.status === 'IN_PREPARATION') {
      const target = collectPendingItemTarget(order);
      if (target) {
        inPrepTargets.push(target);
      }
    }
  }

  return { reservedIds, inPrepTargets };
}
