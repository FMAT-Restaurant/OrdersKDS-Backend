-- Auditoría SQL directa sobre PostgreSQL 16 (base fmat_auth) - Iteración 3 (QA_Equipo5_Auth)
-- 1. Migraciones TypeORM aplicadas desde BD vacía
SELECT id, timestamp, name
FROM migrations
ORDER BY id ASC;

-- 2. Verificación de prefijo bcrypt $2b$12$ en hashes de contraseñas (NFR-AUTH-SEC-001)
SELECT
  COUNT(*)::int AS total_users,
  COUNT(*) FILTER (WHERE "passwordHash" ~ '^\$2b\$12\$[./A-Za-z0-9]{53}$')::int AS valid_bcrypt_2b_12,
  COUNT(*) FILTER (WHERE "passwordHash" !~ '^\$2b\$12\$[./A-Za-z0-9]{53}$')::int AS invalid_or_legacy_hashes
FROM users;

-- 3. Verificación de refresh_tokens hasheados en SHA-256 (64 hex chars, cero tokens en claro)
SELECT
  COUNT(*)::int AS total_refresh_tokens,
  COUNT(*) FILTER (WHERE "tokenHash" ~ '^[a-f0-9]{64}$')::int AS valid_sha256_hashes,
  COUNT(*) FILTER (WHERE "tokenHash" !~ '^[a-f0-9]{64}$')::int AS non_sha256_tokens,
  COUNT(*) FILTER (WHERE "isRevoked" = true)::int AS revoked_tokens,
  COUNT(*) FILTER (WHERE "isRevoked" = false)::int AS active_tokens
FROM refresh_tokens;

-- 4. Auditoría de registros inmutables en audit_logs (CP-AUTH-21 / NFR-AUTH-AUD-001)
SELECT
  COUNT(*)::int AS total_audit_logs,
  COUNT(*) FILTER (WHERE "ipAddress" IS NOT NULL AND length(trim("ipAddress")) > 0)::int AS with_non_null_ip,
  COUNT(*) FILTER (WHERE "userAgent" IS NOT NULL AND length(trim("userAgent")) > 0)::int AS with_non_null_user_agent,
  COUNT(*) FILTER (WHERE "ipAddress" IS NULL OR "userAgent" IS NULL)::int AS missing_network_context
FROM audit_logs;

-- 5. Conteo agrupado por tipo de acción en audit_logs
SELECT action, COUNT(*)::int AS total
FROM audit_logs
GROUP BY action
ORDER BY total DESC, action ASC;

-- 6. Resumen de roles y perfiles de personal activos/temporales/inactivos
SELECT
  u."userType",
  u."passwordStatus",
  u."isActive",
  COUNT(*)::int AS total
FROM users u
GROUP BY u."userType", u."passwordStatus", u."isActive"
ORDER BY u."userType", u."passwordStatus", u."isActive";
