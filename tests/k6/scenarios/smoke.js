import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { SMOKE_OPTIONS } from '../config/options.js';
import { checkHealth, getKdsQueue } from '../lib/http.js';
import { createSummaryHandler } from '../lib/summary.js';
import { runOrderWriteCycle } from '../lib/workloads.js';

export const options = SMOKE_OPTIONS;

export default function () {
  const vu = exec.vu.idInTest;
  const iteration = exec.vu.iterationInScenario;

  const { r1, r2 } = checkHealth();
  check(
    { r1, r2 },
    {
      'orders health 200': (x) => x.r1.status === 200,
      'kitchen health 200': (x) => x.r2.status === 200,
    }
  );

  const kdsRes = getKdsQueue(vu);
  check(kdsRes, { 'kds queue 200': (r) => r.status === 200 });

  runOrderWriteCycle(vu, iteration, {
    tableModulo: 40,
    includeDetail: true,
    confirmOrderFlag: true,
    labelPrefix: 'smoke',
  });

  sleep(0.5);
}

export const handleSummary = createSummaryHandler('smoke');
