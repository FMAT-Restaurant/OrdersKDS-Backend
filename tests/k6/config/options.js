import { ENV } from './env.js';

/**
 * Perfiles de carga compartidos (PT-01..08), mesetas de 300 VUs y picos de 600 VUs.
 */

const SUMMARY_TREND_STATS = ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'];

export const SMOKE_OPTIONS = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  discardResponseBodies: false,
  scenarios: {
    smoke: {
      executor: 'constant-vus',
      vus: ENV.smokeVus,
      duration: ENV.smokeDuration,
      gracefulStop: '10s',
      tags: { suite: 'smoke', pt: 'PT-08' },
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.001'],
    http_req_duration: ['p(95)<100'],
    checks: ['rate>0.999'],
  },
};

export const LOAD_OPTIONS = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  discardResponseBodies: true,
  scenarios: {
    nominal_load: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '1m', target: 50 },
        { duration: '8m', target: 50 },
        { duration: '1m', target: 0 },
      ],
      gracefulRampDown: '30s',
      tags: { suite: 'load', pt: 'PT-01_PT-02' },
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<200', 'p(99)<500'],
    server_errors_5xx: ['rate<0.001'],
    checks: ['rate>0.99'],
  },
};

export const STRESS_300_TO_600_OPTIONS = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  discardResponseBodies: true,
  scenarios: {
    stepped_stress: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '1m', target: 50 },
        { duration: '2m', target: 50 },
        { duration: '1m', target: 150 },
        { duration: '3m', target: 150 },
        { duration: '1m', target: 300 },
        { duration: '5m', target: 300 },
        { duration: '1m', target: 450 },
        { duration: '3m', target: 450 },
        { duration: '1m', target: 600 },
        { duration: '3m', target: 600 },
        { duration: '1m', target: 300 },
        { duration: '2m', target: 300 },
        { duration: '1m', target: 0 },
      ],
      gracefulRampDown: '30s',
      tags: { suite: 'stress', pt: 'PT-03_BREAKPOINT' },
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    http_req_failed: ['rate<0.01'],
    server_errors_5xx: [{ threshold: 'rate<0.10', abortOnFail: true, delayAbortEval: '30s' }],
  },
};

export const SPIKE_600_FROM_300_OPTIONS = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  discardResponseBodies: true,
  scenarios: {
    spike_300_to_600: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 100 },
        { duration: '1m', target: 300 },
        { duration: '3m', target: 300 },
        { duration: '15s', target: 600 },
        { duration: '2m', target: 600 },
        { duration: '15s', target: 300 },
        { duration: '3m', target: 300 },
        { duration: '15s', target: 600 },
        { duration: '2m', target: 600 },
        { duration: '30s', target: 300 },
        { duration: '2m', target: 300 },
        { duration: '1m', target: 0 },
      ],
      gracefulRampDown: '30s',
      tags: { suite: 'spike', pt: 'SPIKE_300_600' },
    },
  },
  thresholds: {
    server_errors_5xx: ['rate<0.02'],
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<1500'],
  },
};

export const SOAK_OPTIONS = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  discardResponseBodies: true,
  scenarios: {
    soak_45m: {
      executor: 'constant-vus',
      vus: ENV.soakVus,
      duration: ENV.soakDuration,
      gracefulStop: '30s',
      tags: { suite: 'soak', pt: 'PT-06' },
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.005'],
    http_req_duration: ['p(95)<250', 'p(99)<600'],
    server_errors_5xx: ['rate<0.001'],
  },
};

export const WEBSOCKET_FANOUT_OPTIONS = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  scenarios: {
    kds_sockets: {
      executor: 'constant-vus',
      exec: 'runKdsListener',
      vus: 20,
      duration: ENV.wsDuration,
      gracefulStop: '10s',
      tags: { suite: 'websocket', pt: 'PT-05' },
    },
    waiter_sockets: {
      executor: 'constant-vus',
      exec: 'runWaiterListener',
      vus: 30,
      duration: ENV.wsDuration,
      gracefulStop: '10s',
      tags: { suite: 'websocket', pt: 'PT-05' },
    },
    mutator_burst: {
      executor: 'constant-arrival-rate',
      exec: 'runOrderMutations',
      rate: 8,
      timeUnit: '1s',
      duration: ENV.wsMutatorDuration,
      startTime: '10s',
      preAllocatedVUs: 20,
      maxVUs: 50,
      tags: { suite: 'websocket', pt: 'PT-05' },
    },
  },
  thresholds: {
    ws_delivery_duration: ['p(95)<500'],
    ws_messages_received: ['count>50'],
    ws_version_regressions: ['count==0'],
    http_req_failed: ['rate<0.01'],
  },
};

export const STOCK_RACE_OPTIONS = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  scenarios: {
    concurrent_confirm: {
      executor: 'per-vu-iterations',
      vus: 20,
      iterations: 1,
      maxDuration: '90s',
      tags: { suite: 'stock_race', pt: 'PT-07' },
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    stock_race_pending_timeout: ['count==0'],
  },
};

export const ARRIVAL_RATE_OPTIONAL_PROFILE = {
  executor: 'ramping-arrival-rate',
  startRate: 20,
  timeUnit: '1s',
  preAllocatedVUs: 300,
  maxVUs: 600,
  stages: [
    { duration: '2m', target: 150 },
    { duration: '5m', target: 150 },
    { duration: '30s', target: 350 },
    { duration: '2m', target: 350 },
    { duration: '1m', target: 0 },
  ],
};
