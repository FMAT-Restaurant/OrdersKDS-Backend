import fs from 'node:fs';
import path from 'node:path';
import pg from 'pg';

const { Client } = pg;
const CONNECTION_STRING =
  process.env.PG_CONNECTION_STRING ||
  'postgresql://postgres:postgres@localhost:5433/fmat_auth';

async function main() {
  const client = new Client({ connectionString: CONNECTION_STRING });
  await client.connect();

  try {
    const tablesRes = await client.query(`
      SELECT tablename
      FROM pg_tables
      WHERE schemaname = 'public'
      ORDER BY tablename ASC
    `);

    const migrationsRes = await client.query(`
      SELECT id, timestamp, name
      FROM migrations
      ORDER BY id ASC
    `);

    const passwordHashesRes = await client.query(`
      SELECT
        COUNT(*)::int AS total_users,
        COUNT(*) FILTER (WHERE "passwordHash" ~ '^\\$2b\\$12\\$[./A-Za-z0-9]{53}$')::int AS valid_bcrypt_2b_12,
        COUNT(*) FILTER (WHERE "passwordHash" !~ '^\\$2b\\$12\\$[./A-Za-z0-9]{53}$')::int AS invalid_or_legacy_hashes
      FROM users
    `);

    const passwordPrefixesSampleRes = await client.query(`
      SELECT id, "userType", "passwordStatus", left("passwordHash", 7) AS hash_prefix, length("passwordHash")::int AS hash_length
      FROM users
      ORDER BY "createdAt" ASC
      LIMIT 15
    `);

    const refreshTokensRes = await client.query(`
      SELECT
        COUNT(*)::int AS total_refresh_tokens,
        COUNT(*) FILTER (WHERE "tokenHash" ~ '^[a-f0-9]{64}$')::int AS valid_sha256_hashes,
        COUNT(*) FILTER (WHERE "tokenHash" !~ '^[a-f0-9]{64}$')::int AS non_sha256_tokens,
        COUNT(*) FILTER (WHERE "isRevoked" = true)::int AS revoked_tokens,
        COUNT(*) FILTER (WHERE "isRevoked" = false)::int AS active_tokens
      FROM refresh_tokens
    `);

    const auditSummaryRes = await client.query(`
      SELECT
        COUNT(*)::int AS total_audit_logs,
        COUNT(*) FILTER (WHERE "ipAddress" IS NOT NULL AND length(trim("ipAddress")) > 0)::int AS with_non_null_ip,
        COUNT(*) FILTER (WHERE "userAgent" IS NOT NULL AND length(trim("userAgent")) > 0)::int AS with_non_null_user_agent,
        COUNT(*) FILTER (WHERE "ipAddress" IS NULL OR "userAgent" IS NULL)::int AS missing_network_context
      FROM audit_logs
    `);

    const auditByActionRes = await client.query(`
      SELECT action, COUNT(*)::int AS total
      FROM audit_logs
      GROUP BY action
      ORDER BY total DESC, action ASC
    `);

    const auditSampleRes = await client.query(`
      SELECT id, "userId", action, "ipAddress", left("userAgent", 80) AS user_agent_prefix, details, "createdAt"
      FROM audit_logs
      ORDER BY "createdAt" DESC
      LIMIT 20
    `);

    const usersBreakdownRes = await client.query(`
      SELECT u."userType", u."passwordStatus", u."isActive", COUNT(*)::int AS total
      FROM users u
      GROUP BY u."userType", u."passwordStatus", u."isActive"
      ORDER BY u."userType", u."passwordStatus", u."isActive"
    `);

    const rolesRes = await client.query(`
      SELECT
        code,
        name,
        "isSystemRole",
        "isAssignable",
        CASE
          WHEN permissions IS NULL OR length(trim(permissions)) = 0 THEN 0
          ELSE array_length(string_to_array(permissions, ','), 1)
        END::int AS permission_count
      FROM roles
      ORDER BY code ASC
    `);

    const passwordStats = passwordHashesRes.rows[0];
    const tokenStats = refreshTokensRes.rows[0];
    const auditStats = auditSummaryRes.rows[0];

    const checks = [
      {
        id: 'SQL-01-MIGRATIONS',
        description: 'Migraciones TypeORM ejecutadas sobre esquema public vacío',
        passed: migrationsRes.rows.length >= 1,
        observed: {
          tables: tablesRes.rows.map((r) => r.tablename),
          migrations: migrationsRes.rows,
        },
      },
      {
        id: 'SQL-02-BCRYPT-2B-12',
        description: '100% de hashes en tabla users utilizan bcrypt $2b$12$ (NFR-AUTH-SEC-001)',
        passed:
          passwordStats.total_users > 0 &&
          passwordStats.valid_bcrypt_2b_12 === passwordStats.total_users &&
          passwordStats.invalid_or_legacy_hashes === 0,
        observed: passwordStats,
      },
      {
        id: 'SQL-03-REFRESH-SHA256',
        description: '100% de refresh_tokens persistidos como hash SHA-256 hex de 64 caracteres (sin texto claro)',
        passed:
          tokenStats.total_refresh_tokens > 0 &&
          tokenStats.valid_sha256_hashes === tokenStats.total_refresh_tokens &&
          tokenStats.non_sha256_tokens === 0,
        observed: tokenStats,
      },
      {
        id: 'SQL-04-AUDIT-LOGS-CONTEXT',
        description: 'Registros en audit_logs contienen ipAddress y userAgent no nulos (CP-AUTH-21 / NFR-AUTH-AUD-001)',
        passed:
          auditStats.total_audit_logs > 0 &&
          auditStats.missing_network_context === 0 &&
          auditStats.with_non_null_ip === auditStats.total_audit_logs &&
          auditStats.with_non_null_user_agent === auditStats.total_audit_logs,
        observed: {
          summary: auditStats,
          byAction: auditByActionRes.rows,
        },
      },
    ];

    const auditReport = {
      executedAtUtc: new Date().toISOString(),
      database: 'fmat_auth',
      tables: tablesRes.rows.map((r) => r.tablename),
      migrations: migrationsRes.rows,
      passwordHashes: {
        summary: passwordStats,
        samplePrefixes: passwordPrefixesSampleRes.rows,
      },
      refreshTokens: tokenStats,
      auditLogs: {
        summary: auditStats,
        byAction: auditByActionRes.rows,
        latest20: auditSampleRes.rows,
      },
      usersBreakdown: usersBreakdownRes.rows,
      roles: rolesRes.rows,
      checks,
      overallPassed: checks.every((c) => c.passed),
    };

    const outDir = path.resolve(process.cwd(), 'evidencias/CP-AUTH-21');
    fs.mkdirSync(outDir, { recursive: true });
    fs.writeFileSync(path.join(outDir, 'sql_audit.json'), JSON.stringify(auditReport, null, 2));

    const txtLines = [
      `=== AUDITORIA SQL DIRECTA (fmat_auth) @ ${auditReport.executedAtUtc} ===`,
      `Tablas: ${auditReport.tables.join(', ')}`,
      `Migraciones: ${JSON.stringify(auditReport.migrations)}`,
      `Hashes bcrypt ($2b$12$): ${JSON.stringify(passwordStats)}`,
      `Refresh Tokens (SHA-256): ${JSON.stringify(tokenStats)}`,
      `Audit Logs resumen: ${JSON.stringify(auditStats)}`,
      `Audit Logs por accion: ${JSON.stringify(auditByActionRes.rows)}`,
      `Usuarios por estado: ${JSON.stringify(usersBreakdownRes.rows)}`,
      `Checks passed: ${checks.filter((c) => c.passed).length}/${checks.length}`,
    ];
    fs.writeFileSync(path.join(outDir, 'sql_audit.txt'), txtLines.join('\n') + '\n');

    // Enriquecer evidencias/CP-AUTH-21/registro.json
    const regPath = path.join(outDir, 'registro.json');
    let existingReg = {};
    if (fs.existsSync(regPath)) {
      existingReg = JSON.parse(fs.readFileSync(regPath, 'utf8'));
    }

    const existingVerifs = Array.isArray(existingReg.verificaciones)
      ? existingReg.verificaciones.filter((v) => !String(v.id || '').startsWith('SQL-'))
      : [];
    const mergedVerifs = [
      ...existingVerifs,
      ...checks.map((c) => ({
        id: c.id,
        origen: 'postgresql-sql',
        nombre: c.description,
        estado: c.passed ? 'PASA' : 'FALLA',
        observado: c.observed,
      })),
    ];

    const pasanCount = mergedVerifs.filter((v) => v.estado === 'PASA').length;
    const fallanCount = mergedVerifs.filter((v) => v.estado === 'FALLA').length;
    const estadoGlobal =
      fallanCount === 0 && pasanCount > 0
        ? 'PASA'
        : fallanCount > 0 && pasanCount > 0
          ? 'FALLA_PARCIAL'
          : 'FALLA';

    const updatedReg = {
      ...existingReg,
      casoId: 'CP-AUTH-21',
      titulo: existingReg.titulo || 'Auditoría inmutable en audit_logs, bcrypt $2b$12$, SHA-256 y headers HTTP',
      requisitos: existingReg.requisitos || ['NFR-AUTH-AUD-001', 'NFR-AUTH-SEC-001', 'NFR-AUTH-SEC-002', 'REQ-AUTH-022'],
      estadoGlobal,
      resumenVerificaciones: {
        total: mergedVerifs.length,
        pasan: pasanCount,
        fallan: fallanCount,
      },
      verificaciones: mergedVerifs,
      evidenciaSqlPath: 'evidencias/CP-AUTH-21/sql_audit.json',
      actualizadoSqlUtc: auditReport.executedAtUtc,
    };
    fs.writeFileSync(regPath, JSON.stringify(updatedReg, null, 2));

    const summaryPath = path.resolve(process.cwd(), 'evidencias/resumen_casos_i3.json');
    if (fs.existsSync(summaryPath)) {
      const summary = JSON.parse(fs.readFileSync(summaryPath, 'utf8'));
      const idx = (summary.cases || []).findIndex((c) => c.casoId === 'CP-AUTH-21');
      if (idx >= 0) {
        summary.cases[idx] = {
          casoId: 'CP-AUTH-21',
          estadoGlobal,
          pasan: pasanCount,
          fallan: fallanCount,
          total: mergedVerifs.length,
        };
        fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
      }
    }

    console.log(
      `[QA-I3][SQL] Audit complete: users=${passwordStats.total_users} ($2b$12$: ${passwordStats.valid_bcrypt_2b_12}), refreshTokens=${tokenStats.total_refresh_tokens} (SHA256: ${tokenStats.valid_sha256_hashes}), auditLogs=${auditStats.total_audit_logs}, missingContext=${auditStats.missing_network_context}`,
    );
    if (!auditReport.overallPassed) {
      process.exitCode = 1;
    }
  } finally {
    await client.end();
  }
}

main().catch((err) => {
  console.error('[QA-I3][SQL] Error:', err);
  process.exit(1);
});
