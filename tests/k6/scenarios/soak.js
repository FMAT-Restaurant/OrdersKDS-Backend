import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { SOAK_OPTIONS } from '../config/options.js';
import { getKdsQueue, listOrders } from '../lib/http.js';
import { createSummaryHandler } from '../lib/summary.js';
import { runOrderWriteCycle } from '../lib/workloads.js';

export const options = SOAK_OPTIONS;

export default function () {
  const vu = exec.vu.idInTest;
  const iteration = exec.vu.iterationInScenario;

  const kdsRes = getKdsQueue(vu);
  check(kdsRes, { 'soak GET /kds/queue 200': (r) => r.status === 200 });

  if (iteration % 4 === 0) {
    runOrderWriteCycle(vu, iteration, {
      tableModulo: 60,
      includeDetail: false,
      confirmOrderFlag: true,
      labelPrefix: 'soak',
    });
  } else {
    const listRes = listOrders(vu);
    check(listRes, { 'soak GET /orders 200': (r) => r.status === 200 });
  }

  sleep(1.0);
}

export const handleSummary = createSummaryHandler('soak');
