# Nodyra Licensing — Plain-English Guide

Nodyra is **fair-code**: the source is public, self-hosting is free for your
own use, and commercial resale or hosting requires a license from us. This page
explains what you can and cannot do. It is a summary for convenience — the
[LICENSE](../LICENSE), [LICENSE.enterprise](../LICENSE.enterprise), and
[TRADEMARK.md](../TRADEMARK.md) files are the binding texts.

## The three licenses at a glance

| What | License | Cost |
|---|---|---|
| Core platform (editor, engine, nodes, API, CLI, MCP) | [Nodyra Sustainable Use License](../LICENSE) | Free |
| Enterprise features (multi-tenancy, SSO/SAML/OIDC, SCIM, audit export, external KMS, license enforcement) | [Nodyra Enterprise License](../LICENSE.enterprise) | Paid license key |
| The Nodyra name and logo | [Trademark policy](../TRADEMARK.md) | Not licensed with the code |

## What you CAN do, free of charge

- Self-host Nodyra for your team, company, or personal projects — unlimited
  internal business use.
- Modify the source and run your modified version internally.
- Build and sell **consulting, integration, and support services** around
  Nodyra, as long as each client hosts and controls its own instance and you
  charge for your services, not for the software.
- Build and share nodes, integrations, and workflows — including commercially
  licensed ones, since your original node code is yours.
- Write tutorials, courses, and videos about Nodyra, including paid ones.
- Redistribute unmodified copies free of charge with the license intact.

The default Community product posture includes up to five seats, ten active
deployments, unlimited local workflow drafts, three environments, one
sandboxed runner, and basic health/metrics visibility. Limits reject new
activity without deleting existing workflows, runs, or artifacts.

## What you CANNOT do without a commercial agreement

- Offer Nodyra (or a derivative) to third parties as a hosted or managed
  service — SaaS, "automation platform powered by our fork", white-label
  hosting, or exposing its functionality through your own app or API.
- Sell the software, charge for access to it, or bundle it into a paid
  product or subscription.
- Use, enable, or unlock Enterprise-gated features without a valid license
  key, or tamper with the license checks.
- Use the Nodyra name or logo for your own product, fork, company, or domain.

If you want to do any of these, email **sharma.har97@gmail.com** — commercial
and OEM licenses are available.

## FAQ

**Is Nodyra open source?**
Not by the OSI definition — the Sustainable Use License restricts commercial
redistribution and hosting. It is *fair-code*: source available, free to
self-host for permitted uses, and commercially protected.

**Why not MIT or Apache?**
A permissive license would let anyone — including large cloud vendors — resell
Nodyra without contributing back. The fair-code model keeps self-hosting free
while funding continued development.

**I'm an agency. Can I build Nodyra workflows for clients and charge for it?**
Yes. Your time and expertise are yours to sell. The client should run its own
instance (or you administer the client's instance for them); what you may not
do is run one central Nodyra you charge many clients to access.

**Do Enterprise features stop working when a license expires?**
Licensing is designed to soft-fail: expiry downgrades limits to Community and
disables gated features for *new* activity, but never deletes data or stops
existing workflows destructively.

**Can I evaluate the Enterprise code?**
You may read the Enterprise source for evaluation, audit, and security review.
Running or enabling those features requires a license key.

**Who owns contributions?**
You keep ownership of your contribution; the [CLA](../CONTRIBUTOR_LICENSE_AGREEMENT.md)
grants the project the rights to distribute and relicense it. This is what
keeps a future license change (e.g., toward a more permissive license)
possible.

**Does the license phone home?**
No. License keys are verified offline with an embedded public key. Air-gapped
deployments are fully supported.
