import { check, sleep } from 'k6';
import {
  confirmOrder,
  createDraftOrder,
  getKdsQueue,
  getOrderDetail,
  listOrders,
  markItemReady,
  replaceOrderItems,
  startPreparation,
} from './http.js';
import { KDS_STATIONS, pickOrderItems } from './setup.js';

/**
 * Generador pseudoaleatorio determinista de 32 bits (evita Math.random / S2245).
 */
export function deterministicRoll(vu, iteration, salt = 0) {
  let state = (Math.imul(vu + 1, 0x9e3779b1) ^ Math.imul(iteration + 1, 0x85ebca77) ^ salt) >>> 0;
  state ^= state >>> 15;
  state = Math.imul(state, 0x2c1b3c6d) >>> 0;
  state ^= state >>> 12;
  state = Math.imul(state, 0x297a2d39) >>> 0;
  state ^= state >>> 15;
  return state / 0x100000000;
}

export function jitterSleep(baseSec, spanSec, vu, iteration) {
  const offset = deterministicRoll(vu, iteration, 7919) * spanSec;
  sleep(baseSec + offset);
}

export function runReadMix(vu, iteration, config = {}) {
  const kdsRatio = config.kdsRatio ?? 0.64;
  const stations = config.stations ?? KDS_STATIONS;
  const prefix = config.labelPrefix ?? 'read';
  const subRoll = deterministicRoll(vu, iteration, 101);

  if (subRoll < kdsRatio) {
    const station = stations[(vu + iteration) % stations.length];
    const res = getKdsQueue(vu, station);
    check(res, { [`${prefix} GET /kds/queue 200`]: (r) => r.status === 200 });
    return res;
  }

  const res = listOrders(vu);
  check(res, { [`${prefix} GET /orders 200`]: (r) => r.status === 200 });
  return res;
}

export function runOrderWriteCycle(vu, iteration, config = {}) {
  const tableModulo = config.tableModulo ?? 50;
  const prefix = config.labelPrefix ?? 'write';
  const shouldConfirm = config.confirmOrderFlag ?? true;
  const includeDetail = config.includeDetail ?? false;

  const tableNumber = ((vu + iteration) % tableModulo) + 1;
  const createRes = createDraftOrder(vu, tableNumber);
  const created = check(createRes, { [`${prefix} POST /orders 201`]: (r) => r.status === 201 });
  if (!created) {
    return null;
  }

  const orderId = createRes.json('orderId');
  const itemsRes = replaceOrderItems(vu, orderId, pickOrderItems(vu));
  const itemsUpdated = check(itemsRes, {
    [`${prefix} PATCH /orders/{id}/items 200`]: (r) => r.status === 200,
  });
  if (!itemsUpdated) {
    return orderId;
  }

  if (includeDetail) {
    const detailRes = getOrderDetail(vu, orderId);
    check(detailRes, { [`${prefix} GET /orders/{id} 200`]: (r) => r.status === 200 });
  }

  if (shouldConfirm) {
    const confirmRes = confirmOrder(vu, orderId);
    check(confirmRes, { [`${prefix} POST /orders/{id}/confirm 200`]: (r) => r.status === 200 });
  }
  return orderId;
}

export function runKitchenTransition(vu, iteration, data) {
  const reserved = data?.reservedIds || [];
  const inPrep = data?.inPrepTargets || [];
  const preferItemReady = deterministicRoll(vu, iteration, 307) < 0.7;

  if (inPrep.length > 0 && preferItemReady) {
    const target = inPrep[iteration % inPrep.length];
    const res = markItemReady(vu, target.orderId, target.itemId, [200, 409]);
    check(res, { 'item ready handled cleanly': (r) => r.status === 200 || r.status === 409 });
    return;
  }

  if (reserved.length > 0) {
    const orderId = reserved[iteration % reserved.length];
    const res = startPreparation(vu, orderId, [200, 409]);
    check(res, { 'start-prep handled cleanly': (r) => r.status === 200 || r.status === 409 });
    return;
  }

  const fallbackRes = getKdsQueue(vu);
  check(fallbackRes, { 'fallback GET /kds/queue 200': (r) => r.status === 200 });
}

export function runWeightedTraffic(vu, iteration, config) {
  const readCutoff = config.readWeight ?? 0.70;
  const writeCutoff = readCutoff + (config.writeWeight ?? 0.20);
  const roll = deterministicRoll(vu, iteration, 17);

  if (roll < readCutoff) {
    runReadMix(vu, iteration, config);
    return;
  }

  if (roll < writeCutoff) {
    const confirmNow =
      typeof config.confirmWhen === 'function' ? config.confirmWhen(iteration) : true;
    runOrderWriteCycle(vu, iteration, {
      tableModulo: config.tableModulo,
      includeDetail: config.includeDetail,
      confirmOrderFlag: confirmNow,
      labelPrefix: config.labelPrefix,
    });
    return;
  }

  runKitchenTransition(vu, iteration, config.kitchenData);
}
