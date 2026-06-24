-- D-06: Dedicated PostgreSQL application role with row-level-security enforcement.
--
-- RLS on Noodle tables is always enforced against this role (NOBYPASSRLS).
-- Using the table-owner superuser role bypasses RLS entirely, even with FORCE
-- policies — which is why Noodle checks at startup that the connection role
-- is not a superuser or bypass-RLS role (see app/tenancy.py: assert_safe_postgres_role).
--
-- Run this script as the PostgreSQL superuser (postgres) before starting Noodle
-- in multi-tenant mode. Set DATABASE_URL to use noodle_app, not the table owner.
--
-- Usage:
--   psql -U postgres -d noodle -f postgres-role-setup.sql
--
-- After running, update DATABASE_URL:
--   postgresql+asyncpg://noodle_app:<password>@localhost:5432/noodle

-- 1. Create the application role.
CREATE ROLE noodle_app WITH
    NOSUPERUSER
    NOINHERIT
    NOCREATEROLE
    NOCREATEDB
    NOREPLICATION
    NOBYPASSRLS
    LOGIN
    PASSWORD 'change_me_in_production';

-- 2. Grant connect and schema usage.
GRANT CONNECT ON DATABASE noodle TO noodle_app;
GRANT USAGE ON SCHEMA public TO noodle_app;

-- 3. Grant DML on all existing tables and sequences.
--    Re-run this block whenever new migrations add tables.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO noodle_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO noodle_app;

-- 4. Grant DML on future tables created by migrations (run as the table owner).
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO noodle_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO noodle_app;

-- Verification:
-- SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'noodle_app';
-- Expected: rolsuper=false, rolbypassrls=false
