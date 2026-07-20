# Community Node Registry

The registry is Nodyra's signed distribution channel for independently
versioned node packs. Search results expose maintainer, lifecycle, permissions,
compatibility, download/health metadata, examples, advisories, and every
available version. Installs, upgrades, and rollbacks all use the same durable
environment-build queue.

## Operator trust policy

Production installs are fail-closed. A package is installable only when:

- the selected version has an immutable HTTPS distribution URL and lowercase
  SHA-256 digest;
- the publisher signs the canonical manifest with Ed25519;
- `REGISTRY_TRUSTED_PUBLISHERS` contains that publisher's public key;
- permission and compatibility manifests are present;
- lifecycle is `active`; `deprecated` remains visible but cannot be newly
  installed, while `quarantined` and `revoked` are blocked.

Configure trusted publisher roots as JSON. The value may be a URL-safe base64
raw Ed25519 public key or PEM public key:

```env
REGISTRY_TRUSTED_PUBLISHERS={"nodyra-official":"<base64-public-key>"}
REGISTRY_ALLOW_UNVERIFIED_INSTALL=false
```

Unverified installs cannot be enabled in production. For a local-only
publisher workflow, set `REGISTRY_ALLOW_UNVERIFIED_INSTALL=true` with
`RUNTIME_MODE=local`; the UI labels the package unverified.

Nodyra stores a locked PEP 508 requirement:

```text
nodyra-example @ https://packages.example/nodyra_example-1.2.0-py3-none-any.whl#sha256=<digest>
```

The environment build downloads that exact artifact. Changing the index later
does not change an installed version.

## Package contract

A package declares a `nodyra.nodes` entry point and registers decorated nodes:

```toml
[project.entry-points."nodyra.nodes"]
example = "nodyra_example:register"
```

```python
def register() -> None:
    import nodyra_example.nodes  # noqa: F401
```

Keep registration side effects limited to importing node definitions. Do not
make network calls, read secrets, or mutate the filesystem during discovery.
Installed Python packages execute with the permissions of the workflow runtime;
the signature proves publisher and manifest integrity, not code safety.

## Signed index entry

Each release is a separate signed object. `signature` is URL-safe base64 over
the canonical JSON object containing the signed fields below, sorted by key and
encoded without whitespace.

```json
{
  "id": "nodyra-example",
  "name": "Example nodes",
  "version": "1.2.0",
  "pypi_package": "nodyra-example",
  "distribution_url": "https://packages.example/nodyra_example-1.2.0-py3-none-any.whl",
  "distribution_sha256": "<64 lowercase hex characters>",
  "publisher_key_id": "nodyra-official",
  "permissions": {
    "network": ["api.example.com:443"],
    "secrets": ["example_api_key"],
    "filesystem": "artifacts-only"
  },
  "compatibility": {
    "nodyra": ">=0.1.0,<0.2.0",
    "python": ">=3.12,<3.15"
  },
  "lifecycle": "active",
  "signature": "<urlsafe-base64-ed25519-signature>"
}
```

The index may place older signed entries in a `versions` array. Selecting an
older active version performs an explicit rollback; selecting a newer active
version performs an upgrade. Never reuse a version or replace its distribution
bytes.

## Publishing checklist

1. Build a wheel from a clean, tagged commit and generate its SHA-256 digest.
2. Review transitive dependencies, requested permissions, license, and source.
3. Sign the canonical manifest with a protected publisher key.
4. Submit the package plus release history, examples, health metadata, and any
   active advisories to the curated index.
5. Install into a disposable environment, run example workflows, then verify
   upgrade and rollback to the prior supported version.

## API and operations

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/node-registry/search?q=...` | Search and return trust-enriched packages |
| `GET` | `/node-registry/packages/{id}` | Inspect versions and metadata |
| `POST` | `/node-registry/install` | Install, upgrade, or roll back a selected version |
| `GET` | `/node-registry/installs/{id}` | Poll the durable build job |

Monitor `nodyra_registry_search_total` and
`nodyra_registry_install_total`. Alert on increased blocked/failed outcomes and
quarantine affected versions before removing their history from the index.

Set `ALLOW_REGISTRY=false` for air-gapped deployments. Registry index and
distribution fetches reject redirects, private IP literals, oversized indexes,
and unsafe URLs.
