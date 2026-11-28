import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const AUTH_BACKEND_DIR = process.env.AUTH_BACKEND_DIR || '/tmp/fmat-auth-sut/Auth-Backend';
const AUTH_FRONTEND_DIR = process.env.AUTH_FRONTEND_DIR || '/tmp/fmat-auth-sut/Auth-Frontend';
const ts = require(path.join(AUTH_BACKEND_DIR, 'node_modules/typescript'));

function walkFiles(dir, exts, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walkFiles(full, exts, out);
    } else if (
      exts.some((ext) => full.endsWith(ext)) &&
      !/\.(spec|test)\.[jt]sx?$/.test(full) &&
      !full.endsWith('.d.ts')
    ) {
      out.push(full);
    }
  }
  return out;
}

function isFunctionLike(node) {
  return (
    ts.isFunctionDeclaration(node) ||
    ts.isMethodDeclaration(node) ||
    ts.isConstructorDeclaration(node) ||
    ts.isGetAccessorDeclaration(node) ||
    ts.isSetAccessorDeclaration(node) ||
    ts.isFunctionExpression(node) ||
    ts.isArrowFunction(node)
  );
}

function getFunctionName(node, sf) {
  if (node.name && typeof node.name.getText === 'function') {
    return node.name.getText(sf);
  }
  if (ts.isConstructorDeclaration(node)) {
    return 'constructor';
  }
  if (ts.isVariableDeclaration(node.parent) && node.parent.name) {
    return node.parent.name.getText(sf);
  }
  if (ts.isPropertyAssignment(node.parent) && node.parent.name) {
    return node.parent.name.getText(sf);
  }
  return '<anonymous>';
}

function computeOwnMcCabeCc(fnNode) {
  let cc = 1;
  function visit(node) {
    if (node !== fnNode && isFunctionLike(node)) {
      return;
    }
    switch (node.kind) {
      case ts.SyntaxKind.IfStatement:
      case ts.SyntaxKind.ForStatement:
      case ts.SyntaxKind.ForInStatement:
      case ts.SyntaxKind.ForOfStatement:
      case ts.SyntaxKind.WhileStatement:
      case ts.SyntaxKind.DoStatement:
      case ts.SyntaxKind.CaseClause:
      case ts.SyntaxKind.CatchClause:
      case ts.SyntaxKind.ConditionalExpression:
        cc += 1;
        break;
      case ts.SyntaxKind.BinaryExpression:
        if (
          node.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken ||
          node.operatorToken.kind === ts.SyntaxKind.BarBarToken ||
          node.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken
        ) {
          cc += 1;
        }
        break;
      default:
        break;
    }
    ts.forEachChild(node, visit);
  }
  if (fnNode.body) {
    visit(fnNode.body);
  }
  return cc;
}

function analyzeRepo(repoName, repoDir, sonarProject) {
  const srcDir = path.join(repoDir, 'src');
  const files = walkFiles(srcDir, ['.ts', '.tsx']);
  const functions = [];

  for (const file of files) {
    const code = fs.readFileSync(file, 'utf8');
    const scriptKind = file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
    const sf = ts.createSourceFile(file, code, ts.ScriptTarget.Latest, true, scriptKind);
    function walk(node) {
      if (isFunctionLike(node) && node.body) {
        const cc = computeOwnMcCabeCc(node);
        const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
        functions.push({
          file: path.relative(repoDir, file).replace(/\\/g, '/'),
          line,
          name: getFunctionName(node, sf),
          cc,
        });
      }
      ts.forEachChild(node, walk);
    }
    walk(sf);
  }

  const over10 = functions
    .filter((fn) => fn.cc > 10)
    .sort((a, b) => b.cc - a.cc || a.file.localeCompare(b.file) || a.line - b.line);
  const maxCc = functions.reduce((max, fn) => Math.max(max, fn.cc), 0);

  const s3776Issues = (sonarProject?.openIssuesSample || [])
    .filter((issue) => issue.rule === 'typescript:S3776')
    .map((issue) => ({
      issueId: issue.issueId,
      rule: issue.rule,
      severity: issue.severity,
      component: issue.component,
      line: issue.line,
      message: issue.message,
    }));

  return {
    totalFunctions: functions.length,
    maxCc,
    functionsOverCc10Count: over10.length,
    meetsCcLe10PerFunction: over10.length === 0,
    functionsOverCc10: over10,
    sonarCognitiveComplexityIssues: s3776Issues,
  };
}

function main() {
  const baseDir = process.cwd();
  const sonarPath = path.resolve(baseDir, 'evidencias/sonarcloud_metrics.json');
  const metaPath = path.resolve(baseDir, 'evidencias/metadata_ejecucion.json');
  const outPath = path.resolve(baseDir, 'evidencias/static_reaudit/function_cc_audit.json');

  const sonar = JSON.parse(fs.readFileSync(sonarPath, 'utf8'));
  const metadata = JSON.parse(fs.readFileSync(metaPath, 'utf8'));

  const authBackend = analyzeRepo(
    'Auth-Backend',
    AUTH_BACKEND_DIR,
    sonar.projects?.['Auth-Backend'],
  );
  const authFrontend = analyzeRepo(
    'Auth-Frontend',
    AUTH_FRONTEND_DIR,
    sonar.projects?.['Auth-Frontend'],
  );

  const report = {
    auditedAtUtc: new Date().toISOString(),
    methodology: {
      scope: 'src/**/*.{ts,tsx} excluding *.spec.*, *.test.* and *.d.ts',
      algorithm:
        'Per-function McCabe cyclomatic complexity via TypeScript AST (base=1 plus If/For/ForIn/ForOf/While/Do/Case/Catch/Conditional/?:/&&/||/??, excluding nested function bodies)',
      threshold: 'CC <= 10 per function',
      sonarLimitNote:
        'SonarCloud public measures/component_tree API exposes aggregated file complexity and S3776 Cognitive Complexity (>15) issues, not individual McCabe CC per function.',
    },
    commits: {
      'Auth-Backend': metadata.repositorios?.['Auth-Backend'] || null,
      'Auth-Frontend': metadata.repositorios?.['Auth-Frontend'] || null,
    },
    repositories: {
      'Auth-Backend': authBackend,
      'Auth-Frontend': authFrontend,
    },
  };

  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, JSON.stringify(report, null, 2));

  console.log(
    `[QA-I3][CC-AST] Auth-Backend: total=${authBackend.totalFunctions} maxCC=${authBackend.maxCc} over10=${authBackend.functionsOverCc10Count} S3776=${authBackend.sonarCognitiveComplexityIssues.length}`,
  );
  console.log(
    `[QA-I3][CC-AST] Auth-Frontend: total=${authFrontend.totalFunctions} maxCC=${authFrontend.maxCc} over10=${authFrontend.functionsOverCc10Count} S3776=${authFrontend.sonarCognitiveComplexityIssues.length}`,
  );
}

main();
