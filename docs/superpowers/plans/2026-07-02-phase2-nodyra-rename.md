# Phase 2 — Rename Noodle → Nodyra (Full Product Rename)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the product from Noodle to Nodyra everywhere — Python packages, imports, env vars, Redis keys, cookies, MCP URIs, web UI, infra, docs — leaving zero user-visible "Noodle" and a green test suite.

**Architecture:** One mechanical case-preserving text sweep (script-driven, so it is reviewable and re-runnable) followed by `git mv` of the six Python package directories, then targeted follow-ups that a text sweep cannot do (env-var fallbacks, localStorage migration, lockfile regen). A guard script makes "no noodle left" an executable gate.

**Tech Stack:** Python (sweep scripts), uv workspace, alembic, Vite/React.

**Parent plan:** `docs/superpowers/plans/2026-07-02-nodyra-master-roadmap.md`
**Prerequisite:** Phase 1 complete and committed (clean-ish tree; only known in-flight work unstaged).

## Global Constraints

- New brand: **Nodyra** (`nodyra` / `Nodyra` / `NODYRA` — case-preserving mapping from `noodle`/`Noodle`/`NOODLE`).
- Python packages: `noodle`→`nodyra` (core), `noodle_nodes`→`nodyra_nodes`, `noodle_runtime`→`nodyra_runtime`, `noodle_exporter`→`nodyra_exporter`, `noodle_importer`→`nodyra_importer`, `noodle_runner_agent`→`nodyra_runner_agent`. Dist names `noodle-*`→`nodyra-*`. `packages/client` is already `nodyra-client`.
- **Keep unchanged:** `ndpat_` token prefix (already brand-neutral), database table names, alembic revision IDs/filenames, historical docs under `docs/audits/` and `docs/superpowers/` (they may say "Noodle" — they are records, exclude them from sweep and guard).
- Env vars: new `NODYRA_*` names are canonical; every reader falls back to the old `NOODLE_*` name for one release.
- Accepted breaking changes (pre-public-release): session/CSRF cookie names (users re-login), Redis key prefixes (drain in-flight queue before deploying), sandbox network name, vault transit key name, MCP resource URIs `noodle://`→`nodyra://`.
- Work on a dedicated branch `phase2-nodyra-rename`; commit after every task; full suite must be green at the end of the phase (guard: Task 8).
- Never `git add -A` — the tree may still hold unrelated in-flight work.
- Every commit message ends with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: Guard script — make "no noodle left" executable

**Files:**
- Create: `scripts/check_rename.py`

**Interfaces:**
- Produces: `python scripts/check_rename.py` → exit 0 when no disallowed `noodle` remains; exit 1 listing `path:line: text` otherwise. Task 8 and CI use it as the gate.

- [ ] **Step 1: Write the script:**

```python
"""Rename gate: fail if any tracked file still says 'noodle' (any case).

Historical records (audits, plans, changelog) and this script family are
exempt. Run: python scripts/check_rename.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

EXEMPT_PREFIXES = (
    "docs/audits/",
    "docs/superpowers/",
    "scripts/check_rename.py",
    "scripts/rename_to_nodyra.py",
    "CHANGELOG.md",
)
TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".css", ".html", ".md", ".toml",
    ".yml", ".yaml", ".json", ".cfg", ".ini", ".txt", ".env", ".example",
    ".sh", ".ps1", ".sql", ".mako", ".dockerfile", "",
}
PATTERN = re.compile(r"noodle", re.IGNORECASE)
EXEMPT_LINES = {
    # Former-name SEO/support note intentionally retained after the rename.
    ("README.md", "previously developed under the working name"),
}


def main() -> int:
    files = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    hits: list[str] = []
    for rel in files:
        if rel.startswith(EXEMPT_PREFIXES):
            continue
        p = Path(rel)
        if p.suffix.lower() not in TEXT_SUFFIXES and p.name != "Dockerfile":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                if any(rel == p and marker.lower() in line.lower() for p, marker in EXEMPT_LINES):
                    continue
                hits.append(f"{rel}:{i}: {line.strip()[:120]}")
    if hits:
        print(f"{len(hits)} 'noodle' occurrence(s) remain:")
        print("\n".join(hits[:200]))
        return 1
    print("clean: no 'noodle' outside exempt paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it to capture the baseline**

Run: `uv run python scripts/check_rename.py; echo "exit=$?"`
Expected: exit 1 with hundreds of hits (~790 files contain the string today). Save the count — Task 8 drives it to 0.

- [ ] **Step 3: Commit**

```bash
git add scripts/check_rename.py
git commit -m "chore(rename): add executable no-noodle-left gate

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Mechanical text sweep + package directory moves

This is the big one. Sweep first (contents), then `git mv` (paths), then regenerate the lockfile, then run everything.

**Files:**
- Create: `scripts/rename_to_nodyra.py`
- Modify: ~790 tracked text files (mechanical)
- Rename: `packages/core/noodle/` → `packages/core/nodyra/`, `packages/nodes/noodle_nodes/` → `packages/nodes/nodyra_nodes/`, `packages/runtime/noodle_runtime/` → `packages/runtime/nodyra_runtime/`, `packages/exporter/noodle_exporter/` → `packages/exporter/nodyra_exporter/`, `packages/importer/noodle_importer/` → `packages/importer/nodyra_importer/`, `packages/runner/noodle_runner_agent/` → `packages/runner/nodyra_runner_agent/`

- [ ] **Step 1: Verify the inner package directory names** (they must exist exactly as listed above):

Run: `ls packages/core packages/runtime packages/exporter packages/importer packages/runner packages/nodes | cat`
Expected: each contains its `noodle*` import package. Adjust Task file list if any name differs.

- [ ] **Step 2: Check alembic migrations for embedded brand strings** (we do NOT rename migration files or revision ids, but literal `noodle` strings *inside* migration bodies — e.g. seeded defaults — will be swept; verify nothing breaks semantically):

Run: `grep -rn -i "noodle" apps/api/alembic/versions | grep -v -i "revision"`
Review each hit: comments and docstrings are fine to sweep; a seeded *data value* (e.g. a default org name inserted into a table) must keep working — if found, note it and exclude that file via the sweep script's exempt list, handling it manually.

- [ ] **Step 3: Write `scripts/rename_to_nodyra.py`:**

```python
"""One-shot case-preserving sweep: noodle->nodyra in all tracked text files.

Skips historical records and binary-ish files. Idempotent. Run once, review
with git diff, then git mv the package directories (paths are NOT renamed
here).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

EXEMPT_PREFIXES = (
    "docs/audits/",
    "docs/superpowers/",
    "scripts/check_rename.py",
    "scripts/rename_to_nodyra.py",
    "CHANGELOG.md",
    "uv.lock",           # regenerated by `uv lock`, never hand-edited
    "package-lock.json", # ditto (npm)
)
TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".css", ".html", ".md", ".toml",
    ".yml", ".yaml", ".json", ".cfg", ".ini", ".txt", ".env", ".example",
    ".sh", ".ps1", ".sql", ".mako",
}
# Plain substring replacement, longest-case-first is unnecessary: the three
# case variants are disjoint. Substring (not word-boundary) is deliberate so
# compounds like noodle_nodes, noodle-sandbox, noodle://, noodle:queue all
# rename in one pass.
REPLACEMENTS = [("noodle", "nodyra"), ("Noodle", "Nodyra"), ("NOODLE", "NODYRA")]


def main() -> None:
    files = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    changed = 0
    for rel in files:
        if rel.startswith(EXEMPT_PREFIXES):
            continue
        p = Path(rel)
        if p.suffix.lower() not in TEXT_SUFFIXES and p.name != "Dockerfile":
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = text
        for old, repl in REPLACEMENTS:
            new = new.replace(old, repl)
        if new != text:
            p.write_text(new, encoding="utf-8", newline="")
            changed += 1
    print(f"rewrote {changed} files")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the sweep, then move the directories:**

```bash
uv run python scripts/rename_to_nodyra.py
git mv packages/core/noodle packages/core/nodyra
git mv packages/nodes/noodle_nodes packages/nodes/nodyra_nodes
git mv packages/runtime/noodle_runtime packages/runtime/nodyra_runtime
git mv packages/exporter/noodle_exporter packages/exporter/nodyra_exporter
git mv packages/importer/noodle_importer packages/importer/nodyra_importer
git mv packages/runner/noodle_runner_agent packages/runner/nodyra_runner_agent
```

- [ ] **Step 5: Regenerate the workspace lockfile and reinstall:**

```bash
uv lock
uv sync --all-packages
```

Expected: lock succeeds with the new `nodyra-*` dist names (the sweep already rewrote every `pyproject.toml` `name =` and dependency entry, plus `[tool.hatch.build.targets.wheel] packages = [...]` lists and the root `known-first-party`).

- [ ] **Step 6: Spot-check the diff before testing** (mechanical sweeps deserve eyeballs):

```bash
git diff --stat | tail -5
git diff apps/api/app/config.py | head -80
git diff apps/api/app/services/events.py | head -40
```

Confirm: `database_url` default now `postgresql+asyncpg://nodyra:nodyra@localhost:5432/nodyra`, cookies `nodyra_session`/`nodyra_csrf`, Redis prefixes `nodyra:run:` etc., no mangled identifiers.

- [ ] **Step 7: Full test suites**

Run: `uv run ruff check . && uv run pytest apps/api/tests packages/core/tests packages/nodes/tests packages/runtime/tests packages/exporter/tests packages/importer/tests packages/client/tests -q`
Expected: 0 failures. Then in `apps/web`: `npm run typecheck && npm test -- --run` → clean (the sweep renamed TS identifiers/strings consistently on both sides of the API).

Common failure modes and fixes:
- A test asserts a literal old string (e.g. cookie name) that the sweep also renamed in the assertion — should pass; if a test *loads a fixture file* with a `noodle` path, sweep missed a non-text suffix: rename the fixture reference by hand.
- `ModuleNotFoundError: noodle...` → a dynamic import built from a string concat; grep: `grep -rn "noodle" apps packages --include='*.py' | grep -v docs/` and fix.

- [ ] **Step 8: Commit**

```bash
git add -u
git add scripts/rename_to_nodyra.py uv.lock
git commit -m "refactor!: rename Noodle -> Nodyra across packages, app, web, infra

Mechanical case-preserving sweep + package dir moves:
- python packages: nodyra{,_nodes,_runtime,_exporter,_importer,_runner_agent}
- redis key prefixes nodyra:*, cookies nodyra_session/nodyra_csrf,
  MCP URIs nodyra://, sandbox network nodyra-sandbox
- BREAKING: sessions invalidated (cookie rename); drain run queue before
  deploying (redis key prefix change); NOODLE_* env vars renamed NODYRA_*
  (compat fallbacks added in the next commit)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(`git add -u` is safe here only if Phase 1 left no unrelated modified files; verify with `git status` first — stage explicitly if in doubt.)

---

### Task 3: Env-var compatibility fallbacks

The sweep renamed every env-var *reader* to `NODYRA_*`. Existing deployments still export `NOODLE_*`. Add one-release fallbacks.

**Files:**
- Modify: every `os.environ` reader of a renamed var, `apps/api/app/config.py` alias fields
- Test: `apps/api/tests/test_env_compat.py` (new)

- [ ] **Step 1: Inventory the renamed env vars:**

Run: `grep -rn "NODYRA_" apps packages --include='*.py' | grep -v tests | grep -E "environ|getenv|validation_alias|alias"`
Expected known set (verify, don't assume): `NODYRA_ALLOW_PRIVATE_EGRESS` (in `packages/nodes/nodyra_nodes/http_security.py` and `httpx_security.py`), `NODYRA_LICENSE_KEY` (config.py field alias), possibly sandbox/worker toggles.

- [ ] **Step 2: Write the failing test** `apps/api/tests/test_env_compat.py`:

```python
"""Old NOODLE_* env vars must keep working for one release after the rename."""
from __future__ import annotations


def test_private_egress_honours_legacy_env(monkeypatch) -> None:
    monkeypatch.delenv("NODYRA_ALLOW_PRIVATE_EGRESS", raising=False)
    monkeypatch.setenv("NOODLE_ALLOW_PRIVATE_EGRESS", "1")
    from nodyra_nodes.httpx_security import _private_egress_allowed

    assert _private_egress_allowed() is True


def test_new_env_wins_over_legacy(monkeypatch) -> None:
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "0")
    monkeypatch.setenv("NOODLE_ALLOW_PRIVATE_EGRESS", "1")
    from nodyra_nodes.httpx_security import _private_egress_allowed

    assert _private_egress_allowed() is False
```

Run: `uv run pytest apps/api/tests/test_env_compat.py -v` → Expected: FAIL (legacy var ignored).

- [ ] **Step 3: Implement the fallback pattern.** For plain `os.environ` readers:

```python
def _env_with_legacy(new: str, old: str, default: str = "") -> str:
    """NODYRA_* is canonical; fall back to the pre-rename NOODLE_* name for
    one release so existing deployments don't silently lose the setting."""
    val = os.environ.get(new)
    if val is not None:
        return val
    return os.environ.get(old, default)
```

Apply it at every reader found in Step 1 (e.g. in `httpx_security.py`: `_env_with_legacy("NODYRA_ALLOW_PRIVATE_EGRESS", "NOODLE_ALLOW_PRIVATE_EGRESS")`). Note the new-var check is `is not None`, not truthiness — an explicit `NODYRA_...=0` must override a legacy `NOODLE_...=1` (the second test locks this).

For pydantic-settings fields in `config.py` that use `validation_alias` with an env name (find them: `grep -n "validation_alias\|alias=" apps/api/app/config.py`), use `AliasChoices`:

```python
from pydantic import AliasChoices, Field

    license_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NODYRA_LICENSE_KEY", "NOODLE_LICENSE_KEY"),
    )
```

Also grep the same pattern in `apps/web` (Vite env vars, `import.meta.env.VITE_...`): `grep -rn "VITE_NODYRA\|VITE_NOODLE" apps/web/src` — if any exist, mirror the fallback in the single config module that reads them.

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/api/tests/test_env_compat.py apps/api/tests/test_settings_endpoints.py packages/nodes/tests/test_httpx_security.py -v`
Expected: PASS. Also update `packages/nodes/tests/test_httpx_security.py`'s env test to set the **new** var name (the Phase 1 test used `NOODLE_ALLOW_PRIVATE_EGRESS`; keep one legacy-name test as compat coverage).

- [ ] **Step 5: Commit**

```bash
git add -p apps packages   # or list files explicitly
git commit -m "feat(rename): NOODLE_* env fallbacks for one release

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Web session/token continuity

The sweep renamed the localStorage token key, which would silently log out every browser. Add a one-time migration.

**Files:**
- Modify: `apps/web/src/api.ts` (token storage; `grep -n "nodyra_token" apps/web/src/api.ts` to find it post-sweep)
- Test: the web test file covering token storage (find with `grep -rln "noodle_token\|nodyra_token" apps/web/src --include='*.test.*'`), or add cases to `apps/web/src/api.test.ts` if none exists

- [ ] **Step 1: Write the failing test** (vitest, colocated with existing api tests):

```ts
import { afterEach, describe, expect, it } from "vitest";

describe("token key migration", () => {
  afterEach(() => localStorage.clear());

  it("adopts a legacy noodle_token and removes it", () => {
    localStorage.setItem("noodle_token", "tok-123");
    // getStoredToken is the exported accessor api.ts uses internally;
    // if the current accessor has a different name, test that one.
    expect(getStoredToken()).toBe("tok-123");
    expect(localStorage.getItem("nodyra_token")).toBe("tok-123");
    expect(localStorage.getItem("noodle_token")).toBeNull();
  });

  it("prefers the new key when both exist", () => {
    localStorage.setItem("nodyra_token", "new");
    localStorage.setItem("noodle_token", "old");
    expect(getStoredToken()).toBe("new");
  });
});
```

Run in `apps/web`: `npm test -- --run api` → Expected: FAIL.

- [ ] **Step 2: Implement in `api.ts`** where the token is read (adapt to the existing accessor shape — api.ts already centralises reads; keep its export names):

```ts
const TOKEN_KEY = "nodyra_token";
const LEGACY_TOKEN_KEY = "noodle_token"; // pre-rename key: migrate once, then delete

export function getStoredToken(): string | null {
  const current = localStorage.getItem(TOKEN_KEY);
  if (current) return current;
  const legacy = localStorage.getItem(LEGACY_TOKEN_KEY);
  if (legacy) {
    localStorage.setItem(TOKEN_KEY, legacy);
    localStorage.removeItem(LEGACY_TOKEN_KEY);
  }
  return legacy;
}
```

- [ ] **Step 3: Run web suites**

Run in `apps/web`: `npm run typecheck && npm test -- --run`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src
git commit -m "feat(rename): migrate legacy noodle_token localStorage key

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

Note: the `noodle_session` **cookie** is server-set and httpOnly; renaming it logs SPA users out once. Accepted (Global Constraints) — do not build cookie migration.

---

### Task 5: Infra + deploy sweep verification

The sweep already rewrote compose/Helm/Dockerfiles textually. Verify they actually build and document the operational breaking changes.

**Files:**
- Verify: `deploy/docker-compose.yml`, `deploy/` Helm chart, Dockerfiles, `.env.example`
- Create: `docs/upgrading-to-nodyra.md`

- [ ] **Step 1: Validate compose and images:**

```bash
docker compose -f deploy/docker-compose.yml config -q && echo "compose ok"
docker compose -f deploy/docker-compose.yml build api 2>&1 | tail -5
```

Expected: config valid; the api image builds (this exercises `uv sync --locked` inside Docker against the regenerated lockfile — the highest-risk rename artifact). If Helm exists (`ls deploy | grep -i helm`), run `helm lint deploy/<chart-dir>`.

- [ ] **Step 2: Postgres dev-data note.** The compose default DB/user changed `noodle`→`nodyra`, so a pre-existing dev volume will fail auth. Confirm the volume name (`docker compose -f deploy/docker-compose.yml config --volumes`) and document the reset in Step 3's doc. Do NOT auto-delete anyone's volume.

- [ ] **Step 3: Write `docs/upgrading-to-nodyra.md`:**

```markdown
# Upgrading a Noodle deployment to Nodyra

Nodyra is the new name for Noodle. One-time operational changes:

## Before you upgrade
1. **Drain the run queue.** Redis keys moved from `noodle:*` to `nodyra:*`;
   in-flight queue state is not migrated. Stop enqueuing, let running work
   finish (`GET /ops/queue` shows zero leased/queued), then deploy.
2. Note your `NOODLE_*` environment variables. They still work this release
   (each has a `NODYRA_*` canonical name) but will be removed next release.

## What breaks once
- **Sessions:** the session cookie was renamed (`noodle_session` →
  `nodyra_session`); browser users log in again. API tokens (`ndpat_...`)
  are unchanged.
- **Dev compose database:** default user/db renamed `noodle` → `nodyra`.
  Fresh volume required for the dev stack: `docker compose down -v` (dev
  data only — production installs set explicit DATABASE_URL and are
  unaffected).
- **MCP clients:** resource URIs are now `nodyra://...`; re-run discovery.
- **Vault (if using transit KMS):** the default transit key name is now
  `nodyra-master`. Either create the new key and re-wrap, or keep the old
  name by setting the explicit key-name setting.

## What does not change
- Database schema, table names, and migration history.
- `ndpat_` personal-access-token format.
- Webhook URLs and workflow/run IDs.
```

- [ ] **Step 4: Commit**

```bash
git add docs/upgrading-to-nodyra.md
git commit -m "docs: Nodyra upgrade guide (queue drain, cookie, dev-db reset)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Brand surfaces — title, README, landing

- [ ] **Step 1: Verify user-visible strings post-sweep:**

```bash
grep -rn -i "noodle" apps/web/index.html apps/web/src/Logo.tsx README.md 2>/dev/null
grep -rn "Nodyra" apps/web/index.html | head -3
```

Expected: zero noodle hits; `<title>Nodyra</title>`. Fix stragglers by hand.

- [ ] **Step 2: README top section.** Ensure the first line/logo says Nodyra and add a one-line former-name note near the top (searchers will look for it):

```markdown
> **Nodyra** was previously developed under the working name *Noodle*.
```

- [ ] **Step 3: Registry default.** `apps/api/app/config.py` `registry_index_url` now points at `.../nodyra-registry/...` post-sweep. This URL only resolves after the GitHub repo is renamed (manual checklist item in the master roadmap — GitHub redirects the old URL automatically after rename, so renaming the repo *before* deploying this change makes both URLs work).

- [ ] **Step 4: Commit**

```bash
git add README.md apps/web/index.html apps/web/src/Logo.tsx
git commit -m "docs: Nodyra brand surfaces (title, README, former-name note)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: CHANGELOG entry

**Files:** Create or prepend `CHANGELOG.md`

- [ ] **Step 1:** Prepend:

```markdown
# Changelog

## Unreleased — Nodyra rename

Noodle is now **Nodyra**. See `docs/upgrading-to-nodyra.md` for the
operational one-pager (queue drain, session cookie, dev-db reset,
`NOODLE_*` → `NODYRA_*` env vars with one-release fallbacks).
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for the Nodyra rename

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Final gate

- [ ] **Step 1: Rename gate**

Run: `uv run python scripts/check_rename.py`
Expected: `clean: no 'noodle' outside exempt paths`, exit 0. Fix any stragglers (each is either a missed suffix in the sweep script or a generated file — regenerate rather than hand-edit lockfiles).

- [ ] **Step 2: Full suites**

Run: `uv run ruff check . && uv run pytest apps/api/tests packages -q` then in `apps/web`: `npm run typecheck && npm test -- --run && npm run build`
Expected: 0 failures everywhere; the Vite build succeeds.

- [ ] **Step 3: Boot smoke** — migrations + app import under the new names:

Run: `uv run python -c "import app.main; print('app imports ok')"` and `uv run alembic -c apps/api/alembic.ini upgrade head` against a scratch SQLite (check how tests configure the alembic URL in `apps/api/tests/conftest.py` and reuse that env var).
Expected: no import errors; migrations apply.

- [ ] **Step 4: Add the gate to CI.** In `.github/workflows/ci.yml`, in the `python` job after the ruff step, add:

```yaml
      - run: uv run python scripts/check_rename.py
```

- [ ] **Step 5: Commit + PR**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: enforce the no-noodle-left rename gate

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

Open a PR of `phase2-nodyra-rename` → `main`; CI must be green before merge.

---

## Out-of-repo checklist (manual — for Harry, listed in master roadmap too)

1. Rename the GitHub repo (`noodle` → `nodyra`) **before** merging Task 6 (redirects keep old clones working).
2. Rename `noodle-registry` → `nodyra-registry` on GitHub (redirect preserves the old raw URL).
3. Register PyPI names `nodyra` and `nodyra-client` (even a 0.0.1 placeholder) before Phase 3 publishes.
4. Rename the local working dir `D:\noodle` → `D:\nodyra` when convenient. Note: Claude Code project memory is keyed by directory path — expect a fresh memory index after the move (the old one stays under the old key).
5. Any deployed instance: follow `docs/upgrading-to-nodyra.md`.
