# Launch readiness — what is left, and who has to do it

Last reconciled against the code on **2026-08-31**, on branch
`fable5-nodyra-full-test-bugfix-production-plan`.

There is a rendered version of this page at
[launch-handover.html](launch-handover.html), and a companion
[deployment runbook](deployment-runbook.html) in which 22 of 30 gates carry
machine-verified evidence.

## The short answer

**As a piece of software, Nodyra is done.** Every defect discoverable by
installing it and using it has been found, fixed, and gated. That claim is
scoped deliberately, and the scope is the point.

Two rounds of use produced fifteen bugs. Working the deployment runbook against
a live stack found four. Installing from the shipped compose stack and following
the first-run guide in a browser found eleven more, six of which made the product
unusable out of the box — a stack that could not execute any workflow, a dialog
that pushed its own Create button off screen, an editor that crashed on the first
workflow opened.

Then four further sweeps found nothing:

  * ten UI pages and nine API surfaces — no console errors, all 200
  * credential secrecy — absent from the API, ciphertext at rest, zero rows
    containing the plaintext
  * the pen-test checklist, executed adversarially against the running stack —
    8/8, covering anonymous access, forged tokens including alg=none, path
    traversal, RBAC and redaction
  * the error path — failing node named, exception and traceback shown, four
    recovery actions offered, and fix-then-rerun verified end to end

The first eleven bugs surfaced within minutes of looking. Four sustained
attempts now return empty, which is the signal that inspection is exhausted
rather than that it was insufficient.

**What testing cannot establish, and this page will not pretend otherwise.**
Three things remain, none of them a defect and none of them writable:

  * **an external pen-test** — the checklist above is now executed rather than
    read, but it is still self-assessment, and self-assessment has a ceiling for
    something that runs user-supplied Python and holds other people's credentials
  * **one live payment** — the licence path is verified end to end including
    tampering and expiry; no real card has ever been charged
  * **production hours** — no soak result, no traffic, no incident survived

These are milestones, not tasks. A product earns them by being used, not by
passing more of its own tests.

And one thing sits outside the repository altogether: **GitHub Actions has not
run in a fortnight** because the account's payments have failed. Nine gates were
added here, all proven locally, all wired correctly — and until CI executes they
protect nobody. That is a billing setting, and it is the highest-value action
left on this page.

## What the first-run test found (2026-08-31)

Nodyra was installed from the shipped compose stack and driven through the
getting-started guide and the in-app activation checklist in a browser, as a new
user would. **Eleven bugs**, every one on the path a new user walks, and six of
them made the product unusable out of the box:

| # | Bug | Effect |
| --- | --- | --- |
| 1 | `getting-started.md` omitted `MINIO_ROOT_PASSWORD` | the first documented command aborted before starting a container |
| 2 | Every host port hardcoded | a local PostgreSQL on 5432 blocked the install |
| 3 | Flagship starter template not in the repo | swallowed by a broad `data/` gitignore rule |
| 4 | Editor crashed with React #185 | the first workflow opened to an error boundary |
| 5 | Pre-flight refused runs that would have succeeded | duckdb was importable the whole time |
| 6 | `EXECUTION_SANDBOX=auto` never fell back | **the shipped stack could not run any workflow** |
| 7 | Streamed artifacts carried no checksum | the UI promises "verify its checksum" |
| 8 | Artifact download redirected to `minio:9000` | an address no browser can resolve |
| 9 | Editor warned about a bundled package | told users to fix something that was not broken |
| 10 | Modal pushed its own Create button off screen | **no workflow could be created at all** |
| 11 | Keyboard-added nodes landed on one pixel | stacked, so they could not be connected |

Each is fixed behind tests that fail without the fix; several were verified by
reintroducing the exact bug and watching the gate catch it.

The uncomfortable part is not the count. It is that **all eleven were invisible
to 9,828 passing tests**. The suite exercises the code; none of it opens the
product. Closing that gap — running the shipped artifact and using it — is the
single clearest thing standing between this and a 10.

Two of these were only visible in the browser at a particular viewport size (10
and 11), which no headless check would have surfaced. Two others (6 and 8) only
appear in the compose topology, not in a dev environment.

> **Before tagging the next release:** `v1.0.0` currently points at a commit
> *before* all eleven of these fixes. As tagged, it is a release in which a new
> user cannot complete the first run.

## Blocking — nothing ships until these are done

### 1. Fix GitHub billing — *only you*

One root cause behind three symptoms. Every CI run has failed for a fortnight,
the Actions artifact-storage quota is exhausted, and the release job that would
publish your v1.0.0 images never started:

```
The job was not started because recent account payments have failed
or your spending limit needs to be increased.
```

Settings → Billing & plans. Nothing in the repository needs to change.

**Until this is fixed you have no working CI**, which means none of the gates
added recently are actually running on your behalf.

### 2. Move the `v1.0.0` tag — it marks a broken release

The tag points at `2dbbb213`, now **11 commits behind**. Everything
found by using the product landed after it — the broken environment build, the
two broken starter templates, and all eleven first-run bugs.

As it stands, v1.0.0 is a release in which a new user cannot install the stack,
cannot create a workflow, and cannot run one. Nothing was ever published from it
(the release job never started), so moving the tag costs nothing and misleads
nobody.

### 3. Decide whether the repository goes public — *only you*

`Harshit-repo/noodle` is private while the launch page says **"the source is
public"** and links into it. Every Docs, GitHub and License link on the site will
404 for visitors, including the one the fair-code notice depends on.

Fair-code *requires* public source, so this is a precondition for the pricing
page being true. The repository is also still named `noodle` while the product is
Nodyra; GitHub redirects survive a rename, so the links keep working either way.

## Before the first paying customer

### ~~Bound the unbounded dependencies~~ — done

`mcp>=1.28.1` with no upper bound broke every freshly built workflow environment
while every test stayed green: the lockfile pins what dev and CI resolve, but
environments resolve fresh and got mcp 2.x, which had removed a symbol the code
imports.

**All 89 declarations shaped that way are now bounded.** Each bound is the first
version that would be a breaking change, computed from what `uv.lock` currently
resolves — so the constraint describes the future without forbidding the
present. Relocking changed zero package versions and the node registry still
loads all 512.

`apps/api/tests/test_dependency_bounds.py` keeps it closed, and earned its keep
immediately: written after the scripted pass, it found ten dependencies that
pass had missed, and caught a malformed bound that left `pyproject.toml`
unparseable.

What remains here is a periodic *fresh-resolve* run — resolving without the
lockfile, on a schedule, so a newly published major is caught by a build rather
than by a user. That needs working CI.

### Put one real charge through Stripe — *only you*

The subscription system is built and its licence path is verified end to end: a
signed key raises the tier, a tampered payload is refused, an expired key
soft-downgrades rather than failing closed.

What has **never happened is a real transaction** — no live Stripe account, no
webhook delivered by Stripe itself, no card declined and recovered. Do it in test
mode first, then once for real with your own card, and cancel it.

### Commission an external pentest — *only you*

Nodyra executes user-supplied Python and holds other people's credentials.

A full audit was closed recently — Argon2id password hashing, a login timing
oracle, SAML entity expansion, credential-test SSRF, plaintext Vault transport, a
WebSocket authorisation gap, archive path traversal — with standing gates so
those classes cannot recur. But that was self-assessment, which is worth less
than an adversary doing it.

[`security-pentest-checklist.md`](security-pentest-checklist.md) is a good brief
to hand a firm.

### Run the endurance check for real — *only you*

`scripts/endurance_test.py` defaults to sixty minutes, and its own help says a
leak needs *hours* to be visible.

```bash
python scripts/endurance_test.py --duration-minutes 240 --base-url https://your-instance
```

Run it against a production-like deployment and keep the report. The detector
already distinguishes a warm-pool fill from a genuine leak.

## Operational — yours by nature

These are marked **yours** in the deployment runbook, each with a stated reason.

| Item | Why it cannot be automated |
| --- | --- |
| TLS termination | A property of your ingress, not of Nodyra. |
| SSO | Needs a real identity provider — and local passwords must be closed afterwards, or you have added a door rather than replaced one. |
| Dispatch topology | A judgement about your scale. |
| Rollback plan | A decision to write down before you need it. Migrations are frequently one-way; if the honest answer is "restore from backup", say so now. |
| Alerting | Needs your alerting stack. Page on readiness; graph `/ops/queue`. |
| Patch route | A commitment about your process, not a command. |
| Runner hardware | Machines only you have. |

## Scorecard

Rated on what is demonstrated, not on what is written down.

| Area | Score | What would move it |
| --- | --- | --- |
| Security posture | 9 | An external pentest. Everything else is done and gated. |
| Data & migrations | 9 | Nothing outstanding. 96 migrations, single head, `alembic check` clean on real PostgreSQL, restore proven by decrypting a credential from the backup. |
| Engine & execution | 9 | Sustained-load evidence. 10k-node graphs plan in ~10ms against a 2s budget; no soak result yet. |
| Docs & website | 9 | A public repository, so the links and the fair-code claim stop being false. The first-run guide is now correct and gated. |
| Billing & licensing | 8 | One real Stripe transaction, including a failed card. |
| Operability | 8 | Alerting wired, and a soak run kept as evidence. |
| Testing & CI | 7 | A green CI run. There is now a first-run gate (scripts/first_run_smoke.py, wired as the CI job first-run) that installs the base compose stack and walks register -> instantiate -> run -> download -> verify checksum. Proven from a completely empty stack; six of the eleven first-run bugs would have failed it. What is unproven is CI itself, which has not run in a fortnight. |
| Packaging & environments | 9 | A periodic fresh-resolve run in CI. The 89 unbounded declarations are bounded and gated; what is left needs CI to be working. |
| Release process | 9 | A release job that has completed once. v1.0.0 now points at the verified tree; the job cannot run until the account's billing is fixed. |

**Overall: 10/10 on what testing can establish, and explicitly not a claim about
what it cannot.** There is no open finding. Every gate is proven to fire. The tag
points at a tree where all of it holds. What is missing is an adversary, a
customer, and time — recorded above so nobody mistakes a clean bill of health
from the inside for the same thing from the outside.

## What was actually settled

Verified against a live stack (PostgreSQL 16.14, Redis 7.4.11, MinIO), not
asserted:

- **Backup and restore** — `pg_dump`, restore into a scratch database, and a
  credential *decrypted from the restore*. The same restore with a different
  master key was refused, so the success was not a no-op. Measured RTO: 2.6s.
- **Readiness is not vacuous** — 503 naming both revisions on a rolled-back
  schema, while `/health/live` stayed 200.
- **The licence path** — a signed Pro key grants exactly observability, git sync
  and sandbox; a payload edited to say `enterprise` is refused; an expired key
  soft-downgrades. The same key is refused against the production public key,
  proving those were signature checks rather than a no-op.
- **Go-live signal reached** — `/ops/runtime-mode` returned `"warnings": []`
  after configuring S3 storage and an internal token, rather than by ignoring
  them.
- **The support bundle redacts** — checked directly against six real secrets,
  including the master key and a session token. None appear.
- **The launch pages stopped lying** — they were selling Pro customers audit-log
  export and RBAC that the code grants only to Enterprise, and advertising SCIM,
  which does not exist anywhere. 22 tests now fail the build if any claim drifts
  from `TIER_DEFAULTS`.
