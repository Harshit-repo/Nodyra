# nodyra-runner

Remote runner agent for [Nodyra](https://github.com/Harshit-repo/nodyra) — a
self-hostable, Python-native workflow automation platform.

The agent registers a machine as a worker in a Nodyra runner pool, connects to
the Nodyra API over a WebSocket, and executes assigned workflow runs in isolated
per-environment virtualenvs. It streams run events back to the API and supports
cancellation and brokered sub-workflow calls.

## Install

```bash
# Published release:
pip install nodyra-runner

# Self-hosted instance (wheel served from the API's internal index):
pip install --find-links https://nodyra.example.com/runner-pools/wheels/ nodyra-runner
```

## Usage

Generate a registration token in the Nodyra UI (Runner Pools → your pool →
*Add runner*), then register and start the agent:

```bash
# Register this machine against your Nodyra API using the one-time token.
nodyra-runner register --api-url https://nodyra.example.com --token <TOKEN> --name my-runner

# Start accepting workflow runs (reconnects automatically on drop).
nodyra-runner start --max-concurrent 4
```

Configuration is stored under `~/.nodyra-runner/config.json`. Override the data
directory with the `NODYRA_RUNNER_DATA` environment variable.

## Requirements

- Python 3.12+
- Network access to your Nodyra API (HTTP for registration, WS/WSS for the run
  loop)
