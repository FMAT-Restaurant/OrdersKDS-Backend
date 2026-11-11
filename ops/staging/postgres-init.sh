#!/bin/sh
set -eu

for variable in \
    POSTGRES_APP_USER \
    POSTGRES_APP_PASSWORD \
    POSTGRES_MIGRATION_USER \
    POSTGRES_MIGRATION_PASSWORD \
    POSTGRES_READONLY_USER \
    POSTGRES_READONLY_PASSWORD
do
    if [ -z "$(printenv "$variable" || true)" ]; then
        echo "Missing required staging variable: $variable" >&2
        exit 1
    fi
done

psql \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=app_user="$POSTGRES_APP_USER" \
    --set=app_password="$POSTGRES_APP_PASSWORD" \
    --set=migration_user="$POSTGRES_MIGRATION_USER" \
    --set=migration_password="$POSTGRES_MIGRATION_PASSWORD" \
    --set=readonly_user="$POSTGRES_READONLY_USER" \
    --set=readonly_password="$POSTGRES_READONLY_PASSWORD" \
    <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'migration_user', :'migration_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'migration_user') \gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'readonly_user', :'readonly_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'readonly_user') \gexec

SELECT format('ALTER ROLE %I PASSWORD %L', :'app_user', :'app_password') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'migration_user', :'migration_password') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'readonly_user', :'readonly_password') \gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_user') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'migration_user') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'readonly_user') \gexec
GRANT USAGE ON SCHEMA public TO :"app_user", :"migration_user", :"readonly_user";
GRANT CREATE ON SCHEMA public TO :"migration_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA public
    GRANT SELECT ON TABLES TO :"readonly_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_user";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"readonly_user";
SQL
