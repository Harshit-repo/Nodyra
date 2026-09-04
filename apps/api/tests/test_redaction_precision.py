"""Redaction must mask secrets without destroying ordinary data.

``_is_sensitive_key`` matched key names by raw substring, so any key
*containing* "auth", "token" or "secret" was masked. That is a lot of ordinary
field names:

  author, authors, author_email, authority, authored_at
  total_tokens, prompt_tokens, completion_tokens, token_count
  secretary, authenticity_score

Found by building a workflow over MCP that fetched a quote and returned its
author. The run succeeded and the agent received:

  {"author": "***REDACTED***", "quote": "Your heart is the size of an ocean..."}

The same value is shown to the user in the run view. Nothing failed, nothing
was logged; the data was simply gone, which is the worst way for it to go.

``total_tokens`` matters twice over: ``model_serving`` emits it and
``model_monitoring`` reads it to compute cost, so the two shipped nodes are on
opposite sides of the mask.

The fix is two rules, and the second is the one that carries the load:

  1. Key names match on word boundaries, not substrings.
  2. A number is never a secret. No credential is an int.

Both directions are gated below, because a redaction test that only checks
"secrets are masked" passes for a function that masks everything.
"""

from __future__ import annotations

import pytest

from app.services.redaction import REDACTED, redact_value

# Keys that MUST be masked. Weakening any of these is a security regression.
SECRET_KEYS = [
    "password",
    "user_password",
    "api_key",
    "apiKey",
    "API_KEY",
    "x-api-key",
    "secret",
    "secret_key",
    "client_secret",
    "private_key",
    "token",
    "access_token",
    "refresh_token",
    "auth_token",
    "bearer_token",
    "auth",
    "authorization",
    "Authorization",
    "bearer",
    "connection_url",
]

# Ordinary data a workflow returns. Masking any of these is data loss.
INNOCENT_KEYS = [
    "author",
    "authors",
    "author_name",
    "author_email",
    "authored_at",
    "authority",
    "authenticity_score",
    "secretary",
    "tokenizer",
    "quote",
    "title",
]


@pytest.mark.parametrize("key", SECRET_KEYS)
def test_secret_keys_are_still_masked(key: str) -> None:
    """The security half. These must not regress while fixing the other half."""
    out = redact_value({key: "hunter2-a-real-looking-value"})
    assert out[key] == REDACTED, f"{key!r} was not masked"


@pytest.mark.parametrize("key", INNOCENT_KEYS)
def test_ordinary_keys_are_left_alone(key: str) -> None:
    """The data half. A mask on these is silent corruption."""
    out = redact_value({key: "Marcus Aurelius"})
    assert out[key] == "Marcus Aurelius", (
        f"{key!r} was masked; the user's own data was destroyed"
    )


@pytest.mark.parametrize(
    "key", ["total_tokens", "prompt_tokens", "completion_tokens", "token_count"]
)
def test_llm_usage_counts_survive(key: str) -> None:
    """model_serving emits these and model_monitoring reads them to compute
    cost. Masking one breaks the pair."""
    out = redact_value({key: 512})
    assert out[key] == 512, f"{key!r} was masked, so cost reporting reads a string"


def test_a_number_is_never_a_secret() -> None:
    """The rule that makes the plural cases safe without loosening key
    matching: no credential is an integer, so a numeric value cannot leak one."""
    out = redact_value({"token": 42, "api_key": 3.5, "password": True})
    assert out == {"token": 42, "api_key": 3.5, "password": True}


def test_a_string_under_a_secret_key_is_still_masked() -> None:
    """Guard the guard for the rule above: it must apply to numbers only."""
    out = redact_value({"token": "ghp_realLookingTokenValue"})
    assert out["token"] == REDACTED


def test_nested_structures_are_walked() -> None:
    payload = {
        "results": [
            {"author": "Seneca", "api_key": "sk-live-123456"},
            {"author": "Epictetus", "meta": {"access_token": "abc123", "hits": 4}},
        ]
    }
    out = redact_value(payload)
    assert out["results"][0]["author"] == "Seneca"
    assert out["results"][0]["api_key"] == REDACTED
    assert out["results"][1]["meta"]["access_token"] == REDACTED
    assert out["results"][1]["meta"]["hits"] == 4


def test_secret_values_are_still_masked_wherever_they_appear() -> None:
    """Key-name matching is only one of the two mechanisms. Known credential
    values are replaced anywhere they occur, including under innocent keys."""
    out = redact_value(
        {"author": "written by sk-live-abc123"}, ["sk-live-abc123"]
    )
    assert "sk-live-abc123" not in out["author"]
