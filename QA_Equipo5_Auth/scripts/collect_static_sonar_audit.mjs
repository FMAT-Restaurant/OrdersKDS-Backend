import fs from 'node:fs';
import path from 'node:path';

const SONAR_BASE = 'https://sonarcloud.io/api';
const PROJECTS = [
  { key: 'FMAT-Restaurant_Auth-Backend', name: 'Auth-Backend' },
  { key: 'FMAT-Restaurant_Auth-Frontend', name: 'Auth-Frontend' },
];

const METRICS = [
  'ncloc',
  'complexity',
  'cognitive_complexity',
  'coverage',
  'line_coverage',
  'branch_coverage',
  'duplicated_lines_density',
  'sqale_debt_ratio',
  'sqale_index',
  'bugs',
  'vulnerabilities',
  'code_smells',
  'security_hotspots',
  'reliability_rating',
  'security_rating',
  'sqale_rating',
].join(',');

const RATING_MAP = { '1.0': 'A', '2.0': 'B', '3.0': 'C', '4.0': 'D', '5.0': 'E' };

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`HTTP ${res.status} en ${url}: ${body.slice(0, 300)}`);
  }
  return res.json();
}

function parseMeasures(measures = []) {
  const out = {};
  for (const m of measures) {
    out[m.metric] = m.value;
    if (m.metric.endsWith('_rating')) {
      out[`${m.metric}_letter`] = RATING_MAP[m.value] || m.value;
    }
  }
  return out;
}

async function auditProject(projectKey) {
  const qg = await fetchJson(
    `${SONAR_BASE}/qualitygates/project_status?projectKey=${encodeURIComponent(projectKey)}`,
  );
  const comp = await fetchJson(
    `${SONAR_BASE}/measures/component?component=${encodeURIComponent(projectKey)}&metricKeys=${encodeURIComponent(METRICS)}`,
  );
  const tree = await fetchJson(
    `${SONAR_BASE}/measures/component_tree?component=${encodeURIComponent(projectKey)}&qualifiers=FIL&metricKeys=ncloc,complexity,cognitive_complexity,functions,coverage,code_smells&ps=300`,
  );
  const issues = await fetchJson(
    `${SONAR_BASE}/issues/search?componentKeys=${encodeURIComponent(projectKey)}&resolved=false&ps=100`,
  );

  const files = (tree.components || [])
    .map((f) => {
      const m = parseMeasures(f.measures);
      const cc = Number(m.complexity || 0);
      const cognitive = Number(m.cognitive_complexity || 0);
      const funcs = Number(m.functions || 0);
      const ncloc = Number(m.ncloc || 0);
      const coverage = m.coverage !== undefined ? Number(m.coverage) : null;
      return {
        path: f.path,
        ncloc,
        functions: funcs,
        complexity: cc,
        cognitive_complexity: cognitive,
        avg_cc_per_function: funcs > 0 ? Number((cc / funcs).toFixed(2)) : null,
        coverage,
        code_smells: Number(m.code_smells || 0),
      };
    })
    .sort((a, b) => b.complexity - a.complexity || b.ncloc - a.ncloc);

  const measures = parseMeasures(comp.component?.measures || []);

  return {
    projectKey,
    qualityGate: qg.projectStatus,
    measures,
    filesOverFileCc10: files.filter((f) => f.complexity > 10),
    filesOverAvgFuncCc10: files.filter((f) => (f.avg_cc_per_function || 0) > 10),
    topComplexFiles: files.slice(0, 20),
    allFilesCount: files.length,
    openIssuesTotal: issues.total,
    openIssuesSample: (issues.issues || []).map((i) => ({
      issueId: i.key,
      rule: i.rule,
      severity: i.severity,
      type: i.type,
      component: i.component,
      line: i.line,
      message: i.message,
    })),
  };
}

async function main() {
  const results = {};
  for (const proj of PROJECTS) {
    results[proj.name] = await auditProject(proj.key);
  }

  const output = {
    collectedAtUtc: new Date().toISOString(),
    source: 'https://sonarcloud.io/api',
    projects: results,
  };

  const outPath = path.resolve(process.cwd(), 'evidencias/sonarcloud_metrics.json');
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, JSON.stringify(output, null, 2));

  for (const proj of PROJECTS) {
    const p = results[proj.name];
    console.log(
      `[QA-I3][SONAR] ${proj.name}: QG=${p.qualityGate?.status} cov=${p.measures.coverage}% dup=${p.measures.duplicated_lines_density}% rel=${p.measures.reliability_rating_letter} sec=${p.measures.security_rating_letter} maint=${p.measures.sqale_rating_letter} smells=${p.measures.code_smells} bugs=${p.measures.bugs} vuln=${p.measures.vulnerabilities} filesCC>10=${p.filesOverFileCc10.length}`,
    );
  }
}

main().catch((err) => {
  console.error('[QA-I3][SONAR] Error:', err);
  process.exit(1);
});
