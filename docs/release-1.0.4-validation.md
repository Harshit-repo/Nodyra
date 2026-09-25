# Nodyra 1.0.4 self-hosted validation

Validated on 25 September 2026 using source downloaded from GitHub at
`4ab663f9922fbee7d6504472ecb450dbb584fe3e` (`v1.0.4`).

The supported release profile is Docker Compose on a single host with local
artifact storage and trusted workflow authors. This is a self-hosted beta.

## Completed local checks

- Both distribution archives match all 1,478 tagged source files and each other.
- A fresh container build and installation used a separate Compose project,
  empty volumes, and generated test secrets.
- First-owner setup, a six-node workflow, and three downloaded artifacts passed;
  artifact SHA-256 checksums matched.
- All 13 browser journeys passed over HTTP and again over HTTPS (26 total).
- All 12 workflow scenarios passed, including failure recovery, cancellation,
  timeout, subworkflows, private egress, webhooks, batch execution, and export.
- Nine ingress checks passed: redirect path/query preservation, certificate and
  security headers, authentication, secure cookies, cross-origin mutation
  rejection, registration closure, MCP, WebSocket upgrades, and persisted data.
- All nine ingress/data checks passed again after restarting the stack.
- The API, worker, and web application processes ran as non-root users.
- Grype 0.119.0 scanned the three locally built images with a valid vulnerability
  database dated 24 September 2026. No High or Critical findings were reported.
  Lower-severity findings remain recorded in the attached reports.

Machine-readable acceptance and vulnerability reports, source archives, and
checksums accompany the [GitHub release](https://github.com/Harshit-repo/Nodyra/releases/tag/v1.0.4).

## Distribution and verification limits

This release distributes source for local Docker builds. GitHub Actions could
not start the 1.0.4 pipeline because of the account billing/spending-limit block;
the release does not claim a successful 1.0.4 hosted CI run, published 1.0.4
container images, or signatures/attestations for those images.

HTTPS checks used a temporary certificate trusted only by the test clients.
Operators must configure their own domain, trusted certificate, backups, and
secrets. High availability and isolation between hostile workflow authors are
outside the validated profile. The repository and release remain private.

## Product tour

The updated [product tour](nodyra-product-tour.gif) uses seven actual browser
captures: the homepage, workflow canvas, execution in progress, successful
execution, node inspector, Python source tab, and full Python editor. It loops
over 17.5 seconds at 1440 × 720. The captures use synthetic local test data.

The application captures are from the verified 1.0.4 installation; the homepage
includes the later standalone homepage polish on main. This updated tour is
provided as a separate release asset and on main. The source archives remain
exact copies of the immutable v1.0.4 tag, including its earlier documentation.
