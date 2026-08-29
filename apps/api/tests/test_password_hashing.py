"""Password hashing: Argon2id with transparent upgrade from PBKDF2 (F-10).

PBKDF2-HMAC-SHA256 at 600k rounds meets the OWASP floor, but it is trivially
parallelisable on a GPU. Argon2id is memory-hard and is the current OWASP first
choice. Existing hashes must keep verifying and be re-hashed silently on the
owner's next successful sign-in — a migration that logs anyone out is a
migration that does not get deployed.
"""

import contextlib

import pytest

from app.services import crypto


@contextlib.asynccontextmanager
async def _session(_client):
    """The session factory the test app is currently bound to."""
    from app.services import runner as runner_module

    async with runner_module.SessionLocal() as session:
        yield session


def test_new_hashes_use_argon2id() -> None:
    stored = crypto.hash_password("correct horse battery staple")
    assert stored.startswith("$argon2id$"), stored[:32]


def test_roundtrip() -> None:
    stored = crypto.hash_password("s3cret-pa55phrase")
    assert crypto.verify_password("s3cret-pa55phrase", stored) is True
    assert crypto.verify_password("wrong", stored) is False


def test_salts_are_unique_per_hash() -> None:
    a = crypto.hash_password("same-password")
    b = crypto.hash_password("same-password")
    assert a != b
    assert crypto.verify_password("same-password", a)
    assert crypto.verify_password("same-password", b)


# ── Backward compatibility: nobody gets locked out ─────────────────────────


def _pbkdf2(password: str, rounds: int) -> str:
    """Reproduce the historical formats byte-for-byte."""
    import base64
    import hashlib
    import os

    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return (
        f"{rounds}:{base64.b64encode(salt).decode()}:{base64.b64encode(digest).decode()}"
    )


def _pbkdf2_legacy(password: str) -> str:
    """The oldest format: ``salt:digest`` with 200k rounds implied."""
    import base64
    import hashlib
    import os

    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"{base64.b64encode(salt).decode()}:{base64.b64encode(digest).decode()}"


@pytest.mark.parametrize(
    "make_hash",
    [
        lambda pw: _pbkdf2(pw, 600_000),
        lambda pw: _pbkdf2(pw, 200_000),
        _pbkdf2_legacy,
    ],
    ids=["pbkdf2-600k", "pbkdf2-200k", "pbkdf2-legacy-2-part"],
)
def test_existing_hashes_still_verify(make_hash) -> None:
    stored = make_hash("legacy-password")
    assert crypto.verify_password("legacy-password", stored) is True
    assert crypto.verify_password("nope", stored) is False


@pytest.mark.parametrize(
    "make_hash",
    [lambda pw: _pbkdf2(pw, 600_000), _pbkdf2_legacy],
    ids=["pbkdf2-600k", "pbkdf2-legacy-2-part"],
)
def test_outdated_hashes_are_flagged_for_rehash(make_hash) -> None:
    assert crypto.needs_rehash(make_hash("pw")) is True


def test_current_hashes_are_not_flagged() -> None:
    assert crypto.needs_rehash(crypto.hash_password("pw")) is False


def test_garbage_never_verifies_and_never_raises() -> None:
    for junk in ("", "not-a-hash", "$argon2id$truncated", "a:b:c:d", "1:!:!"):
        assert crypto.verify_password("pw", junk) is False


def test_needs_rehash_is_safe_on_garbage() -> None:
    """A corrupt hash must not crash the login path; flag it for replacement."""
    assert crypto.needs_rehash("not-a-hash") is True


# ── Login integration: upgrade and enumeration resistance ──────────────────


async def test_login_upgrades_a_legacy_hash_in_place(client):
    """A user stored under PBKDF2 signs in normally and leaves with Argon2id."""
    from sqlalchemy import select

    from app.models import User

    email, password = "legacy@example.test", "legacy-password-1"
    resp = await client.post(
        "/auth/register", json={"email": email, "password": password}
    )
    assert resp.status_code in (200, 201), resp.text

    # Rewrite the stored hash to the historical format, as an upgraded
    # deployment's existing rows would be.
    async with _session(client) as session:
        user = await session.scalar(select(User).where(User.email == email))
        user.password_hash = _pbkdf2(password, 600_000)
        await session.commit()

    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text

    async with _session(client) as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user.password_hash.startswith("$argon2id$"), user.password_hash[:20]

    # And the upgraded hash still authenticates on the next attempt.
    assert (
        await client.post("/auth/login", json={"email": email, "password": password})
    ).status_code == 200


async def test_login_rejects_a_wrong_password_after_upgrade(client):
    email, password = "upgrade@example.test", "upgrade-password-1"
    await client.post("/auth/register", json={"email": email, "password": password})
    await client.post("/auth/login", json={"email": email, "password": password})

    resp = await client.post(
        "/auth/login", json={"email": email, "password": "not-the-password"}
    )
    assert resp.status_code == 401


def test_unknown_user_pays_the_same_kdf_cost_as_a_real_one():
    """Regression: the stand-in hash used for a missing account was a bcrypt
    literal that no Nodyra hasher recognised, so it was rejected on a format
    check in microseconds while a real account paid the full KDF cost — a
    timing oracle for account enumeration.

    Asserting on the hash's *format* rather than on wall-clock keeps this
    deterministic on a shared CI runner.
    """
    from app.routers import auth

    assert auth._DUMMY_HASH.startswith("$argon2id$"), auth._DUMMY_HASH[:24]
    # It must be a hash verify_password genuinely processes, not one it
    # short-circuits: a non-match here costs the full Argon2id evaluation.
    assert crypto.verify_password("anything", auth._DUMMY_HASH) is False
    assert crypto.needs_rehash(auth._DUMMY_HASH) is False
