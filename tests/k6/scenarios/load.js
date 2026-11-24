import exec from 'k6/execution';
import { LOAD_OPTIONS } from '../config/options.js';
import { discoverKdsActiveOrders } from '../lib/setup.js';
import { createSummaryHandler } from '../lib/summary.js';
import { jitterSleep, runWeightedTraffic } from '../lib/workloads.js';

export const options = LOAD_OPTIONS;

export function setup() {
  return discoverKdsActiveOrders();
}

/**
 * Distribucion 70% consultas / 20% creacion-edicion / 10% transiciones cocina.
 */
export default function (data) {
  const vu = exec.vu.idInTest;
  const iteration = exec.vu.iterationInScenario;

  runWeightedTraffic(vu, iteration, {
    readWeight: 0.70,
    writeWeight: 0.20,
    kdsRatio: 0.64,
    tableModulo: 50,
    includeDetail: false,
    confirmWhen: (iter) => iter % 2 === 0,
    kitchenData: data,
    labelPrefix: 'load',
  });

  jitterSleep(0.3, 0.4, vu, iteration);
}

export const handleSummary = createSummaryHandler('load');
