# Workflow Template Catalog

The curated catalog is served by `GET /templates` and rendered in the workflow
creation journey. It supports text/tag/credential-free filters. Each template
is versioned independently and includes creator attribution, verification,
prerequisites, expected result, permission summary, compatibility range,
rating metadata, screenshot, and a complete importable graph.

Template source lives in `apps/api/app/data/templates`; screenshots live in
`apps/web/public/template-previews`. A catalog change must:

1. increment the template's SemVer version when behavior changes;
2. explain every credential, network destination, runtime package, and inbound
   trigger in prerequisites/permissions;
3. use a deterministic or clearly documented expected result;
4. validate against the current node registry without executing nodes;
5. retain older versions externally before introducing breaking changes so
   existing adopters have a migration path.

Run `uv run python scripts/validate_templates.py` before review. The release
workflow runs the same static validator. Runtime smoke fixtures for verified
templates belong in the final certification lane and must use fake/local
providers, never maintainer credentials.

`nodyra_template_instantiation_total` measures selected template and outcome.
Ratings in the bundled catalog are curated seed metadata, not claimed live
marketplace reviews; a public rating service must show sample size and abuse
controls before accepting user submissions.
