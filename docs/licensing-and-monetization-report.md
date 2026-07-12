# Nodyra Licensing & Monetization Decision Report

> Prepared 2026-07-11. Recommendation: **publish the repository publicly under the
> fair-code Sustainable Use License already drafted in `LICENSE`, keep the
> Enterprise carve-out (`LICENSE.enterprise`), and monetize open-core via offline
> license keys as specified in `docs/licensing-plan.md`.** This document explains
> why, what the alternatives are, and what to do before launch.
> This is strategic analysis, not legal advice — have an attorney review
> `LICENSE`, `LICENSE.enterprise`, and the CLA before public launch.

## 1. The question

Should Nodyra be released open source, and under which license, given that
modern AI tooling lowers the cost of building a comparable product — and that if
Nodyra stays closed, someone else may ship an open version that captures the
category?

## 2. The AI-clone argument actually favors publishing

The fear is correct in one direction and wrong in the other:

- **Correct:** code secrecy is no longer a moat. If an AI-assisted team can
  rebuild Nodyra's feature set in months, keeping the repo private protects
  almost nothing while forfeiting distribution.
- **Wrong conclusion:** "therefore give everything away under MIT." When code is
  cheap to produce, the durable assets are **distribution, community, brand,
  velocity, and trust** — none of which require a permissive license, and two of
  which (brand, monetization) a permissive license actively undermines.

n8n proves the model: source-available under its Sustainable Use License since
2022, ~200,000 active users, 3,000+ enterprise customers, and a $60M Series B —
while the code has been publicly forkable the whole time. Nobody out-competed
n8n with a fork, because the moat was never the code. Conversely, Activepieces
ships MIT and must compete with anyone — including cloud providers — reselling
its own core.

The category is being commoditized by AI regardless of what you do. Publishing
early under a protective license is how you become the default before a clone
does, while keeping the legal right to be the only one selling it as a service.

## 3. Options considered

| Option | Precedent | What it protects | What it costs |
|---|---|---|---|
| **MIT / Apache-2.0** | Activepieces (core) | Nothing — anyone may resell, host, rebrand | Max adoption; zero commercial protection |
| **AGPL-3.0 + commercial EE** | Windmill, Automatisch | Closed SaaS free-riding (copyleft forces source release) | OSI-approved "open source" label; but a competitor may still legally run a hosted AGPL service if they publish their changes; enterprise legal teams often ban AGPL, which ironically drives paid-license sales |
| **BSL / BUSL** | MariaDB, HashiCorp | Production/competitive use for up to 4 years, then opens | Per-grant variability confuses users; HashiCorp's switch triggered the OpenTofu fork |
| **FSL** | Sentry, Liquibase | Competing use for 2 years, each release then converts to Apache-2.0/MIT | Clean and predictable, but the 2-year conversion means old versions become fully free — a well-funded competitor can build on your 2-year-old code |
| **Fair-code SUL + EE** ← current draft | **n8n** | Commercial resale and hosted/managed offerings, permanently; free for internal business and personal use | Not OSI "open source" (marketing nuance); relies on your enforcement |
| **Stay closed** | — | Nothing durable (see §2) | Forfeits adoption funnel entirely |

## 4. Recommendation

**Ship what you have: Sustainable Use License (fair-code) for the core +
Enterprise license for gated features + trademark policy + CLA.** Rationale:

1. **It matches the business.** Nodyra is self-hosted automation for teams —
   exactly n8n's shape, and the SUL was purpose-built for that shape. Free
   internal use is the adoption funnel; the SaaS/resale prohibition reserves
   hosting and commercial licensing revenue for you.
2. **It answers the clone threat.** A competitor can read your code but cannot
   legally host it, resell it, or rebrand it (trademark). To compete they must
   rewrite from scratch — at which point your community, node library,
   integrations, docs, and brand are the race, and you have a head start.
3. **The infrastructure already exists.** LICENSE, LICENSE.enterprise,
   TRADEMARK.md, the CLA (which lets you relicense later if you ever want
   AGPL or FSL), and the offline Ed25519 license-key design in
   `docs/licensing-plan.md` are all consistent with this model. Switching to
   AGPL now would mean re-architecting the EE boundary for no clear gain.
4. **The CLA keeps every door open.** Because contributors assign relicensing
   rights, you can later move to FSL (if you want time-based opening as a
   community goodwill gesture) or AGPL (if the "not real open source" criticism
   ever materially hurts adoption). License changes toward *more* freedom are
   always safe; the reverse is what burned HashiCorp.

**When you would pick AGPL instead:** if the "100% open source" label proves
decisive for your target users (platform/data engineers sometimes care), AGPL +
EE (the Windmill model) is the fallback. You lose the hard resale prohibition
but gain OSI legitimacy. Revisit after 6–12 months of public feedback; the CLA
makes the migration possible.

## 5. Monetization plan

Open-core with offline license keys, per `docs/licensing-plan.md`:

1. **Community (free, default):** full platform, limits on active workflows,
   deployments, environments, users. The funnel. Keep limits generous enough
   that a hobbyist never hits them — resentment kills fair-code goodwill.
2. **Pro (per instance / month):** raised limits, runner pools, sandboxed
   execution at scale, observability, audit-log export, Git-backed versioning,
   email support. Anchors: n8n's self-hosted Business plan is ~$800/mo; the
   landing page's $49/mo Pro is an aggressive wedge — sensible for launch,
   with room to move upmarket.
3. **Enterprise (custom, annual):** unlimited scale, SSO/SAML/OIDC, SCIM,
   external KMS/Vault, multi-tenancy, air-gap licensing, SLA, onboarding.
4. **Later, in order:** managed cloud (highest-margin, but only after the
   self-hosted funnel proves demand); paid support/services for Community
   users; a certified node/integration marketplace.

Pricing mechanics that respect the product's values (and differentiate from
n8n's execution-based billing): per-instance, offline-verified keys, no
phone-home, soft-fail on expiry. That story is already on the landing page —
keep it, it's a genuine trust differentiator.

## 6. Pre-launch checklist

- [ ] Attorney review of LICENSE, LICENSE.enterprise, CLA (fill the
      contact/governing-law placeholders), TRADEMARK.md.
- [ ] Confirm the legal licensor name in all license files.
- [ ] Register the Nodyra trademark (at minimum, file in your home jurisdiction)
      — the trademark is the enforcement backbone of this strategy.
- [ ] Move enterprise-gated code under `ee/` or mark files with the
      "Nodyra Enterprise" header so the license boundary is mechanical, not
      interpretive.
- [ ] Implement the license-key service (`docs/licensing-plan.md` §3–4) before
      announcing paid tiers.
- [ ] Ensure no secrets/history leaks in the public git history
      (`.secrets/`, `.env`, audit logs) — consider a fresh public repo with a
      squashed initial commit.
- [ ] README, CONTRIBUTING, landing page all state the same license (fixed in
      this pass — landing previously said "MIT licensed").

## 7. Sources

- [n8n Sustainable Use License](https://docs.n8n.io/sustainable-use-license/) ·
  [announcement](https://blog.n8n.io/announcing-new-sustainable-use-license/) ·
  [license text](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)
- [n8n licensing explained (Scalevise)](https://scalevise.com/resources/n8n-automation-license-commercial-use/) ·
  [n8n deep dive incl. scale/funding (Jimmy Song, 2026)](https://jimmysong.io/blog/n8n-deep-dive/)
- [Activepieces license (MIT + commercial ee/)](https://www.activepieces.com/docs/about/license) ·
  [repo](https://github.com/activepieces/activepieces)
- [n8n vs Activepieces vs Windmill comparison, 2026](https://www.booleanbeyond.com/insights/n8n-vs-activepieces-vs-windmill-open-source-automation)
- [Functional Source License](https://fsl.software/) ·
  [Sentry FSL announcement](https://blog.sentry.io/introducing-the-functional-source-license-freedom-without-free-riding/) ·
  [FOSSA guide to source-available licenses](https://fossa.com/blog/comprehensive-guide-source-available-software-licenses/)
- [n8n pricing](https://n8n.io/pricing/) ·
  [n8n pricing analysis 2026](https://techjacksolutions.com/ai-tools/n8n/n8n-pricing/)
- [Why open source isn't always fair — dual licenses](https://www.architecture-weekly.com/p/why-open-source-isnt-always-fair)
