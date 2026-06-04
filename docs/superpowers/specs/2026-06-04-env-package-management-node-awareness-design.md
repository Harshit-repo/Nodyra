# Env package management + node↔env package awareness

**Date:** 2026-06-04
**Status:** Approved design, pending implementation plan

## Problem

The Environments page renders every package of an env as a removable chip directly
inside the env card, plus a single-package text input. A package-heavy env bloats
the card, and adding packages one at a time is tedious. There is also no link between
the nodes a user places in a workflow and the packages those nodes need: nodes today
just `import` lazily and raise a runtime `ImportError` with a helpful message if the
package is missing from the workflow's environment. The user only discovers the gap
when a run fails.

Two goals:

1. **Better env package UX** — move package management off the card into a dedicated
   drawer, support comma-separated multi-add, and support importing a `requirements.txt`.
2. **Node ↔ env package awareness** — let nodes declare the pip packages they need,
   detect when a node's package is missing from its workflow's env, surface it in the
   editor, block runs that would fail, and track which nodes depend on which packages
   so the env drawer can show and protect them.

## Decisions (locked during brainstorming)

- **Scope:** one combined spec covering env-page UX and node-awareness.
- **Detection surface:** both a live banner in the editor *and* a pre-run preflight.
- **Runtime behavior:** the preflight **blocks** the run with a clear message (fail
  fast before workers spawn), rather than relying on the lazy `ImportError`.
- **Node-dependency tags:** included in v1 (reverse-tracking which workflows/nodes
  use an env).
- **requirements.txt import:** diff preview with **merge as the default** (new
  packages added, nothing removed unless the user opts in per-removal); removals of
  node-required packages are flagged.
- **Env card layout:** option C — the card shows only the package **count**; clicking
  the Packages tile opens a right-side drawer with the full manager.

## Key facts about the current codebase

- `NodeManifest` (`packages/core/noodle/models.py:112`) is the single source the editor
  palette reads via `GET /nodes` (`apps/api/app/routers/nodes.py`). Adding a field here
  propagates to the editor with no new endpoint.
- The `@node` decorator (`packages/core/noodle/sdk.py:793`) builds the manifest via
  `_build_manifest`.
- integration_v2 nodes are generated from declarative specs
  (`packages/nodes/noodle_nodes/integrations_v2/specs.py`,
  `node_factory.py`). They import **only stdlib + noodle core** (HTTP via the shared
  core client), so they declare **no** extra requirements and never trigger a prompt —
  exactly the "don't pre-bloat the env" behavior wanted.
- Nodes that *do* need extra packages import them lazily and raise a helpful
  `RuntimeError`/`ImportError`: `datasets`→`duckdb`, `ml`→`scikit-learn`/`joblib`,
  `charts` PNG→`cairosvg`, plus a few in `cloud_devops` / `integrations`.
- `Workflow.environment_id` (`apps/api/app/models.py:170`) binds a workflow to an env
  (nullable → falls back to the global env). This is how the editor and preflight know
  which env to compare against.
- The env page is `apps/web/src/EnvironmentsPage.tsx`. Package APIs are
  `addPackage` / `removePackage` / `rebuildEnvironment` in `apps/web/src/api.ts`,
  backed by `apps/api/app/routers/environments.py`. `addPackage` currently appends one
  package and sets status `pending` → a background `build_environment` rebuild.

## Architecture

### A. Node requirements model

- Add `requirements: list[str]` to `NodeManifest` (default empty).
- Add a `requirements=[...]` kwarg to the `@node` decorator, threaded through
  `_build_manifest` into the manifest.
- Add `requirements` to the integration_v2 spec (provider level, since a provider's
  operations share dependencies) → threaded through `node_factory.py`. Defaults empty.
- Backfill heavy built-in nodes with their pip specifiers:
  - `datasets` → `duckdb`
  - `ml` → `scikit-learn`, `joblib`, `pandas`
  - `charts` (PNG export) → `cairosvg`
  - audit `cloud_devops` and `integrations` for any hard deps and declare them.
- Strings are pip specifiers (e.g. `"duckdb>=0.9"`). All "is it present" comparisons
  use a shared **canonicalization** helper (PEP 503: lowercase, collapse `-`/`_`/`.`),
  ignoring version pins and extras.
- **Optional** dependencies (a package only needed for some param combinations, e.g.
  `cairosvg` only for PNG output) are declared only when they are a hard requirement
  for the node to function. Genuinely optional features keep their lazy import +
  runtime error in v1 rather than forcing an install.

### B. Editor live banner

- The editor already has node manifests and the workflow's `environment_id`. It fetches
  that env (`GET /environments/{id}`) for its `packages`, and computes
  `missing = requirements whose canonical name is absent`.
- Surfaces:
  - a small warning badge on the canvas node card;
  - a banner in the NDV listing the missing packages, with two fix actions:
    - **Add to `<env>`** → bulk-add the missing packages + rebuild. Env transitions to
      "building"; the existing env poll reflects progress.
    - **Switch environment…** → a picker of environments, highlighting those that
      already satisfy the requirement; selection sets `workflow.environment_id`.

### C. Pre-run preflight (blocking)

- Before a run spawns workers (in the run path — `apps/api/app/services/runner.py` /
  the run router), gather all node types in the graph → their manifest requirements →
  compare to the resolved env's `packages` (canonicalized).
- If any are missing, **fail fast** with a structured error: the missing packages, the
  nodes that need them, and a hint pointing to the fix. This runs before today's lazy
  `ImportError` can fire.
- Env-status note: if the env is still `building` but the required packages are already
  in its list, allow (the rebuild will finish). If a required package is absent from the
  list, block regardless of status.

### D. Package drawer + requirements.txt import (env page)

- `EnvCard` loses its inline chip list and inline add-input. The **Packages tile
  becomes a button** that opens a right-side `PackageDrawer` over a dimmed grid.
- Drawer contents:
  - **Add packages** — one comma-separated input (`pandas, numpy==2.1, httpx>=0.27`),
    version pins preserved.
  - **Import requirements.txt** — drag-drop or browse → parse → **diff preview**
    (`+N to add · M already present · K installed but not in file`). Removals are
    **unchecked by default** (merge semantics); checking a removal that a node depends
    on shows a warning. Confirm applies.
  - **Installed list** — searchable/filterable, each package removable.
- API:
  - Bulk `PUT /environments/{id}/packages` with `{ packages: [...] }` — sets the full
    desired list, validates + dedups (canonicalized), sets status `pending`, triggers
    `build_environment`. Comma-add and import confirm both funnel through this.
  - Keep `DELETE /environments/{id}/packages/{package}` for single-chip removal.
  - requirements.txt parsing: parse client-side for the preview (skip comments, blank
    lines, `-r`/`-c` includes, and environment markers); apply via the bulk `PUT`.

### E. Node-dependency tags (reverse tracking)

- New `GET /environments/{id}/package-usage` → for every workflow bound to this env
  (plus null-env workflows when this is the global env), scan node graphs (draft +
  published versions), map each node `type` → manifest `requirements`, and return
  `package (canonical) → [{ workflow_id, workflow_name, node_id, node_label }]`.
- The drawer uses this to:
  - render green **"node: …"** tags on packages that a node depends on;
  - **warn before removing** a package some node needs;
  - optionally show a **"Required by nodes but not installed"** section with an
    **Add all** action.

### Cross-cutting

- A single **canonicalization** helper (PEP 503 name normalization + specifier parsing
  to project name) shared by the editor comparison, the preflight, the bulk-PUT
  dedup, and the usage scan. Lives in core so both API and node packages can use it.

## Data flow

- `NodeManifest.requirements`: node defs → registry → `GET /nodes` → editor palette.
- Editor banner: `workflow.environment_id` → `GET /environments/{id}.packages` →
  compare with node `requirements` → badge/banner + fix actions.
- Preflight: run request → server gathers graph node types → registry requirements →
  env packages → block if any missing.
- Drawer: `GET /environments/{id}` (packages) + `GET /environments/{id}/package-usage`
  (node tags) → render list, tags, removal warnings, diff import.

## Error handling

- All name comparisons go through the shared canonicalization helper; version pins and
  extras are ignored for presence checks but preserved in stored specifiers.
- Bulk `PUT` dedups by canonical name; when two specifiers collide, the later one
  (e.g. the imported file's pin) wins.
- Adding/importing packages triggers a rebuild → env temporarily not `ready`; the
  existing Environments-page poll handles the transition.
- Preflight returns a structured, user-readable error rather than a stack trace.

## Testing

- **Core/SDK:** `requirements` threading through the `@node` decorator and
  `_build_manifest`; canonicalization helper unit tests.
- **Nodes:** integration_v2 factory carries `requirements` (default empty); backfilled
  heavy nodes expose the expected specifiers in their manifests.
- **API:** bulk `PUT /environments/{id}/packages` (set/dedup/rebuild); requirements.txt
  diff behavior; `GET /environments/{id}/package-usage`; preflight rejects a run whose
  node needs a missing package and allows one whose packages are present (or building).
- **Frontend:** drawer renders list + tags; comma-add parsing; requirements.txt diff
  preview with merge default and removal warnings; missing-package banner in the NDV
  with both fix actions.

## Out of scope (v1)

- Auto-installing genuinely optional dependencies (e.g. `cairosvg` for PNG-only export)
  — those keep their lazy runtime error.
- Static import-analysis to infer requirements — rejected in favor of explicit
  declarations.
- Per-deployment / per-schedule env overrides in the usage scan beyond the
  workflow→env binding (can be added later if needed).
