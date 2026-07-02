# Nodyra Master Roadmap — Audit Fixes, Rename, CLI, P1 Product

> **For agentic workers:** This is the index/sequencing document. Each phase below is a self-contained plan file — execute them **one phase at a time** with superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Do not start a phase before its prerequisite merges.

**Goal:** Take the platform from "beta-ready under a working name" to a launch-shaped product named **Nodyra**: green and protected `main`, all audit bugs closed, a publishable CLI/SDK, and the P0/P1 product items from the 2026-07-02 audit shipped.

**Source assessment:** `docs/audits/2026-07-02-fable5-full-audit.md` (moved there by Phase 1 Task 1; currently at repo root as `FABLE5_NOODLE_FULL_AUDIT_OPTIMIZATION_AND_PRODUCT_IDEAS.md`).

---

## Phase sequence

| # | Plan file | Delivers | Prereq | Size |
|---|---|---|---|---|
| 1 | [2026-07-02-phase1-stabilize.md](2026-07-02-phase1-stabilize.md) | Audit fixes committed; BUG-6/7/8/10 + checkpoint debounce closed; soak test run; `main` protected; repo hygiene | — | ~10 tasks |
| 2 | [2026-07-02-phase2-nodyra-rename.md](2026-07-02-phase2-nodyra-rename.md) | Full Noodle→Nodyra rename with executable gate, env-var fallbacks, upgrade guide | Phase 1 merged | ~8 tasks, ~800 files |
| 3 | [2026-07-02-phase3-nodyra-cli-v1.md](2026-07-02-phase3-nodyra-cli-v1.md) | `nodyra-client` v1: rich/`--json` output, `run watch`, import/export, auth UX, docs, PyPI release lane | Phase 2 merged | ~9 tasks |
| 4 | [2026-07-02-phase4-product-p1.md](2026-07-02-phase4-product-p1.md) | MCP quickstart (P0 marketing), AI-builder vocabulary, replay-from-node UI, MCP trace, template gallery, per-workflow requirements, HuggingFace provider, GitOps docs | Phase 2 merged (tasks independent of Phase 3) | 8 independent tasks |

Ordering rationale: fixes land under the old names (they're already in the working tree); the rename happens before any new feature code so nothing new is written under the dead brand; CLI and product work then build on Nodyra names. Phase 4 tasks are mutually independent — they can be parallelized across worktrees or interleaved with Phase 3.

## How to execute (agents, skills, models)

- **Per phase:** `superpowers:subagent-driven-development` — fresh subagent per task, review between tasks. Tasks in these plans are written to be executable by an inexpensive model (Sonnet-class): exact paths, complete code, verification commands. Where a plan says "mirror/adapt to the file's existing pattern," that is the task — read the named reference first, then write.
- **Branches:** one branch per phase (`phase1-stabilize`, `phase2-nodyra-rename`, `phase3-cli-v1`); Phase 4 = one branch per task. PR to `main`; CI green is the merge gate (enforced by Phase 1 Task 9).
- **After each task:** run the plan's verification steps; after each phase, run `/code-review` (medium) on the branch diff before the PR.
- **Runtime-facing changes** (Phase 3 CLI, Phase 4 UI tasks): run the `verify` skill against the live dev stack before calling the task done — tests passing is necessary, not sufficient.
- **Lookups during execution:** use the `Explore` agent (or `codebase-memory-mcp` search tools) rather than loading whole files into the implementing agent's context.

## Decision log (locked — do not relitigate during execution)

| Decision | Rationale |
|---|---|
| Full rename incl. Python packages, Redis keys, MCP URIs | Pre-public-release is the only cheap moment; partial renames rot |
| Keep `ndpat_` token prefix | Brand-neutral, avoids invalidating every issued token |
| Keep DB table names + alembic history | Zero user value in schema churn |
| `NOODLE_*` env vars: one-release fallback, then removed | Existing self-hosted installs (Harry's) keep working |
| Cookie rename logs users out once; Redis prefix change requires queue drain | Documented in `docs/upgrading-to-nodyra.md` |
| CLI keeps deps to httpx/click/pydantic/rich | Install weight is part of the product |
| CLI watch = polling `GET /runs/{id}` | No REST events endpoint exists; WS streaming is a later enhancement |
| Templates ship as repo JSON, not a DB table | Curated set, versioned with code; user-shared templates are a later feature |
| AI-builder allows all manifest types except `UNCONDITIONAL_UNSAFE − {code}` | Manifest-driven validation already guards structure; activation gates guard risk |
| Messaging nodes (telegram/discord/teams) NOT rebuilt | Audit wishlist was stale — v2 providers already exist; Task 2 of Phase 4 makes them buildable |

## Backlog — P2/P3 (each needs brainstorm → spec → plan before execution)

Do **not** start these from this document; each gets its own spec cycle (superpowers brainstorming) when scheduled.

**P2 — differentiators**
- **MCP discovery UI**: browse a connected server's tools in the editor, drag one to the canvas as a preconfigured `mcp_tool` node. Done when: connect → browse → drop → run works without leaving the editor.
- **Fix-failed-run AI loop**: button on a failed run feeding error + graph to the builder, producing a patch proposal with a per-change diff. Done when: a seeded failure is repaired end-to-end with user approval of the diff.
- **AI test generation**: pin a run's inputs → generate assertion pins → store as regression checks per workflow.
- **AI explain-workflow panel** (S): natural-language summary of any graph.
- **Internal builder rebased on the MCP tool layer**: one agent surface, two frontends.
- **DataFrame preview on wires**: inline schema/sample preview for tabular outputs in the editor.
- **Custom node publish flow**: code module → shared palette entry with review step.
- **Run comparison view**: side-by-side node statuses/outputs of two runs.
- **Approval gates + side-effect metadata for dynamic MCP workflow tools**; **org-level MCP server allowlist policy** (hosted-mode requirements).
- **Event-ordering follow-up**: monotonic sequence numbers stamped at publish (client-sortable) if the Phase 1 chaining fix proves insufficient under multi-replica load.

**P3 — enterprise/future**
- SCIM provisioning; per-org usage analytics dashboard; credential sharing policies; notebook import/export round-trip; workflow health score / production checklist; MCP server marketplace; queue sharding (explicitly premature today).

## Manual checklist (Harry — not agent-executable)

1. **Before Phase 2 merge:** rename GitHub repos `noodle`→`nodyra` and `noodle-registry`→`nodyra-registry` (redirects preserve old URLs).
2. **Before Phase 3 release:** register PyPI project `nodyra-client` (and reserve `nodyra`); configure PyPI trusted publishing for `release-client.yml`, environment `pypi`.
3. Phase 1 Task 9 branch protection needs repo admin; run it yourself if the agent lacks `gh` auth.
4. After Phase 2: rename `D:\noodle` → `D:\nodyra` locally when convenient (note: Claude Code project memory is keyed by directory path; the index resets under the new path).
5. Licensing decision from the release plan (Sustainable Use License vs Apache-2.0 for the client package) — Phase 3 Task 1 reads the root LICENSE and follows it; make sure the root LICENSE says what you intend **before** publishing to PyPI.
6. Any deployed instance: apply `docs/upgrading-to-nodyra.md` during the Phase 2 rollout window.

## Definition of done (whole roadmap)

- `main` protected; full backend suite, web suite, e2e lane green; rename gate (`scripts/check_rename.py`) in CI and passing.
- `pip install nodyra-client` works from PyPI; `nodyra run start --watch` drives a real workflow from a terminal.
- A fresh user can: land on the README → connect Claude via MCP in one paste → have a workflow built for them → start from a template themselves → replay a failed node from the UI.
- Soak test script exists and has passed once against the compose stack (worker kill + cancel storm, zero stuck runs).
