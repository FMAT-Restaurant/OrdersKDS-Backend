import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { ENV } from '../config/env.js';
import { STOCK_RACE_OPTIONS } from '../config/options.js';
import {
  confirmOrder,
  createDraftOrder,
  getOrderDetail,
  replaceOrderItems,
} from '../lib/http.js';
import {
  stockRacePendingTimeout,
  stockRaceRejected,
  stockRaceReserved,
} from '../lib/metrics.js';
import { createSummaryHandler } from '../lib/summary.js';

export const options = STOCK_RACE_OPTIONS;

function prepareRaceDraft(vuIndex, scarceDish) {
  const createRes = createDraftOrder(vuIndex, vuIndex);
  if (createRes.status !== 201) {
    throw new Error(`Fallo creando borrador en setup() para VU ${vuIndex}: ${createRes.status}`);
  }
  const orderId = createRes.json('orderId');
  const itemsRes = replaceOrderItems(vuIndex, orderId, [scarceDish]);
  if (itemsRes.status !== 200) {
    throw new Error(`Fallo agregando item escaso en setup() para orden ${orderId}`);
  }
  return orderId;
}

export function setup() {
  const scarceDish = {
    dishId: ENV.scarceDishId,
    sourceId: ENV.scarceDishId,
    offerId: ENV.scarceOfferId,
    name: 'Platillo Escaso (PT-07)',
    quantity: ENV.scarceQtyPerOrder,
    unitPrice: '165.00',
    modifiers: [],
    notes: 'PT-07 stock race',
  };

  const orderIds = [];
  for (let i = 1; i <= 20; i++) {
    orderIds.push(prepareRaceDraft(i, scarceDish));
  }
  return { orderIds };
}

function pollOrderStatus(vu, orderId) {
  const deadline = Date.now() + ENV.sagaPollTimeoutMs;
  let finalStatus = 'CREATED';

  while (Date.now() < deadline) {
    const detailRes = getOrderDetail(vu, orderId);
    if (detailRes.status === 200) {
      finalStatus = detailRes.json('status');
      if (finalStatus !== 'CREATED') {
        return finalStatus;
      }
    }
    sleep(ENV.sagaPollIntervalSec);
  }
  return finalStatus;
}

function recordRaceOutcome(finalStatus) {
  if (finalStatus === 'RESERVED' || finalStatus === 'IN_PREPARATION') {
    stockRaceReserved.add(1);
    return;
  }
  if (finalStatus === 'REJECTED') {
    stockRaceRejected.add(1);
    return;
  }
  stockRacePendingTimeout.add(1);
}

export default function (data) {
  const vu = exec.vu.idInTest;
  const orderId = data.orderIds[(vu - 1) % data.orderIds.length];

  const confirmRes = confirmOrder(vu, orderId);
  check(confirmRes, { 'confirm accepted 200': (r) => r.status === 200 });

  const finalStatus = pollOrderStatus(vu, orderId);
  recordRaceOutcome(finalStatus);

  check(finalStatus, {
    'saga resolved to RESERVED or REJECTED': (s) =>
      s === 'RESERVED' || s === 'IN_PREPARATION' || s === 'REJECTED',
  });
}

export const handleSummary = createSummaryHandler('stock-race');
