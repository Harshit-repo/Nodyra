# Rollback notes for 0.1.0

Follow `docs/operations/upgrade-rollback.md`. Drain new work, preserve the
database and artifact store together, and roll application images back only
when the source version is declared forward-schema compatible. Do not run an
Alembic downgrade in production unless release-specific notes explicitly name
and authorize the revision.

Registry packages and workflow releases roll back by selecting an older
immutable signed version. Never replace bytes behind an existing version.
