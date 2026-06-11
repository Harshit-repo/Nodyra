# Export modes

| Mode | Endpoint | Shape | Best for |
|------|----------|-------|----------|
| Python script | `GET /workflows/{id}/export.py` | graph embedded as JSON, engine runs it | running a frozen copy elsewhere |
| Docker bundle | `GET /workflows/{id}/export/docker` | script + Dockerfile + requirements | shipping as an image |
| Python module (code-first) | `GET /workflows/{id}/export.module.py` | one `@node` function per node, `main()` rebuilds + runs | reading, editing, extending in code |

## Code-first module: what is editable

- **Param defaults** on each function — `main()` reads them back via `inspect`,
  so editing them reconfigures the run.
- **`wires={...}`** — the graph's edges.
- **New `@node` functions** — become real custom nodes executed by the engine.
- Function **bodies** of generated wrappers are conveniences for calling a node
  standalone; the engine executes the original built-in type (`_DELEGATES`).

## Limitations (both .py modes)

- Credential references resolve against the Noodle server and will not decrypt
  in a standalone script — replace them with literals or environment lookups.
- `user:` code-module nodes are carried as raw graph entries; their Python
  lives in the Noodle database, so bundle it manually if needed.
- Expressions (`{{ $json... }}`) work unchanged — the engine evaluates them.
