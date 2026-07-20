# Nodyra Academy

These exercises use a deterministic loopback API and require no external
account. Start it in a separate terminal:

```bash
uv run python scripts/practice_api.py
curl http://127.0.0.1:8099/health
```

## 1. First inspected result

Build **Manual Trigger → HTTP Request → Code**. Fetch `/customers`, keep active
customers, and output only `id`, `name`, and `region`. Success means the run is
green and you can explain the exact input/output at each node.

## 2. Retry a recoverable provider failure

Request `/unstable?key=<unique-value>`. Configure the HTTP node for at least two
retries with bounded backoff. Confirm the first two responses are retryable 503s
and the final result is `recovered`. Then disable retries and inspect the
failure timeline.

## 3. Produce and trace an artifact

Use the **Dataset filter and CSV export** template. Run it, download the CSV,
open its provenance, and identify producer workflow/version/run/node, checksum,
storage backend, retention deadline, and any downstream consumers.

## 4. Publish without changing production accidentally

Publish a successful draft, create a deployment pinned to that version, then
edit the draft. Verify the deployed version is unchanged until you explicitly
publish and promote a new immutable version. Compare versions before promotion.

## 5. Migrate with evidence

Export a small n8n workflow or use a standalone Python script. Analyze it,
explain every exact/transformed/manual/unsupported finding, create a partial
draft only when required, reconnect credentials/packages, and compare fixtures
before publish.

## 6. Operate and recover

Call `/ops/production-attestation`, locate an intentional warning, generate a
redacted support bundle, and follow the queued-run or artifact decision tree.
The exercise is complete only when another operator can reproduce your diagnosis
without workflow payloads or credentials.

For short tutorial recording, capture each exercise as a 30–90 second clip with
the source version, expected result, captions, keyboard focus, and final evidence
surface visible. Re-record when the UI or outcome changes; do not ship clips
that hide errors, credentials, or skipped steps.
