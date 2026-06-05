# Multi-Backend Environment Support — Design Spec

> Date: 2026-06-05
> Status: approved for implementation planning
> Scope: pluggable environment backends (venv, conda, pixi, Docker), platform-adaptive node requirements, and context-aware system dependency guidance.

## Goal

Give Noodle users control over how their Python environments are built — from fast pure-Python venvs to conda environments with system packages to full Docker containers — while keeping the existing package editor UX familiar and making system dependency problems solvable rather than just documented.

## Hard Constraints

- All four backends must coexist. A workspace can have one env per backend simultaneously.
- Changing a backend on an existing environment triggers a full rebuild. The package list carries over for venv/conda/pixi. For Docker, `packages` is only used by managed mode.
- No backend is mandatory. If `conda` or `docker` is not installed on the server, those options are greyed out in the UI. venv (uv) is always available.
- Existing environments automatically migrate to `backend="venv"` with no rebuild required.

## Phased Rollout

This spec covers the full design. Implementation is broken into three phases by value/risk ratio:

| Phase | Scope | Trigger |
|-------|-------|---------|
| 1 | `index_urls` on venv + platform-adaptive requirements (PEP 508 markers) | Now — unblocks CUDA and `zxing-cpp` swap |
| 2 | conda/micromamba backend + pixi backend + system_requirements on `@node` | Wave 3 ML nodes — system deps become real blockers |
| 3 | Docker backend (all three sub-modes) + context-aware system req UI | First enterprise deployment request |

pixi ships in Phase 2 alongside conda. It adds value over conda when users want a single lock-file tool that manages both conda and PyPI packages with reproducible builds.

---

## Architecture: Backend Dispatcher

### Data Model

Two new columns on the `environments` table (Alembic migration, both with server defaults — zero downtime):

```python
backend: str = "venv"    # "venv" | "conda" | "pixi" | "docker"
backend_config: dict = {}
```

`backend_config` shape per backend:

```python
# venv
{"index_urls": ["https://download.pytorch.org/whl/cu121"]}

# conda
{"channels": ["conda-forge", "defaults", "nvidia"], "solver": "auto"}
# solver: "auto" = detect micromamba > mamba > conda at build time

# pixi
{"channels": ["conda-forge", "defaults"]}
# pixi manages its own lock file; channels control conda package resolution
# packages list is installed as conda deps; PyPI packages use "pkg @ pypi" prefix (see below)

# docker — managed (base image + packages list)
{"mode": "managed", "base_image": "python:3.12-slim"}

# docker — custom Dockerfile
{"mode": "dockerfile", "dockerfile": "FROM python:3.12-slim\nRUN apt install -y ghostscript ..."}

# docker — pre-built image
{"mode": "image", "image_name": "myorg/data-env:v3"}
```

`packages: list[str]` is used by venv, conda, pixi, and Docker managed mode. Ignored for Docker dockerfile and pre-built.

For pixi, packages are treated as conda packages by default. Pure-PyPI packages can be declared with a `@ pypi` suffix (e.g., `"some-pypi-only-pkg @ pypi"`) and `PixiBackend` routes them to `pixi add --pypi`.

### Backend Protocol

```python
# apps/api/app/services/backends/base.py
class EnvironmentBackend(Protocol):
    async def build(self, env: Environment) -> tuple[str, str]: ...
    # Returns ("ready"|"error", log_tail)

    def python_path(self, env_id: str) -> Path | None: ...
    # Stable path to the Python binary. None for Docker (no local binary).

    async def destroy(self, env_id: str) -> None: ...
    # Tear down the environment (rm -rf, conda env remove, docker rmi).
```

Dispatcher:

```python
# apps/api/app/services/backends/__init__.py
def get_backend(env: Environment) -> EnvironmentBackend:
    match env.backend:
        case "venv":   return VenvBackend()
        case "conda":  return CondaBackend()
        case "pixi":   return PixiBackend()
        case "docker": return DockerBackend()
        case _:        raise ValueError(f"Unknown backend: {env.backend}")
```

### File Layout

```
apps/api/app/services/
  backends/
    __init__.py      # get_backend() dispatcher; build_environment() moved here
    base.py          # EnvironmentBackend Protocol
    venv.py          # extracted from current venv.py — minimal changes
    conda.py         # Phase 2 — shells out to micromamba/mamba/conda
    pixi.py          # Phase 2 — shells out to pixi CLI
    docker.py        # Phase 3 — builds/pulls images, handles 3 sub-modes
  venv.py            # thin shim re-exporting from backends.venv for backwards compat
```

---

## Phase 1: venv `index_urls` + Platform-Adaptive Requirements

### venv `index_urls`

`VenvBackend.build()` reads `env.backend_config.get("index_urls", [])` and passes them to `uv pip install`:

```python
cmd = ["uv", "pip", "install", "--python", str(venv_python(env_id)), *to_install]
for url in index_urls:
    cmd += ["--extra-index-url", url]
```

The environment editor shows an "Extra package indexes" field when `backend = "venv"`. Users add `https://download.pytorch.org/whl/cu121` to get CUDA-enabled PyTorch wheels.

### Platform-Adaptive Requirements (PEP 508 Markers)

Node authors declare platform-specific packages using standard PEP 508 environment markers on the `requirements` list:

```python
@node(
    requirements=[
        "zxing-cpp>=2.2; sys_platform=='win32'",
        "pyzbar>=0.1.9; sys_platform!='win32'",
        "pillow>=10.0",
    ]
)
def barcode_qr_decode(input=None):
    import sys
    if sys.platform == "win32":
        import zxing_cpp as _zx
        ...
    else:
        from pyzbar import pyzbar
        ...
```

**Preflight changes (`package_preflight.py`):**
- Parse the `; sys_platform==...` suffix from each requirement before canonical name comparison.
- Filter requirements to only those whose marker matches the server's platform.
- Server platform is cached at startup from `sys.platform`.

**Frontend changes (`missingPackages.ts`):**
- `GET /environments/backends` returns `{"platform": "win32"|"linux"|"darwin", ...}`.
- `missingFor()` gains a `platform` parameter and filters markers before checking installed packages.
- NodeCard missing-package badge only shows packages relevant to the current server platform.

**Canonical marker values mapping:**
| `sys.platform` | PEP 508 `sys_platform` |
|----------------|----------------------|
| `win32` | `win32` |
| `linux` | `linux` |
| `darwin` | `darwin` |

---

## Phase 2: conda Backend + `system_requirements`

### conda Backend

`CondaBackend.build()` uses micromamba by default, falling back to mamba then conda:

```python
async def _solver_cmd(self) -> str:
    for cmd in ("micromamba", "mamba", "conda"):
        if shutil.which(cmd):
            return cmd
    raise RuntimeError("No conda-compatible solver found. Install micromamba.")

async def build(self, env: Environment) -> tuple[str, str]:
    solver = await self._solver_cmd()
    channels = env.backend_config.get("channels", ["conda-forge", "defaults"])
    channel_args = [arg for c in channels for arg in ("-c", c)]
    env_dir = venv_dir(env.id)  # reuse same envs/ directory
    if env_dir.exists():
        shutil.rmtree(env_dir)
    code, log = await _run(
        solver, "create", "--yes",
        "--prefix", str(env_dir),
        f"python={env.python_version}",
        *channel_args,
        *env.packages,
    )
    return ("ready" if code == 0 else "error"), log[-4000:]

def python_path(self, env_id: str) -> Path:
    base = venv_dir(env_id)
    return base / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
```

The existing runner pool warm pool code works unchanged — `python_path()` still returns a `Path`.

**Recommended conda solver: micromamba.** Single ~10MB static binary, no base environment, no Python dependency, same conda-forge channel compatibility as full conda. Documented as the recommended install; other solvers work as fallback.

### pixi Backend

`PixiBackend.build()` creates a pixi project directory under `envs/{env_id}/`, writes a minimal `pixi.toml`, then runs `pixi install`:

```python
async def build(self, env: Environment) -> tuple[str, str]:
    if not shutil.which("pixi"):
        raise RuntimeError("pixi not found. Install from https://pixi.sh")
    env_dir = venv_dir(env.id)
    if env_dir.exists():
        shutil.rmtree(env_dir)
    env_dir.mkdir(parents=True)

    channels = env.backend_config.get("channels", ["conda-forge"])
    channel_lines = "\n".join(f'  "{c}",' for c in channels)

    # Split packages: "pkg @ pypi" goes to [pypi-dependencies], rest to [dependencies]
    conda_pkgs, pypi_pkgs = [], []
    for pkg in env.packages:
        if pkg.endswith("@ pypi") or "@ pypi" in pkg:
            pypi_pkgs.append(pkg.replace("@ pypi", "").strip())
        else:
            conda_pkgs.append(pkg)

    conda_lines = "\n".join(f'{p} = "*"' for p in conda_pkgs)
    pypi_lines = "\n".join(f'{p} = "*"' for p in pypi_pkgs)

    toml = f"""[project]
name = "noodle-env-{env.id}"
channels = [
{channel_lines}
]
platforms = ["{_current_platform()}"]

[dependencies]
python = "{env.python_version}.*"
{conda_lines}

[pypi-dependencies]
{pypi_lines}
"""
    (env_dir / "pixi.toml").write_text(toml)
    code, log = await _run("pixi", "install", "--manifest-path", str(env_dir / "pixi.toml"))
    return ("ready" if code == 0 else "error"), log[-4000:]

def python_path(self, env_id: str) -> Path:
    base = venv_dir(env_id)
    # pixi installs to .pixi/envs/default/ inside the project dir
    if sys.platform == "win32":
        return base / ".pixi/envs/default/Scripts/python.exe"
    return base / ".pixi/envs/default/bin/python"

async def destroy(self, env_id: str) -> None:
    shutil.rmtree(venv_dir(env_id), ignore_errors=True)
```

`_current_platform()` maps `sys.platform` → pixi platform string (`"linux-64"`, `"osx-arm64"`, `"win-64"`).

The existing runner pool warm pool code works unchanged — `python_path()` returns a `Path`.

**pixi vs conda distinction for users:**
- Pixi generates a `pixi.lock` lockfile — rebuilds are fully reproducible even months later.
- Pixi manages both conda and PyPI packages natively in one manifest.
- Conda is better when the user needs full `conda-forge` solver flexibility or existing conda environments to reference.

The PackageDrawer for pixi environments shows a channels field (same as conda) plus a note that packages suffixed `@ pypi` are installed from PyPI instead of conda-forge.

### `system_requirements` on `@node`

New optional field on the `@node` decorator and `NodeManifest`:

```python
class SystemRequirement(BaseModel):
    name: str              # "ghostscript"
    apt: str = ""          # "ghostscript"
    brew: str = ""         # "ghostscript"
    windows: str = ""      # URL or "bundled" or "not-supported"
    dockerfile_hint: str = ""  # "RUN apt-get install -y ghostscript"
    note: str = ""         # shown as tooltip

# On @node:
@node(
    requirements=["camelot-py", "pdfplumber>=0.11", "pandas>=2.0"],
    system_requirements=[
        {
            "name": "ghostscript",
            "apt": "ghostscript",
            "brew": "ghostscript",
            "windows": "https://ghostscript.com/releases",
            "dockerfile_hint": "RUN apt-get install -y ghostscript",
        }
    ],
)
def pdf_extract_tables(...): ...
```

`NodeManifest` gains `system_requirements: list[SystemRequirement] = []`.

### Context-Aware System Requirements UI

The NodeDetails panel shows a `system_requirements` section whose message and actions depend on the active environment's backend:

| Active env backend | Message | Action |
|-------------------|---------|--------|
| venv / conda / pixi | "Must be installed on the server" | Shows apt/brew/Windows commands |
| Docker (Dockerfile) | "Add to your Dockerfile" | Shows `dockerfile_hint` with copy button + "Open Dockerfile editor →" |
| Docker (Managed) | "Managed mode can't install system libs" | "Switch to Dockerfile mode →" (packages carry over) |
| Docker (Pre-built) | "Must be in your image" | Shows package name to verify |

For venv/conda/pixi envs, a footer nudge reads: "Switch to a Docker (Dockerfile) environment to manage this inside Noodle."

NodeCard gets an orange dot badge (distinct from the yellow missing-pip-packages badge) when `system_requirements` is non-empty. It is always visible — Noodle cannot verify OS-level installs from the browser.

---

## Phase 3: Docker Backend

### Three Sub-Modes

Controlled by `backend_config.mode`:

**managed** — base image + packages. Noodle generates the Dockerfile:
```
FROM {base_image}
RUN pip install noodle-runtime {packages...}
```

**dockerfile** — user-provided Dockerfile in the editor. `noodle-runtime` is auto-injected if absent. Full control — `apt install`, `COPY`, custom base, anything.

**pre-built** — image name only. No build step. Noodle pulls on first use. The user is responsible for having `noodle-runtime` installed in the image. Preflight package check is disabled.

### DockerBackend Implementation

```python
async def build(self, env: Environment) -> tuple[str, str]:
    mode = env.backend_config.get("mode", "managed")
    tag = f"noodle-env-{env.id}:latest"

    if mode == "managed":
        base = env.backend_config.get("base_image", "python:3.12-slim")
        pkgs = " ".join(env.packages)
        dockerfile = f"FROM {base}\nRUN pip install noodle-runtime {pkgs}"
        return await self._build_image(env.id, dockerfile, tag)

    elif mode == "dockerfile":
        raw = env.backend_config.get("dockerfile", "")
        if "noodle-runtime" not in raw:
            raw += "\nRUN pip install noodle-runtime"
        return await self._build_image(env.id, raw, tag)

    elif mode == "image":
        name = env.backend_config.get("image_name", "")
        code, log = await _run("docker", "pull", name)
        return ("ready" if code == 0 else "error"), log[-4000:]

def python_path(self, env_id: str) -> Path | None:
    return None  # no local binary — runner uses docker run

def _image_for_env(self, env: Environment) -> str:
    if env.backend_config.get("mode") == "image":
        return env.backend_config["image_name"]
    return f"noodle-env-{env.id}:latest"
```

### Runner Integration

`runner.py` checks `backend.python_path()`:

```python
backend = get_backend(env)
python = backend.python_path(env.id)
if python is not None:
    process = await asyncio.create_subprocess_exec(str(python), "-u", "-m", "noodle_runtime", ...)
else:
    image = backend._image_for_env(env)
    process = await asyncio.create_subprocess_exec(
        "docker", "run", "--rm", "-i", "--network", "host",
        image, "python", "-u", "-m", "noodle_runtime", ...
    )
```

### Warm Pool for Docker

Same fixed/elastic/spawn config as venv (existing `runner_pool_size` / `runner_pool_max` fields). Pre-built image mode defaults to spawn (cold-start) and the pool size fields are read-only in the UI for that mode.

Warm Docker containers are long-running `docker run` processes — the pool manager holds them open exactly as it holds open venv Python subprocesses. No architectural change to the pool manager beyond the `python_path` / `docker run` dispatch above.

### Pre-built Image Autocomplete

`GET /environments/docker-images` returns locally available Docker images from `docker images --format json`. Response groups them:

```json
{
  "noodle_built": [
    {"tag": "noodle-env-a3f2:latest", "env_name": "gpu-env", "size_mb": 1800, "built": "2h ago"}
  ],
  "other": [
    {"tag": "pytorch/pytorch:2.3.0-cuda12.1", "size_mb": 7200, "pulled": "1w ago"}
  ]
}
```

The image name field in the creation dialog uses this for autocomplete, with Noodle-built images listed first.

---

## API Changes

### Modified endpoints

`POST /environments` and `PATCH /environments/{id}` — accept `backend` and `backend_config` (both optional, default to `"venv"` and `{}`). Changing either field sets `status = "pending"` and enqueues a rebuild.

### New endpoints

`GET /environments/backends` — returns available backends and server platform. Cached at startup:
```json
{
  "platform": "linux",
  "venv":   {"available": true,  "version": "uv 0.4.1"},
  "conda":  {"available": true,  "version": "micromamba 1.5.8", "solver": "micromamba"},
  "pixi":   {"available": true,  "version": "pixi 0.24.2"},
  "docker": {"available": false, "version": null}
}
```

`GET /environments/docker-images` — Phase 3 only. Returns locally available Docker images for autocomplete.

### DB migration

```python
op.add_column("environments", sa.Column("backend", sa.String(20), server_default="venv", nullable=False))
# valid values: "venv" | "conda" | "pixi" | "docker"
op.add_column("environments", sa.Column("backend_config", sa.JSON, server_default="{}", nullable=False))
```

Zero downtime. Existing rows get `backend="venv"`, `backend_config={}`.

---

## Frontend Changes

### `EnvironmentsPage.tsx`

Creation dialog: tabbed backend selector at the top. Tabs: **uv + venv** | **conda** | **pixi** | **Docker** (greyed out if unavailable per `/environments/backends`). Fields below the tabs update per backend:
- venv: name, python version, packages, extra index URLs
- conda: name, python version, channels (ordered list, add/remove), packages
- pixi: name, python version, channels (ordered list, add/remove), packages (suffix `@ pypi` for PyPI-only packages)
- Docker: name, mode selector (managed / dockerfile / pre-built), then mode-specific fields

Environment cards show a backend badge: green `venv`, blue `conda`, teal `pixi`, purple `docker`.

### `PackageDrawer.tsx`

Channels field rendered above packages for conda and pixi environments. For conda, each package row shows which channel resolved it (from `micromamba list --json` output). For pixi, the package row shows `conda` or `pypi` as the source. The `@ pypi` suffix is displayed as a tag rather than raw text.

### `types.ts`

```typescript
interface SystemRequirement {
  name: string;
  apt?: string;
  brew?: string;
  windows?: string;
  dockerfile_hint?: string;
  note?: string;
}

interface NodeManifest {
  // ... existing fields ...
  system_requirements?: SystemRequirement[];
}

interface Environment {
  // ... existing fields ...
  backend: "venv" | "conda" | "pixi" | "docker";
  backend_config: Record<string, unknown>;
}
```

### `missingPackages.ts`

`missingFor()` gains platform-aware PEP 508 marker filtering:
```typescript
function evaluateMarker(marker: string, platform: string): boolean
// "sys_platform=='win32'" → true only when platform === "win32"

function missingFor(requirements: string[], installed: string[], platform: string): string[]
// Filters out markers that don't match platform before checking installed
```

### `NodeDetails.tsx` / `NodeCard.tsx`

- NodeCard: orange dot badge when `system_requirements` non-empty.
- NodeDetails: context-aware system requirements section (four variants per env backend, see Phase 2 section above).

---

## Testing Strategy

### Tier 1 — Unit (always run)

- Backend dispatcher returns correct class for each backend string.
- `VenvBackend` generates correct `uv pip install` args including `--extra-index-url`.
- `CondaBackend` generates correct `micromamba create` args with channels.
- `PixiBackend` generates correct `pixi.toml` with channels; routes `@ pypi` packages to `[pypi-dependencies]`.
- `DockerBackend` generates correct Dockerfile for managed mode; injects `noodle-runtime` for dockerfile mode.
- PEP 508 marker parsing — `evaluateMarker("sys_platform=='win32'", "linux")` returns `false`.
- `missingFor()` filters platform-mismatched requirements correctly.
- `system_requirements` round-trips through `@node` → `NodeManifest`.
- NodeDetails renders correct system req variant for each env backend.

### Tier 2 — Integration (skipped if tool absent)

- `VenvBackend.build()` produces a working venv — `skipif not shutil.which("uv")`.
- `CondaBackend.build()` resolves packages from conda-forge — `skipif not shutil.which("micromamba")`.
- `PixiBackend.build()` resolves a package and runs python — `skipif not shutil.which("pixi")`.
- `DockerBackend.build()` (managed) produces a pullable image — `skipif not shutil.which("docker")`.
- `GET /environments/backends` returns correct availability.

### Tier 3 — E2E (CI, tagged `slow`)

- Create conda env → add numpy → run workflow node that imports numpy → assert output.
- Create Docker managed env → run workflow → assert container-spawned run succeeds.

---

## Known Edge Cases

| Edge case | Handling |
|-----------|---------|
| micromamba not found, conda not found | `CondaBackend.build()` raises with "No conda-compatible solver found. Install micromamba: ..." |
| pixi not found | `PixiBackend.build()` raises with "pixi not found. Install from https://pixi.sh" |
| `@ pypi` package not on PyPI | pixi build fails with resolver error; full log shown in env status_detail |
| Platform not supported by pixi (rare) | `_current_platform()` raises `RuntimeError`; env card shows "unsupported platform" |
| Docker daemon not running | `DockerBackend.build()` fails with "Docker daemon unreachable. Start Docker and rebuild." |
| Dockerfile has syntax error | `docker build` returns non-zero; full build log shown in env status_detail |
| Pre-built image not pullable (auth, typo) | `docker pull` returns non-zero; clear error shown in env card |
| Pre-built image missing `noodle-runtime` | Runtime subprocess fails on first run with ModuleNotFoundError; env card shows warning |
| Changing backend on running workflow | Rebuild is queued; in-flight runs complete against old env; new runs use rebuilt env |
| PEP 508 marker syntax error in requirements | Treat as always-applicable (safe fallback); log warning to node registry startup |
| conda package not on any listed channel | Build fails with micromamba error; shown in status_detail with "check channel list" hint |
| Very large Docker image (>10GB) | No size limit enforced by Noodle; pull/build timeout via existing 10-minute build timeout |
