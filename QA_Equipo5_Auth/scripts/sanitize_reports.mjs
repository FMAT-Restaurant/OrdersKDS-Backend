import fs from 'node:fs';
import path from 'node:path';

const EVIDENCIAS_DIR = path.resolve(process.cwd(), 'evidencias');

const REPORT_FILES = [
  path.join(EVIDENCIAS_DIR, 'newman-report.json'),
  path.join(EVIDENCIAS_DIR, 'newman-report.html'),
  path.join(EVIDENCIAS_DIR, 'bdd-report.json'),
  path.join(EVIDENCIAS_DIR, 'bdd-report.html'),
];

const GITLEAKS_FILES = [
  path.join(EVIDENCIAS_DIR, 'gitleaks/gitleaks_Auth-Backend.json'),
  path.join(EVIDENCIAS_DIR, 'gitleaks/gitleaks_Auth-Frontend.json'),
  path.join(EVIDENCIAS_DIR, 'gitleaks/gitleaks_QA_Equipo5_Auth.json'),
];

const LITERAL_TEST_PASSWORDS = [
  'AdminFmat2026!',
  'StaffFmat2026!',
  'WrongPassword99!',
  'A1b!Zx9@Q2w#E3r$T4y%U5i^O6p&A7s*D8f(G9h)J0k_L1z+X2c-V3b=N4m?P5q/R6t:W7y;Z',
  'A1b!Zx9@Q2w#E3r$T4y%U5i^O6p&A7s*D8f(G9h)J0k_L1z+X2c-V3b=N4m?P5q/R6t:W7y;',
  ' Abcdef1!',
  'Abcdef1!',
  'abcdef1!',
  'ABCDEF1!',
  'Abcdefg!',
  'Abcdef12',
  'Abc123!',
  'Xy9!Qw2@',
  'Qa8!Zx2@',
];

function sanitizeReportText(raw) {
  let out = raw;

  // 1. JSON Web Tokens (header.payload.signature)
  out = out.replace(
    /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g,
    '[REDACTED_EPHEMERAL_JWT]',
  );

  // 2. Refresh token cookies in Set-Cookie / Cookie headers
  out = out.replace(
    /fmat_refresh_token=[^;"<\s\\&]+/g,
    'fmat_refresh_token=[REDACTED_EPHEMERAL_COOKIE]',
  );

  // 3. Campos sensibles en JSON plano, JSON escapado y HTML &quot; (preservando placeholders {{...}})
  const secretKeys = '(?:refreshToken|accessToken|temporaryPassword|currentPassword|newPassword|password)';
  out = out.replace(
    new RegExp(`("${secretKeys}"\\s*:\\s*")((?!\\{\\{)[^"\\\\]+)(")`, 'g'),
    '$1[REDACTED_TEST_CREDENTIAL]$3',
  );
  out = out.replace(
    new RegExp(`(\\\\"${secretKeys}\\\\"\\s*:\\s*\\\\")((?!\\{\\{)[^\\\\"]+)(\\\\")`, 'g'),
    '$1[REDACTED_TEST_CREDENTIAL]$3',
  );
  out = out.replace(
    new RegExp(`(&quot;${secretKeys}&quot;\\s*:\\s*&quot;)((?!\\{\\{)[^&]+)(&quot;)`, 'g'),
    '$1[REDACTED_TEST_CREDENTIAL]$3',
  );

  // 4. Variables de entorno en newman-report.json (adminPassword / staffPassword)
  out = out.replace(
    /("key"\s*:\s*"(?:adminPassword|staffPassword)"\s*,\s*"value"\s*:\s*")([^"]+)(")/g,
    '$1[REDACTED_TEST_PASSWORD]$3',
  );

  // 5. Contraseñas temporales generadas por Auth-Backend (Fmat-XXXX-XXXX)
  out = out.replace(/Fmat-[A-Za-z0-9#!?@_$%^&*=-]{4,}/g, '[REDACTED_EPHEMERAL_TEMP_PASS]');

  // 6. Contraseñas literales y variantes escapadas en HTML usadas en Newman/BDD
  out = out.replace(/A1b!Zx9@[^"&<\\\s]+/g, '[REDACTED_TEST_PASSWORD]');
  for (const literal of LITERAL_TEST_PASSWORDS) {
    if (out.includes(literal)) {
      out = out.split(literal).join('[REDACTED_TEST_PASSWORD]');
    }
  }

  return out;
}

function sanitizeGitleaksJson(filePath) {
  if (!fs.existsSync(filePath)) return false;
  const before = fs.readFileSync(filePath, 'utf8');
  let parsed;
  try {
    parsed = JSON.parse(before);
  } catch {
    return false;
  }
  if (!Array.isArray(parsed)) return false;

  let changed = false;
  const cleaned = parsed.map((entry) => {
    const next = { ...entry };
    if (next.Email && next.Email !== '[REDACTED_PERSONAL_EMAIL]') {
      next.Email = '[REDACTED_PERSONAL_EMAIL]';
      changed = true;
    }
    if (next.Author && next.Author !== '[REDACTED_AUTHOR]') {
      next.Author = '[REDACTED_AUTHOR]';
      changed = true;
    }
    if (next.Secret && next.Secret !== 'REDACTED') {
      next.Secret = 'REDACTED';
      changed = true;
    }
    return next;
  });

  if (changed) {
    fs.writeFileSync(filePath, JSON.stringify(cleaned, null, 2) + '\n', 'utf8');
  }
  return changed;
}

let touched = 0;
for (const filePath of REPORT_FILES) {
  if (!fs.existsSync(filePath)) continue;
  const before = fs.readFileSync(filePath, 'utf8');
  const after = sanitizeReportText(before);
  if (after !== before) {
    fs.writeFileSync(filePath, after, 'utf8');
    touched++;
  }
}

for (const filePath of GITLEAKS_FILES) {
  if (sanitizeGitleaksJson(filePath)) {
    touched++;
  }
}

console.log(`[QA-I3][SANITIZE] Archivos sanitizados: ${touched}/${REPORT_FILES.length + GITLEAKS_FILES.length}`);
