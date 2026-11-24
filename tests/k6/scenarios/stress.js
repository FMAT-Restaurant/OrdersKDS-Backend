import exec from 'k6/execution';
import { STRESS_300_TO_600_OPTIONS } from '../config/options.js';
import { createSummaryHandler } from '../lib/summary.js';
import { jitterSleep, runWeightedTraffic } from '../lib/workloads.js';

export const options = STRESS_300_TO_600_OPTIONS;

export default function () {
  const vu = exec.vu.idInTest;
  const iteration = exec.vu.iterationInScenario;

  runWeightedTraffic(vu, iteration, {
    readWeight: 0.70,
    writeWeight: 0.30,
    kdsRatio: 0.60,
    tableModulo: 80,
    includeDetail: false,
    labelPrefix: 'stress',
  });

  jitterSleep(0.25, 0.25, vu, iteration);
}

export const handleSummary = createSummaryHandler('stress');
