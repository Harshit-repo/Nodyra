# Upgrading a Noodle deployment to Nodyra

Nodyra is the new name for Noodle. One-time operational changes:

## Before you upgrade

1. **Drain the run queue.** Redis keys moved from `noodle:*` to `nodyra:*`;
   in-flight queue state is not migrated. Stop enqueuing, let running work
   finish (`GET /ops/queue` shows zero leased/queued), then deploy.
2. Note your `NOODLE_*` environment variables. They still work this release
   where a renamed `NODYRA_*` setting exists, but the `NODYRA_*` names are now
   canonical and the old names will be removed in a later release.

## What breaks once

- **Sessions:** the session cookie was renamed (`noodle_session` ->
  `nodyra_session`); browser users log in again. API tokens (`ndpat_...`) are
  unchanged.
- **Web localStorage token:** browsers that still have `noodle_token` migrate
  it to `nodyra_token` automatically on first load.
- **Dev compose database:** default user/db renamed `noodle` -> `nodyra`.
  Fresh dev volume required if you used the old defaults:
  `docker compose -f deploy/docker-compose.yml down -v`.
- **MCP clients:** resource URIs are now `nodyra://...`; re-run discovery.
- **Vault transit KMS:** the default transit key name is now `nodyra-master`.
  Either create the new key and re-wrap, or keep the old key by setting the
  explicit key-name setting.

## What does not change

- Database schema, table names, and migration history.
- `ndpat_` personal-access-token format.
- Webhook paths and workflow/run IDs.
