"""SSH-based runner onboarding.

Given a host + SSH credentials, connect once, install ``noodle-runner`` into
the user's Python, register it against a pool with a one-time token, and start
it (systemd if available, else nohup). The machine then connects back over the
WS like any agent runner.

The network step lives in ``onboard_machine`` so tests can monkeypatch it.
"""

from __future__ import annotations

import logging
import shlex

from app.schemas import SSHOnboardRequest

logger = logging.getLogger(__name__)


def _install_script(req: SSHOnboardRequest, api_url: str, token: str, name: str) -> str:
    """Single remote shell script: install, register, start. All interpolated
    values are shell-quoted — the endpoint is admin-only, but quoting closes the
    injection vector regardless."""
    q_api = shlex.quote(api_url)
    q_token = shlex.quote(token)
    q_name = shlex.quote(name)

    nohup = (
        'nohup /usr/bin/env python3 -m noodle_runner_agent.agent start '
        '> "$HOME/noodle-runner.log" 2>&1 &'
    )

    if req.use_systemd:
        # ExecStart uses ``/usr/bin/env python3`` (not $PYBIN) because systemd
        # does not expand shell variables. A single-quoted heredoc keeps the
        # unit literal.
        unit = (
            "[Unit]\n"
            "Description=Noodle Runner Agent\n"
            "After=network.target\n"
            "[Service]\n"
            "ExecStart=/usr/bin/env python3 -m noodle_runner_agent.agent start\n"
            "Restart=always\n"
            f"User={req.username}\n"
            f"Environment=PATH=/home/{req.username}/.local/bin:/usr/bin:/bin\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        start = (
            "if command -v sudo >/dev/null 2>&1 "
            "&& command -v systemctl >/dev/null 2>&1; then\n"
            "  sudo tee /etc/systemd/system/noodle-runner.service "
            ">/dev/null <<'UNIT'\n"
            f"{unit}"
            "UNIT\n"
            "  sudo systemctl daemon-reload\n"
            "  if sudo systemctl enable --now noodle-runner; then\n"
            '    echo "[noodle] started via systemd"\n'
            "  else\n"
            f"    {nohup}\n"
            '    echo "[noodle] systemd failed, started via nohup"\n'
            "  fi\n"
            "else\n"
            f"  {nohup}\n"
            '  echo "[noodle] started via nohup (no sudo/systemctl)"\n'
            "fi\n"
        )
    else:
        start = f'{nohup}\necho "[noodle] started via nohup"\n'

    register = (
        '/usr/bin/env python3 -m noodle_runner_agent.agent register '
        f"--api-url {q_api} --token {q_token} --name {q_name}"
    )
    return (
        "set -e\n"
        "PYBIN=$(command -v python3 || command -v python)\n"
        'if [ -z "$PYBIN" ]; then echo "[noodle] no python found" >&2; exit 1; fi\n'
        '"$PYBIN" -m pip install --user --upgrade noodle-runner\n'
        f"{register}\n"
        f"echo [noodle] registered runner {q_name}\n"
        f"{start}"
    )


async def onboard_machine(
    req: SSHOnboardRequest, api_url: str, token: str, name: str
) -> str:
    """Connect over SSH, install + register + start the runner. Returns the
    combined command log. Raises ``RuntimeError`` on connection or install
    failure (the caller turns it into a 400)."""
    try:
        import asyncssh  # type: ignore[import-untyped]  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "SSH onboarding requires the 'asyncssh' package on the API host"
        ) from exc

    conn_kwargs: dict = {
        "host": req.host,
        "port": req.port,
        "username": req.username,
        "known_hosts": None,  # no pinned host key during onboarding
    }
    if req.auth_method == "password":
        if not req.password:
            raise RuntimeError("password auth selected but no password supplied")
        conn_kwargs["password"] = req.password
    else:
        if not req.private_key:
            raise RuntimeError("key auth selected but no private_key supplied")
        try:
            conn_kwargs["client_keys"] = [
                asyncssh.import_private_key(req.private_key, req.passphrase or None)
            ]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"could not parse private key: {exc}") from exc

    script = _install_script(req, api_url, token, name)
    try:
        async with asyncssh.connect(**conn_kwargs) as conn:
            result = await conn.run(script, check=False)
            log = f"{result.stdout or ''}{result.stderr or ''}".strip()
            if result.exit_status != 0:
                raise RuntimeError(
                    f"remote install failed (exit {result.exit_status}):\n{log}"
                )
            return log
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - asyncssh connection errors
        raise RuntimeError(f"SSH connection/onboarding failed: {exc}") from exc
