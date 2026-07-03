-- D-06: Dedicated PostgreSQL application role with row-level-security enforcement.
--
-- RLS on Nodyra tables is always enforced against this role (NOBYPASSRLS).
-- Using the table-owner superuser role bypasses RLS entirely, even with FORCE
-- policies — which is why Nodyra checks at startup that the connection role
-- is not a superuser or bypass-RLS role (see app/tenancy.py: assert_safe_postgres_role).
--
-- Run this script as the PostgreSQL superuser (postgres) before starting Nodyra
-- in multi-tenant mode. Set DATABASE_URL to use nodyra_app, not the table owner.
--
-- Usage:
--   psql -U postgres -d nodyra -f postgres-role-setup.sql
--
-- After running, update DATABASE_URL:
--   postgresql+asyncpg://nodyra_app:<password>@localhost:5432/nodyra

-- 1. Create the application role.
CREATE ROLE nodyra_app WITH
    NOSUPERUSER
    NOINHERIT
    NOCREATEROLE
    NOCREATEDB
    NOREPLICATION
    NOBYPASSRLS
    LOGIN
    PASSWORD 'change_me_in_production';

-- 2. Grant connect and schema usage.
GRANT CONNECT ON DATABASE nodyra TO nodyra_app;
GRANT USAGE ON SCHEMA public TO nodyra_app;

-- 3. Grant DML on all existing tables and sequences.
--    Re-run this block whenever new migrations add tables.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nodyra_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nodyra_app;

-- 4. Grant DML on future tables created by migrations (run as the table owner).
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nodyra_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO nodyra_app;

-- Verification:
-- SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'nodyra_app';
-- Expected: rolsuper=false, rolbypassrls=false
