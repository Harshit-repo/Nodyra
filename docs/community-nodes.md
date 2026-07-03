# Community Node Registry — Developer Guide

This guide explains how to publish, discover, and install community-contributed
node packages for the Nodyra workflow platform.

## Overview

The Community Node Registry lets anyone publish node packages to PyPI and have
them listed in a public registry index. Users can browse and install these
packages directly from the Nodyra UI with one click.

### Architecture

Two components:

1. **Registry index** — a curated JSON file
   (`https://github.com/nodyra-registry/packages`) that lists every available
   package. New entries are added via pull request and reviewed by a Nodyra
   maintainer.

2. **Nodyra client** — the Nodyra API plus the Settings UI that fetches the
   registry index and manages installations.

## Publishing a Node Package

### Step 1: Create your Python package

Your package must be installable from PyPI. The minimal structure:

```
nodyra-my-nodes/
  pyproject.toml
  src/
    nodyra_my_nodes/
      __init__.py
      nodes.py       # your @node-decorated functions
```

### Step 2: Declare the Nodyra entry point

In your `pyproject.toml`, add an entry point under
`[project.entry-points."nodyra.nodes"]`:

```toml
[project.entry-points."nodyra.nodes"]
my_nodes = "nodyra_my_nodes:register"
```

### Step 3: Implement the registration function

```python
# nodyra_my_nodes/__init__.py

def register():
    # Importing your node module is sufficient — the @node decorators
    # auto-register the nodes during import.
    import nodyra_my_nodes.nodes  # noqa: F401
```

```python
# nodyra_my_nodes/nodes.py

from nodyra import node

@node(
    id="my_hello",
    label="Say Hello",
    inputs=[{"name": "name", "type": "string"}],
    outputs=[{"name": "greeting", "type": "string"}],
)
def say_hello(name: str) -> dict:
    """Return a friendly greeting."""
    return {"greeting": f"Hello, {name}!"}
```

### Important

- Do **NOT** import individual node symbols (e.g. `from nodyra_my_nodes.nodes
  import say_hello`) in your `register()` function — that would create
  duplicate imports and could double-register the same node.
- Just `import nodyra_my_nodes.nodes` at the module level — the `@node`
  decorator registers the node automatically on import.

### Step 4: Publish to PyPI

```bash
pip install build twine
python -m build
python -m twine upload dist/*
```

### Step 5: Submit to the registry index

1. Fork `https://github.com/nodyra-registry/packages`
2. Edit `index.json` to add your package entry:

```json
{
  "packages": [
    {
      "id": "nodyra-my-nodes",
      "name": "My Nodes",
      "description": "Useful nodes for my integration",
      "author": "your-npm-username",
      "version": "0.1.0",
      "nodes": ["my_hello", "my_goodbye"],
      "install_url": "https://github.com/your-username/nodyra-my-nodes",
      "pypi_package": "nodyra-my-nodes"
    }
  ]
}
```

3. Open a pull request

A Nodyra maintainer will review the submission before merging.

## Security Model

**Installed PyPI packages run with full interpreter access.** The AST sandbox
only applies to code strings created directly by users (code nodes,
AI-generated node functions). A `nodyra-*` package from PyPI runs compiled
Python with no sandbox — it can do anything the containing process can do.

Security relies on **registry governance**, not sandbox containment:

- The GitHub-backed registry index is the security perimeter. Every new package
  is reviewed by a Nodyra maintainer before inclusion.
- **Review checklist:**
  - No unexpected network calls or hard-coded IPs
  - No file-system writes outside designated paths
  - No `__import__` tricks or dynamic code execution
  - All dependencies are pinned or vendored
- Package source is published on GitHub — users can inspect what they install.
- The `node_registry:install` permission is **admin-only** and not available
  for custom roles.
- Set `ALLOW_REGISTRY=false` in your `.env` (or export
  `NODYRA_ALLOW_REGISTRY=false`) to disable the registry entirely for
  air-gapped or maximum-security deployments.

### Future (post-MVP)

Longer-term, packages may be signed with a Nodyra-managed key and signature
verified on install.

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `ALLOW_REGISTRY` | `true` | Set `false` to disable the registry feature |
| `REGISTRY_INDEX_URL` | `https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json` | URL of the registry index JSON |

## API Reference

The following endpoints are available:

| Method | Path | Description |
|---|---|---|
| `GET` | `/node-registry/search?q=...` | Search registry packages |
| `GET` | `/node-registry/packages/{id}` | Get single package details |
| `POST` | `/node-registry/install` | Install a package (async) |
| `GET` | `/node-registry/installs/{install_id}` | Poll install status |

### Install flow

1. `POST /node-registry/install` with `{"package_id": "...", "environment_id": "..."}`
2. Returns `202 Accepted` with `{"install_id": "...", "status": "pending"}`
3. The package's `pypi_package` is added to the environment's package list immediately
4. A background task rebuilds the environment's virtual environment
5. Poll `GET /node-registry/installs/{install_id}` to track progress:
   - `pending` → `installing` → `ready` (success) or `failed` (with error message)
6. On failure, the package is automatically removed from the environment (rollback)
