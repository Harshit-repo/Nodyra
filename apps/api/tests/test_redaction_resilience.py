"""One unreadable credential must not blind redaction for the whole org.

``_decrypt_credential_values`` builds the exact-match word-list that every
redaction call site depends on — six in the runner alone, plus artifacts,
alerts and operational evidence. Its caller treats it as best-effort and
falls back to ``[]`` on any exception, so a single row that raised took
redaction down for *every* secret in the org, and every other credential's
value then flowed unmasked into logs, node output and the run timeline.

Realistic triggers: a rotated SECRET_KEY, a half-migrated KMS, a corrupt
ciphertext.
"""

import logging

import pytest

from app.services import redaction


class _Credential:
    def __init__(self, cred_id: str, *, encrypted: bool = True) -> None:
        self.id = cred_id
        self.org_id = "org-1"
        self.encrypted_data = "ciphertext" if encrypted else None
        self.encrypted_dek = "wrapped" if encrypted else None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def scalars(self, _stmt):
        return _Result(self._rows)


@pytest.fixture
def decryptor(monkeypatch):
    """Install a decrypt function driven by a per-credential outcome map."""

    def _install(outcomes: dict):
        async def _decrypt(credential, session, **kwargs):
            outcome = outcomes[credential.id]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(redaction, "decrypt_credential_for", _decrypt)

    return _install


async def test_one_raising_credential_does_not_blank_the_others(decryptor) -> None:
    decryptor(
        {
            "good-1": {"token": "aaaa-secret-one"},
            "bad": ValueError("InvalidToken"),
            "good-2": {"token": "bbbb-secret-two"},
        }
    )
    session = _Session([_Credential("good-1"), _Credential("bad"), _Credential("good-2")])

    values = await redaction._decrypt_credential_values(session)

    assert "aaaa-secret-one" in values
    assert "bbbb-secret-two" in values


async def test_the_unreadable_credential_is_reported(decryptor, caplog) -> None:
    """Silence would mean a secret is going out unmasked with nobody told."""
    decryptor({"bad": RuntimeError("KEK unwrap failed")})
    session = _Session([_Credential("bad")])

    with caplog.at_level(logging.WARNING):
        values = await redaction._decrypt_credential_values(session)

    assert values == []
    assert any("credential bad" in r.getMessage() for r in caplog.records)


async def test_a_credential_that_decrypts_to_nothing_is_reported(
    decryptor, caplog
) -> None:
    """decrypt_credential_for is non-strict here and answers {} on failure.

    That is quieter than an exception and just as dangerous: the row holds
    ciphertext, so it has a secret, and that secret is about to appear
    unmasked in run output.
    """
    decryptor({"opaque": {}})
    session = _Session([_Credential("opaque")])

    with caplog.at_level(logging.WARNING):
        await redaction._decrypt_credential_values(session)

    assert any("no readable values" in r.message for r in caplog.records)


async def test_a_credential_with_no_ciphertext_is_not_reported(
    decryptor, caplog
) -> None:
    """An empty credential holds no secret, so it is not a redaction gap."""
    decryptor({"empty": {}})
    session = _Session([_Credential("empty", encrypted=False)])

    with caplog.at_level(logging.WARNING):
        await redaction._decrypt_credential_values(session)

    assert not [r for r in caplog.records if "no readable values" in r.message]


async def test_values_are_ordered_longest_first(decryptor) -> None:
    """Longest-first matters: redacting a short secret first would leave the
    tail of a longer one containing it visible."""
    decryptor({"c": {"short": "abcd", "long": "abcd-efgh-ijkl"}})
    session = _Session([_Credential("c")])

    values = await redaction._decrypt_credential_values(session)

    assert values == ["abcd-efgh-ijkl", "abcd"]
