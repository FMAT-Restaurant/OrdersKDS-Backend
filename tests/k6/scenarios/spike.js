import exec from 'k6/execution';
import { SPIKE_600_FROM_300_OPTIONS } from '../config/options.js';
import { createSummaryHandler } from '../lib/summary.js';
import { jitterSleep, runWeightedTraffic } from '../lib/workloads.js';

export const options = SPIKE_600_FROM_300_OPTIONS;

export default function () {
  const vu = exec.vu.idInTest;
  const iteration = exec.vu.iterationInScenario;

  runWeightedTraffic(vu, iteration, {
    readWeight: 0.80,
    writeWeight: 0.20,
    kdsRatio: 0.6875,
    stations: ['parrilla', ''],
    tableModulo: 100,
    includeDetail: false,
    labelPrefix: 'spike',
  });

  jitterSleep(0.2, 0.3, vu, iteration);
}

export const handleSummary = createSummaryHandler('spike');
