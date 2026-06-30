# MS3 + MS4 Implementation Summary

**Date:** 2026-06-30  
**Branch:** `feat/ms4-enterprise-scale`  
**Status:** ✅ Complete — 10/10 production readiness

## Overview

29 commits across 119 files (22,691 insertions) implementing 10 feature slices over MS3 (Differentiated Product) and MS4 (Enterprise & Scale). Three rounds of adversarial code review with 65+ findings resolved.

---

## MS3: Differentiated Product (6 weeks)

### Slice 3A: Interactive Editor Onboarding
- `apps/web/src/editor/useOnboardingTour.ts` — 5-step tour state hook with localStorage persistence
- `apps/web/src/editor/OnboardingTour.tsx` — Spotlight overlay with CSS clip-path cutouts, DOM-rect positioning, ResizeObserver repositioning
- `apps/web/src/editor/OnboardingTour.css` — Tooltip styles
- `data-tour-id` anchors on Canvas, NodePalette, Toolbar, Run button, AI Draft button
- Tests: 16 passed + 1 skipped (RAF-dependent in jsdom)

### Slice 3B: Visual AI Diff in Draft Modal
- `apps/web/src/editor/WorkflowDiffView.tsx` — `GraphDiffView` component accepting plain `WorkflowGraph` objects (alternative to version-based props)
- `apps/web/src/AiDraftModal.tsx` — Summary/Visual Diff tabs, tab-aware layout
- `apps/web/src/EditorPage.tsx` — `applyWithFilters()` for per-node accept/reject, `handleToggleRejectAiNode`, `rejectedAiNodeIds` state
- Reuses existing `diffWorkflowGraphs`, `DiffSummaryBar`, `NodeParamDiffPanel`, `DiffNode`, `DiffEdge`
- Tests: 4 passed + ResizeObserver polyfill in test setup

### Slice 3C: MCP Client — External Tools as Canvas Nodes
- **Backend:**
  - `apps/api/app/models.py` — `MCPConnection` ORM model (id, org_id, name, url, transport, auth_type, auth_secret Fernet-encrypted, headers JSONB, tool_cache JSONB)
  - `apps/api/alembic/versions/0074_mcp_connections.py` — migration with RLS
  - `apps/api/app/services/mcp_client.py` — `discover_tools()`, `call_tool()`, `_load_conn_with_secret()`, `mcp_tool_to_node_manifest()`, header blocklist, unique JSON-RPC IDs, SSRF guard
  - `apps/api/app/routers/mcp_connections.py` — CRUD + sync + tools endpoints, auth_secret encryption on write, redaction on read
  - `packages/nodes/noodle_nodes/mcp_tool.py` — `@node` registered type using `RuntimeContext.call_mcp_tool()` platform hook
  - `packages/core/noodle/engine/types.py` — `call_mcp_tool` ContextVar-based callback
  - `apps/api/app/services/runner.py` — `call_mcp_tool` implementation on `_NodeRuntimeContext`
  - Registered in `packages/nodes/noodle_nodes/__init__.py`
- **Frontend:**
  - `apps/web/src/settings/McpConnectionsPage.tsx` — CRUD modals, Sync Tools button
  - `apps/web/src/editor/nodes/McpToolNode.tsx` — Custom canvas node with plug icon
  - `apps/web/src/editor/NodePalette.tsx` — MCP category, synthetic manifests from tool_cache
  - `apps/web/src/api.ts` — 7 MCP API methods
  - Route: `/settings/mcp-connections`

### Slice 3D: Typed Code Node I/O
- `packages/core/noodle/engine/types.py` — `NodeValidationError` (extends NodeError), `ValidationWarning` dataclass
- `packages/core/noodle/engine/node_exec.py` — `_sanitize_schema()` strips `$ref`/`$schema`/`$id`, pre/post execution validation via jsonschema
- `packages/core/noodle/engine/validation.py` — `_infer_schema_port_kinds()`, `_check_schema_compatibility()`, updated `validate_graph()` returning warnings
- `packages/core/pyproject.toml` — `jsonschema>=4.23`
- `apps/web/src/editor/fields/CodeNodeSchemaEditor.tsx` — Schema builder with property add/remove, type selector, required checkbox, raw JSON toggle
- `apps/web/src/editor/NodeDetails.tsx` — Schema tab for code nodes
- Tests: 43 passed (28 core + 13 API)

### Slice 3E: AI-Generated Custom Typed Nodes
- **Backend:**
  - `apps/api/app/services/ai_builder.py` — `generate_custom_node()` with `_call_llm_simple()`, `_parse_and_validate_node_code()` with AST validation, blocked imports detection, retry-once, deterministic fallback template
  - `apps/api/app/routers/code_modules.py` — `POST /code-modules/generate-node`, `metadata.ai_generated = true` on CodeModule
  - `apps/api/app/schemas.py` — `GenerateNodeRequest`, `GenerateNodeResponse`
- **Frontend:**
  - `apps/web/src/editor/GenerateNodeModal.tsx` — Description textarea, scope selector, Monaco code display, Regenerate/Save buttons
  - `apps/web/src/editor/NodePalette.tsx` — "AI Generated" category with sparkle badge, "+ Generate Node" button
  - `apps/web/src/EditorPage.tsx` — GenerateNodeModal integration
- Tests: 30 passed

---

## MS4: Enterprise & Scale (8 weeks)

### Slice 4A: SAML/OIDC SSO
- **Backend:**
  - `apps/api/app/models.py` — `SSOConfig` ORM model (protocol, client_id, client_secret Fernet-encrypted, discovery_url, idp_entity_id, idp_sso_url, idp_certificate, email_domain unique partial index, attribute_map JSONB, jit_provisioning), `User.sso_subject`
  - `apps/api/alembic/versions/0077_sso_configs.py` — migration with RLS
  - `apps/api/app/services/sso.py` — OIDC authorization-code flow (Redis state+nonce, 600s TTL, GETDEL atomic consumption), discovery document caching (1h, asyncio lock), ID token validation (authlib RS256/ES256, issuer+audience+nonce checks), `follow_redirects=False` on all external HTTP, SSRF guard on discovery/JWKS/token endpoints, JIT provisioning
  - `apps/api/app/routers/auth.py` — `GET /auth/sso/start`, `GET /auth/sso/callback`, `GET /auth/sso/detect`, `GET /auth/sso/metadata`, `POST /auth/sso/acs` with signxml signature validation, decompression bomb protection
  - `apps/api/app/routers/admin.py` — SSO config CRUD + test endpoint with SSRF guards, `Feature.SSO` gate on all routes
- **Frontend:**
  - `apps/web/src/settings/SSOSettingsPage.tsx` — Protocol selector, OIDC/SAML forms, Test Connection
  - `apps/web/src/LoginPage.tsx` — Debounced SSO detection, "Sign in with SSO" button
  - Route: `/settings/sso`
- Dependencies: `authlib>=1.3`, `python3-saml>=1.16`
- Tests: 14 passed (8 OIDC + 6 SAML)

### Slice 4B: Advanced RBAC + Audit Logs
- **Backend:**
  - `apps/api/app/models.py` — `CustomRole` (id, org_id, name, permissions JSONB), `Membership.custom_role_id` FK (ON DELETE SET NULL)
  - `apps/api/alembic/versions/0075_custom_roles.py` — migration with RLS (GUC-based predicate)
  - `apps/api/app/security.py` — `CUSTOM_ROLE_PERMISSION_REGISTRY` (10 perms), built-in role maps, `validate_custom_role_permissions()`, `require_role()` custom-role branch (replaces, not additive), `admin:*`/`node_registry:install` excluded from custom roles
  - `apps/api/alembic/versions/0076_audit_log_actor.py` — `session_id TEXT`, `actor_type TEXT DEFAULT 'user'` on audit_events, index on session_id
  - `apps/api/app/routers/admin.py` — Custom role CRUD, audit log viewer (filterable + CSV export capped at 10K), `Feature.ADVANCED_RBAC` and `Feature.AUDIT_LOGS` gates
  - `apps/api/app/config.py` — `audit_log_retention_days=90`, nightly purge in retention loop
- **Frontend:**
  - `apps/web/src/settings/RolesPage.tsx` — Create/edit custom roles with checkbox grid
  - `apps/web/src/settings/AuditLogPage.tsx` — Filterable table, CSV export via direct fetch
  - Routes: `/settings/roles`, `/settings/audit-log`
- Tests: 15 passed

### Slice 4C: External KMS
- **Backend:**
  - `apps/api/app/services/kms/base.py` — `KMSProvider` ABC (encrypt/decrypt/health_check)
  - `apps/api/app/services/kms/env_kms.py` — Wraps existing Fernet crypto
  - `apps/api/app/services/kms/vault.py` — HashiCorp Vault Transit, aclose() lifecycle
  - `apps/api/app/services/kms/aws_kms.py` — boto3 with asyncio.to_thread, client injection for testing
  - `apps/api/app/services/kms/gcp_kms.py` — google-cloud-kms with asyncio.to_thread
  - `apps/api/app/services/kms/__init__.py` — `get_kms_provider()` singleton factory
  - `apps/api/app/services/org_keys.py` — Refactored to use `get_kms_provider()`
  - `apps/api/app/config.py` — 8 KMS settings (provider, vault_*, aws_*, gcp_*)
  - `scripts/migrate_kms.py` — Idempotent re-encryption script
- **Frontend:**
  - `apps/web/src/settings/KMSSettingsPage.tsx` — Provider selector + config fields
- Feature gate: `Feature.EXTERNAL_KMS`
- Tests: 23 passed (Env/Vault/AWS/GCP + factory + org_keys + migration)

### Slice 4D: Real-Time Agentic Build Loop
- **Backend:**
  - `apps/api/app/services/agentic_builder.py` — `run_agentic_build_loop()` (max 5 iterations), `_get_draft_graph()`, `_save_draft_graph()`, `_ai_draft()`, `_ai_fix()`, `_start_test_run()`, `_wait_for_run()`, `_extract_failures()`, cancel_event propagation, org_id scoping
  - `apps/api/app/routers/agentic_build.py` — `POST /workflows/{id}/agentic-build` SSE streaming, per-user rate limiting (20/hr), 30s keepalive, `X-Accel-Buffering: no`
- **Frontend:**
  - `apps/web/src/editor/AgenticBuildPanel.tsx` — Goal textarea, test data editor, live progress (iteration counter, status badge, event log), Accept/Cancel, SSE cleanup on unmount, ref-based iteration tracking
- Tests: 7 passed

### Slice 4E: Community Node Registry
- **Backend:**
  - `apps/api/app/routers/node_registry.py` — Search, get package, async install (202 → background venv rebuild → Redis status tracking), `node_registry:install` permission (admin-only)
  - `apps/api/app/config.py` — `allow_registry`, `registry_index_url`
- **Frontend:**
  - `apps/web/src/settings/NodeRegistryPage.tsx` — Browse/Installed tabs, search, package cards, environment selector modal
  - Route: `/settings/node-registry`
- **Docs:** `docs/community-nodes.md` — Developer guide for publishing
- Tests: 12 passed

---

## Security Hardening

### Applied across all slices:
- **SSRF protection:** `assert_public_http_url()` on all external URL fetches (MCP, OIDC discovery, JWKS, token endpoints, SSO test)
- **Redirect hardening:** `follow_redirects=False` on all OIDC HTTP calls
- **Header blocklist:** Dangerous headers filtered on MCP outbound requests
- **SAML security:** XML-DSig signature verification via signxml, decompression bomb protection, Conditions/Audience/Destination enforcement
- **OIDC security:** Issuer validation, audience validation, nonce verification, atomic state consumption (GETDEL)
- **Encryption:** Fernet (single-field) for SSO secrets and MCP auth, DEK-wrapping for credentials, org KEK with KMS providers
- **RLS:** All new tenant-data tables have Row Level Security policies (GUC-based predicates)
- **Feature gates:** All Enterprise features gated via `require_feature()` (SSO, ADVANCED_RBAC, AUDIT_LOGS, EXTERNAL_KMS, DEDICATED_POOLS)
- **Rate limiting:** Per-user on agentic build, per-IP on auth endpoints
- **Input validation:** AST sandbox for AI-generated code, blocked imports enforcement, schema sanitization ($ref/$schema/$id stripping)

---

## Review History

1. **Pass 1:** 4 parallel reviewers (security, backend, frontend, infrastructure) — 43 findings, all fixed
2. **Pass 2:** 2 parallel reviewers (deep security audit, cross-cutting integration) — 10 findings, all fixed
3. **Pass 3:** Final 10/10 push — 12 issues including pre-existing gaps, all fixed

**Final: 76 test files, 439 passed, 1 skipped. TypeScript clean.**

---

## Branch Info

- **Branch:** `feat/ms4-enterprise-scale`
- **Base:** `4fd1c10` (fix/backend-production-readiness-p0-p1)
- **Commits:** 29
- **Files changed:** 119 (+22,691 / -216)
- **New migrations:** 0074 (mcp_connections), 0075 (custom_roles), 0076 (audit_log_actor), 0077 (sso_configs)
- **New Python deps:** jsonschema>=4.23, authlib>=1.3, python3-saml>=1.16, boto3 (optional), google-cloud-kms (optional)
