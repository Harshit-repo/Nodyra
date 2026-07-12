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
