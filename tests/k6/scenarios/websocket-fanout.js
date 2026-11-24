import { sleep } from 'k6';
import exec from 'k6/execution';
import { ENV } from '../config/env.js';
import { WEBSOCKET_FANOUT_OPTIONS } from '../config/options.js';
import { getTokenFor } from '../lib/auth.js';
import { KDS_WS_CHANNELS } from '../lib/setup.js';
import { createSummaryHandler } from '../lib/summary.js';
import { runOrderWriteCycle } from '../lib/workloads.js';
import { openAndMonitorSocket } from '../lib/ws.js';

export const options = WEBSOCKET_FANOUT_OPTIONS;

export function runKdsListener() {
  const vu = exec.vu.idInTest;
  const token = getTokenFor('kitchen', vu);
  const channel = KDS_WS_CHANNELS[vu % KDS_WS_CHANNELS.length];
  openAndMonitorSocket(ENV.kitchenWsUrl, ['bearer', token], [channel]);
}

export function runWaiterListener() {
  const vu = exec.vu.idInTest;
  const token = getTokenFor('waiter', vu);
  openAndMonitorSocket(ENV.ordersWsUrl, ['bearer', token], []);
}

export function runOrderMutations() {
  const vu = exec.vu.idInTest;
  const iteration = exec.vu.iterationInScenario;
  runOrderWriteCycle(vu, iteration, {
    tableModulo: 30,
    includeDetail: false,
    confirmOrderFlag: true,
    labelPrefix: 'ws_mutator',
  });
  sleep(0.1);
}

export const handleSummary = createSummaryHandler('websocket-fanout');
