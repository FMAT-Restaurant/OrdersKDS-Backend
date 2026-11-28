export default {
  paths: ['bdd/features/**/*.feature'],
  import: ['bdd/steps/**/*.mjs'],
  format: [
    'progress-bar',
    'html:evidencias/bdd-report.html',
    'json:evidencias/bdd-report.json',
  ],
};
