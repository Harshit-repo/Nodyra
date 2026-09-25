"""The documented first run must actually work.

``docs/getting-started.md`` told a new user to hand-write ``deploy/.env`` with
four settings. ``deploy/docker-compose.yml`` hard-requires three variables, and
``MINIO_ROOT_PASSWORD`` was not among the four. Following the guide literally,
the very first command failed before starting a single container:

    error while interpolating services.api.environment.AWS_SECRET_ACCESS_KEY:
    required variable MINIO_ROOT_PASSWORD is missing a value

``deploy/.env.example`` had it right the whole time. The two documents
disagreed, and the one a newcomer reads first was the broken one — on the page
that promises first value in about five minutes.

These tests are static: they read the compose file and the docs, so they run
everywhere without Docker.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "deploy" / "docker-compose.yml"
ENV_EXAMPLE = ROOT / "deploy" / ".env.example"
GETTING_STARTED = ROOT / "docs" / "getting-started.md"

pytestmark = pytest.mark.skipif(
    not COMPOSE.exists(), reason="deploy/ is not present in this checkout"
)

#: ``${VAR:?message}`` — compose refuses to start when VAR is unset.
REQUIRED = re.compile(r"\$\{([A-Z_]+):\?")


def _required_variables() -> set[str]:
    return set(REQUIRED.findall(COMPOSE.read_text(encoding="utf-8")))


def test_compose_requires_the_variables_we_think_it_does():
    """Guard the guard: if the hard-required set ever empties, every check
    below would pass by asserting nothing."""
    required = _required_variables()
    assert required, "no ${VAR:?...} variables found — has the compose file changed shape?"
    assert "MINIO_ROOT_PASSWORD" in required, (
        "the variable this suite was written for is no longer required; if that "
        "is deliberate, these tests need revisiting"
    )


def test_the_example_env_covers_every_required_variable():
    """``.env.example`` is what the guide now tells people to copy, so it has to
    be sufficient on its own."""
    example = ENV_EXAMPLE.read_text(encoding="utf-8")
    declared = {
        line.split("=", 1)[0].strip().lstrip("#").strip()
        for line in example.splitlines()
        if "=" in line
    }
    missing = sorted(_required_variables() - declared)
    assert not missing, (
        f"deploy/.env.example does not mention {missing}, so copying it is not "
        f"enough to start the stack"
    )


def test_getting_started_does_not_hand_roll_an_incomplete_env():
    """The specific failure: a fenced block presented as the whole .env that
    omits a hard-required variable.

    Any block that assigns two or more of the required variables is being
    offered as 'here is your .env' — so it must cover all of them.
    """
    required = _required_variables()
    text = GETTING_STARTED.read_text(encoding="utf-8")

    for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
        assigned = {
            line.split("=", 1)[0].strip()
            for line in block.splitlines()
            if "=" in line and not line.strip().startswith("#")
        }
        named = required & assigned
        if len(named) < 2:
            continue  # a snippet about one setting, not a whole .env
        missing = sorted(required - assigned)
        assert not missing, (
            f"docs/getting-started.md shows an env block naming {sorted(named)} "
            f"but omitting {missing}. Following it literally, `docker compose` "
            f"fails before starting anything."
        )


def test_getting_started_points_at_the_example_file():
    """The durable fix is to send people to the file that is kept in step with
    compose, rather than to a snippet that has to be maintained twice."""
    text = GETTING_STARTED.read_text(encoding="utf-8")
    assert ".env.example" in text, (
        "getting-started no longer references deploy/.env.example; a hand-written "
        "env block is exactly how this broke"
    )
