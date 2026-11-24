import { ENV } from '../config/env.js';

function escapeHtml(text) {
  return String(text)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function metricVal(metrics, name, stat, fallback = 0) {
  const value = metrics?.[name]?.values?.[stat];
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function collectSummaryStats(data) {
  const metrics = data?.metrics || {};
  return {
    totalReqs: metricVal(metrics, 'http_reqs', 'count', 0),
    rps: metricVal(metrics, 'http_reqs', 'rate', 0),
    p95Ms: metricVal(metrics, 'http_req_duration', 'p(95)', 0),
    p99Ms: metricVal(metrics, 'http_req_duration', 'p(99)', 0),
    avgMs: metricVal(metrics, 'http_req_duration', 'avg', 0),
    failPct: metricVal(metrics, 'http_req_failed', 'rate', 0) * 100,
    checksPct: metricVal(metrics, 'checks', 'rate', 0) * 100,
    server5xxPct: metricVal(metrics, 'server_errors_5xx', 'rate', 0) * 100,
    wsMessages: metricVal(metrics, 'ws_messages_received', 'count', 0),
    wsP95Ms: metricVal(metrics, 'ws_delivery_duration', 'p(95)', 0),
  };
}

function renderTextSummary(scenarioName, stats) {
  return [
    '',
    `=== k6 Summary: ${scenarioName} ===`,
    `  http_reqs............: ${stats.totalReqs} (${stats.rps.toFixed(2)}/s)`,
    `  http_req_duration....: avg=${stats.avgMs.toFixed(2)}ms p(95)=${stats.p95Ms.toFixed(2)}ms p(99)=${stats.p99Ms.toFixed(2)}ms`,
    `  http_req_failed......: ${stats.failPct.toFixed(3)}%`,
    `  server_errors_5xx....: ${stats.server5xxPct.toFixed(3)}%`,
    `  checks...............: ${stats.checksPct.toFixed(2)}%`,
    `  ws_messages_received.: ${stats.wsMessages} (p95=${stats.wsP95Ms.toFixed(2)}ms)`,
    '',
  ].join('\n');
}

function renderHtmlReport(scenarioName, stats) {
  const safeName = escapeHtml(scenarioName);
  return `<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>k6 Report - ${safeName}</title>
<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#111}
table{width:100%;border-collapse:collapse;margin-top:1rem}th,td{border:1px solid #d1d5db;padding:8px 12px;text-align:left}
th{background:#f3f4f6}</style>
</head><body>
<h1>Reporte k6: ${safeName}</h1>
<table>
  <tr><th>Metrica</th><th>Valor</th></tr>
  <tr><td>Requests totales</td><td>${stats.totalReqs}</td></tr>
  <tr><td>Throughput (req/s)</td><td>${stats.rps.toFixed(2)}</td></tr>
  <tr><td>Promedio http_req_duration</td><td>${stats.avgMs.toFixed(2)} ms</td></tr>
  <tr><td>p(95) http_req_duration</td><td>${stats.p95Ms.toFixed(2)} ms</td></tr>
  <tr><td>p(99) http_req_duration</td><td>${stats.p99Ms.toFixed(2)} ms</td></tr>
  <tr><td>Tasa de error (http_req_failed)</td><td>${stats.failPct.toFixed(3)} %</td></tr>
  <tr><td>Errores 5xx (server_errors_5xx)</td><td>${stats.server5xxPct.toFixed(3)} %</td></tr>
  <tr><td>Checks aprobados</td><td>${stats.checksPct.toFixed(2)} %</td></tr>
  <tr><td>Mensajes WebSocket recibidos</td><td>${stats.wsMessages}</td></tr>
  <tr><td>p(95) entrega WebSocket</td><td>${stats.wsP95Ms.toFixed(2)} ms</td></tr>
</table></body></html>`;
}

/**
 * Handler autonomo (sin dependencias externas) para JSON, HTML y consola.
 */
export function createSummaryHandler(scenarioName) {
  return function handleSummary(data) {
    const stats = collectSummaryStats(data);
    return {
      [`${ENV.summaryDir}/${scenarioName}-summary.json`]: JSON.stringify(data, null, 2),
      [`${ENV.summaryDir}/${scenarioName}-report.html`]: renderHtmlReport(scenarioName, stats),
      stdout: renderTextSummary(scenarioName, stats),
    };
  };
}
