# Contributing To Nodyra

Thanks for your interest in improving Nodyra! The core is distributed under the
fair-code [Nodyra Sustainable Use License](LICENSE); enterprise-gated features
are covered by [LICENSE.enterprise](LICENSE.enterprise). Please read both — and
this guide — before submitting code, documentation, design, or other
copyrightable material.

## Contributor License Agreement

Contributions require a signed
[Contributor License Agreement](CONTRIBUTOR_LICENSE_AGREEMENT.md) before they
can be merged.

The CLA is intended to let contributors keep ownership of their work while
granting the Project Owner the rights needed to maintain, distribute, relicense,
and commercially license Nodyra. The CLA contains contact and governing-law
placeholders that must be completed before the process is used for external
contributors.

## Contribution Flow

1. Open an issue before starting a substantial change.
2. Confirm with a maintainer that the change is in scope.
3. Sign the CLA through the process requested by the maintainers.
4. Submit a pull request with a clear summary and focused scope.
5. Include tests or documentation updates when the behavior changes.

Maintainers may decline contributions that arrive without a CLA, include
unclear third-party material, or fall outside the current product direction.

## Local Checks

Use the existing project tooling for the area you changed. Common checks are:

```powershell
uv run ruff check .
uv run pytest
cd apps/web
npm run typecheck
npm test
```

Run the narrowest relevant checks while developing, then run the broader suite
before opening a pull request when practical.

### Run against PostgreSQL before you trust a green suite

`uv run pytest` defaults to SQLite. That is fast and right for most work, but
production runs on PostgreSQL and **SQLite silently accepts things PostgreSQL
does not**. Five bugs reached a release tag this way; each passed locally and
failed in CI. The differences that actually bite:

| SQLite | PostgreSQL |
| --- | --- |
| `JSON` and `JSONB` are the same thing | Different types, different operators, no containment indexing on `JSON`. A migration creating `sa.JSON()` for a model column declared `POSTGRES_JSON` is drift `alembic check` will reject. |
| `CURRENT_TIMESTAMP` truncates to whole seconds | Microsecond precision. A test that captures `now` *before* inserting a row whose timestamp carries `server_default=func.now()` passes on SQLite by truncation and fails here. Set such columns explicitly in tests. |
| No row-level security | `FORCE ROW LEVEL SECURITY` with the `app.current_org` GUC. A query that forgets to scope returns rows on SQLite and nothing here. |
| `SELECT ... FOR UPDATE SKIP LOCKED` is a no-op | Real queue leasing semantics. |

Point the suite at a throwaway database:

```bash
docker run -d --name nodyra-pg -e POSTGRES_USER=nodyra -e POSTGRES_PASSWORD=nodyra \
  -e POSTGRES_DB=nodyra -p 55432:5432 postgres:16
docker exec nodyra-pg psql -U nodyra -d nodyra -c "CREATE DATABASE nodyra_test;"

NODYRA_TEST_DATABASE_URL="postgresql+asyncpg://nodyra:nodyra@localhost:55432/nodyra_test" \
DATABASE_URL="postgresql+asyncpg://nodyra:nodyra@localhost:55432/nodyra" \
  uv run pytest apps/api/tests -q
```

`NODYRA_TEST_DATABASE_URL` is what `conftest.py` builds the per-test schema in.
`DATABASE_URL` is what application settings resolve, which several tests reach
through — set both, or you will exercise a mix of the two engines.

And run the check that only PostgreSQL can answer:

```bash
cd apps/api
DATABASE_URL="postgresql+asyncpg://nodyra:nodyra@localhost:55432/nodyra" \
  uv run alembic upgrade head && uv run alembic check
```

`alembic check` compares the migrated schema against the ORM. It is the only
thing that catches a migration and a model disagreeing, and it is meaningless
on SQLite. Run it whenever you add or change a migration.

Tear down with `docker rm -f nodyra-pg`.

