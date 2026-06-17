# noodle-runner

Remote runner agent for [Noodle](https://github.com/Harshit-repo/noodle) — a
self-hostable, Python-native workflow automation platform.

The agent registers a machine as a worker in a Noodle runner pool, connects to
the Noodle API over a WebSocket, and executes assigned workflow runs in isolated
per-environment virtualenvs. It streams run events back to the API and supports
cancellation and brokered sub-workflow calls.

## Install

```bash
# Published release:
pip install noodle-runner

# Self-hosted instance (wheel served from the API's internal index):
pip install --find-links https://noodle.example.com/runner-pools/wheels/ noodle-runner
```

## Usage

Generate a registration token in the Noodle UI (Runner Pools → your pool →
*Add runner*), then register and start the agent:

```bash
# Register this machine against your Noodle API using the one-time token.
noodle-runner register --api-url https://noodle.example.com --token <TOKEN> --name my-runner

# Start accepting workflow runs (reconnects automatically on drop).
noodle-runner start --max-concurrent 4
```

Configuration is stored under `~/.noodle-runner/config.json`. Override the data
directory with the `NOODLE_RUNNER_DATA` environment variable.

## Requirements

- Python 3.12+
- Network access to your Noodle API (HTTP for registration, WS/WSS for the run
  loop)
