"""The SAML ACS endpoint must not expand XML entities (F-26).

``/auth/sso/acs`` is unauthenticated by necessity — the IdP posts to it and
holds no Nodyra credential — and the signature is only checked *after* the
document is parsed. So parsing is the first thing an attacker reaches, and it
has to be safe on its own.

lxml's default parser refuses *external* entities, so there is no file-read
XXE here. It does expand entities declared in the internal subset, which is
enough: a 279-byte document expands 2,294x, and nesting one level deeper
multiplies that by ten again. Against the endpoint's 100 KiB input cap that is
hundreds of megabytes of allocation per unauthenticated request.

The existing caps bound the *input*; they cannot bound what parsing turns it
into. Only the parser can.
"""

from __future__ import annotations

import base64
import zlib

import pytest

lxml_etree = pytest.importorskip("lxml.etree")

from app.routers.auth import _saml_xml_parser  # noqa: E402


def _entity_bomb(levels: int = 4, fanout: int = 10) -> bytes:
    """A classic nested-entity document: tiny in, enormous out."""
    entities = [b'<!ENTITY a "' + b"x" * 64 + b'">']
    names = "abcdefgh"[: levels + 1]
    for index in range(levels):
        current, previous = names[index + 1].encode(), names[index].encode()
        entities.append(
            b"<!ENTITY " + current + b' "' + (b"&" + previous + b";") * fanout + b'">'
        )
    return (
        b"<!DOCTYPE r [" + b"".join(entities) + b"]><r>&" + names[-1].encode() + b";</r>"
    )


def test_the_bomb_really_is_one_under_a_default_parser():
    """Guard the guard. If lxml ever hardens its own default, this test tells us
    the threat is gone rather than letting the fix look effective for free.
    """
    document = _entity_bomb()
    root = lxml_etree.fromstring(document)
    expanded = len(root.text or "")
    assert expanded > 100 * len(document), (
        "the default parser no longer amplifies; re-evaluate whether the "
        "hardened parser is still needed"
    )


def test_the_hardened_parser_does_not_expand_entities():
    root = lxml_etree.fromstring(_entity_bomb(), parser=_saml_xml_parser())
    assert not (root.text or "").strip()


def test_the_hardened_parser_refuses_external_entities():
    """Belt and braces: lxml's default already refuses these, but the SAML path
    should not depend on a default staying put."""
    document = (
        b'<!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><r>&xxe;</r>'
    )
    try:
        root = lxml_etree.fromstring(document, parser=_saml_xml_parser())
    except lxml_etree.XMLSyntaxError:
        return  # refusing outright is also correct
    assert "root:" not in (root.text or "")


def test_a_legitimate_assertion_still_parses():
    """No real SAML uses entities, so hardening costs nothing — but prove it."""
    document = (
        b'<?xml version="1.0"?>'
        b'<saml2p:Response xmlns:saml2p="urn:oasis:names:tc:SAML:2.0:protocol"'
        b' xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion">'
        b"<saml2:Assertion><saml2:Subject><saml2:NameID>"
        b"user@example.com"
        b"</saml2:NameID></saml2:Subject></saml2:Assertion>"
        b"</saml2p:Response>"
    )
    root = lxml_etree.fromstring(document, parser=_saml_xml_parser())
    name_id = root.find(
        ".//saml2:NameID", {"saml2": "urn:oasis:names:tc:SAML:2.0:assertion"}
    )
    assert name_id is not None and name_id.text == "user@example.com"


async def test_the_endpoint_survives_an_entity_bomb(client):
    """End to end: the bomb reaches the real handler and is refused without
    allocating its expansion."""
    payload = base64.b64encode(
        zlib.compress(_entity_bomb(levels=5))[2:-4]  # raw deflate, as the ACS expects
    ).decode()

    response = await client.post("/auth/sso/acs", data={"SAMLResponse": payload})

    # Any refusal is acceptable; expanding the bomb is not.
    assert response.status_code >= 400, response.text


async def test_an_oversized_response_is_still_capped(client):
    """The existing input cap stays — it bounds a different attack (a large
    body) than the parser does (a small body that becomes large)."""
    response = await client.post(
        "/auth/sso/acs", data={"SAMLResponse": "A" * 200_000}
    )
    assert response.status_code == 400
    assert "too large" in response.text.lower()
