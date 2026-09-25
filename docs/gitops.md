# GitOps Sync

Nodyra can sync workflow definitions to a GitHub repository as Python workflow
modules. The feature is org-scoped: each org can connect one GitHub repository,
one base directory, and one main branch.

## What Syncs

GitHub sync exports workflow graphs as `.py` modules using the same module format
accepted by `nodyra workflow import`. The sync records the GitHub file SHA on the
workflow and reports one of four statuses in the editor: `synced`, `pending`,
`conflict`, or `error`.

Draft changes enqueue a draft push. Creating a workflow, replacing a draft graph
from the editor, applying templates, and MCP workflow edits all use this path.
Publishing a workflow enqueues a publish push with a publish commit message.

GitHub sync does not export credentials, credential secret values, deployments,
environments, runner pools, run history, artifacts, users, roles, or workspace
settings. Keep those configured in Nodyra and treat the repository as the source
for workflow definition modules only.

## Setup

Create a GitHub token credential in Nodyra first. The credential type is
`github`, and the secret field is `token`. The token needs repository contents
read/write access for the target repository. If Nodyra should create the
repository through `POST /api/github-sync/repo`, the token also needs permission
to create repositories for the target user or organization.

Then configure the org-level sync target:

```bash
curl -X PUT "$NODYRA_BASE_URL/api/github-sync/config" \
  -H "Authorization: Bearer $NODYRA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "repo": "owner/repo",
    "base_path": "workflows/",
    "main_branch": "main",
    "credential_id": "cred_123"
  }'
```

The Settings UI can save the repository, base path, and main branch; validate
repository access; create the repository; show the webhook URL; reveal the
webhook secret; and disconnect sync. The current UI does not yet expose the
`credential_id` picker, so a complete token-backed setup currently requires the
API call above or another API client that sends `credential_id`.

After the config exists, get the webhook secret:

```bash
curl "$NODYRA_BASE_URL/api/github-sync/config/webhook-secret" \
  -H "Authorization: Bearer $NODYRA_TOKEN"
```

In GitHub, add a repository webhook:

- Payload URL: the `webhook_url` returned by `GET /api/github-sync/config`
- Content type: `application/json`
- Secret: the `webhook_secret` value
- Events: push events

Webhook requests are authenticated with `X-Hub-Signature-256`. Nodyra ignores
non-push events and pushes to branches other than the configured main branch.

## Repository Layout

Every synced workflow file is written under the configured base path:

```text
<base_path>/<slugified-workflow-name>.py
```

With `base_path` set to `workflows/`, a workflow named `Daily CRM Sync` is
stored at:

```text
workflows/daily-crm-sync.py
```

Draft pushes target a per-workflow branch:

```text
draft/<slugified-workflow-name>
```

Publish pushes target the configured main branch. Both draft and publish pushes
write the same module path.

## Push And Pull Flow

When a draft changes, Nodyra creates a `push_draft` job. The GitHub sync
dispatcher runs in the API process, wakes immediately after a job is queued, and
also polls for pending or retryable jobs. Draft pushes create the
`draft/<workflow-slug>` branch from the configured main branch if that draft
branch does not already exist.

When a workflow is published, Nodyra creates a `push_publish` job to the
configured main branch. Commit messages follow the origin:

```text
draft: <workflow name>
mcp: <workflow name>
api: <workflow name>
publish: <workflow name> v<version>
```

For inbound GitHub changes, use either path:

- Manual pull: in the editor menu, choose `Pull from GitHub`, which queues
  `POST /api/workflows/{workflow_id}/github-pull`.
- Webhook pull: a push webhook on the configured main branch scans changed
  `.py` files under `base_path`, matches paths to existing workflows, and queues
  pull jobs for matches.

Pull imports the Python module and replaces the workflow draft graph. New files
in GitHub do not create new Nodyra workflows automatically.

## Conflict Behavior

GitHub rejects a push when the stored file SHA no longer matches the remote
file. Nodyra marks the workflow `conflict` for GitHub HTTP `409` or `422`.

Webhook pulls also detect conflicts when GitHub changed since the last synced
SHA and the workflow has a local draft graph. Manual pulls are treated as an
explicit user action and skip that conflict check.

When a workflow is in `conflict`, the editor shows `Resolve GitHub conflict`:

- Keep Nodyra: the current draft remains and Nodyra queues a push that overwrites
  the GitHub file.
- Take GitHub: Nodyra queues a pull and replaces the current draft with the
  GitHub file.

There is no three-way merge UI yet. Conflict resolution is side-based.

## CI Example

After a GitHub change is merged to the configured main branch, the GitHub webhook
updates the existing workflow draft. A deployment pipeline can then run the
synced workflow and fail on the workflow outcome:

```yaml
name: Nodyra workflow smoke

on:
  push:
    branches: [main]
    paths:
      - "workflows/**/*.py"

jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install ./packages/client
      - name: Run synced workflow
        env:
          NODYRA_BASE_URL: ${{ secrets.NODYRA_BASE_URL }}
          NODYRA_TOKEN: ${{ secrets.NODYRA_TOKEN }}
          NODYRA_WORKFLOW_ID: ${{ secrets.NODYRA_WORKFLOW_ID }}
        run: |
          nodyra --json run start "$NODYRA_WORKFLOW_ID" --watch
```

`nodyra run start --watch` exits non-zero when the run fails, is canceled, or
times out, so it can gate a merge or deployment job.

## Not Yet Supported

- Selecting the GitHub credential from the Settings UI.
- Automatic GitHub webhook creation.
- Creating new Nodyra workflows from newly added GitHub files.
- Deleting Nodyra workflows when GitHub files are removed.
- Three-way merge or diff-assisted conflict resolution.
- Syncing credentials, deployments, environments, schedules outside the graph,
  run history, artifacts, users, roles, or workspace settings.
