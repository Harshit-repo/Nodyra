"""The pricing page must not advertise entitlements the licence does not grant.

Marketing copy and ``TIER_DEFAULTS`` drifted apart with nothing to catch it. The
page sold Pro on "Audit log export" and "RBAC" — both Enterprise-only in
``licensing.py`` — listed sandboxed execution as a paid upgrade when Community
already has it, and offered SCIM provisioning, which does not exist anywhere in
the codebase.

That is worse than an ordinary docs bug: someone pays $49, does not get the
feature, and asks for their money back. The fix is to make the page a projection
of the code rather than a parallel description of it.

The page is checked by *tier and feature*, not by prose. Copy stays free to
change; a claim about a gated capability does not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.licensing import TIER_DEFAULTS, Edition, Feature

PAGE = Path(__file__).resolve().parents[3] / "brand" / "homepage" / "nodyra.html"

pytestmark = pytest.mark.skipif(
    not PAGE.exists(), reason="marketing page is not present in this checkout"
)

# Phrases the page uses for each licence-gated feature. A card may only make a
# *positive* claim (a tick) for a feature its tier actually grants.
FEATURE_PHRASES: dict[Feature, tuple[str, ...]] = {
    Feature.SANDBOX: ("sandboxed execution",),
    Feature.OBSERVABILITY: ("observability",),
    Feature.GIT_SYNC: ("git sync",),
    Feature.SSO: ("sso", "saml"),
    Feature.AUDIT_LOGS: ("audit log",),
    Feature.ADVANCED_RBAC: ("advanced rbac", "custom roles"),
    Feature.EXTERNAL_KMS: ("external kms",),
    Feature.MULTI_TENANCY: ("multi-tenancy",),
    Feature.DEDICATED_POOLS: ("dedicated runner pool",),
}

CARD_EDITIONS = {
    "community": Edition.COMMUNITY,
    "pro": Edition.PRO,
    "enterprise": Edition.ENTERPRISE,
}


def _pricing_section() -> str:
    html = PAGE.read_text(encoding="utf-8")
    start = html.index('<section id="pricing"')
    return html[start : html.index("</section>", start)]


def _cards() -> dict[Edition, str]:
    """Split the pricing grid into one chunk of HTML per tier."""
    section = _pricing_section()
    chunks = re.split(r'<!--\s*(Community|Pro|Enterprise)[^>]*?-->', section)
    out: dict[Edition, str] = {}
    # re.split with one capture group yields [pre, tag, body, tag, body, ...],
    # so the two slices are always the same length.
    for name, body in zip(chunks[1::2], chunks[2::2], strict=True):
        out[CARD_EDITIONS[name.strip().lower()]] = body
    return out


def _claims(card: str) -> tuple[list[str], list[str]]:
    """Return (asserted, excluded) feature rows for one card.

    A row is a claim only when it carries the tick icon; the muted rows with the
    dash icon are deliberate statements of *absence* and are checked separately.
    """
    asserted, excluded = [], []
    for row in re.findall(r'<div class="price-feat[^"]*">(.*?)</div>', card, re.S):
        text = re.sub(r"<[^>]+>", " ", row)
        text = re.sub(r"\s+", " ", text).strip().lower()
        (excluded if "price-feat-muted" in row or "var(--line)" in row else asserted).append(text)
    # The muted class sits on the wrapping div, which the regex above consumed as
    # part of the row, so re-derive from the raw card to be certain.
    muted = [
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row)).strip().lower()
        for row in re.findall(
            r'<div class="price-feat price-feat-muted">(.*?)</div>\s*</div>', card, re.S
        )
    ]
    asserted = [row for row in asserted if row not in muted]
    return asserted, muted


def test_every_card_was_found():
    """Guard the guard: if the markup is restructured, the checks below must not
    silently pass by matching nothing."""
    cards = _cards()
    assert set(cards) == set(Edition), cards.keys()
    for edition, card in cards.items():
        asserted, _ = _claims(card)
        assert len(asserted) >= 4, (edition, asserted)


@pytest.mark.parametrize("edition", list(Edition), ids=lambda e: e.value)
def test_a_card_only_claims_features_its_tier_grants(edition):
    granted = TIER_DEFAULTS[edition][0]
    asserted, _ = _claims(_cards()[edition])

    for feature, phrases in FEATURE_PHRASES.items():
        if feature in granted:
            continue
        for row in asserted:
            assert not any(p in row for p in phrases), (
                f"the {edition.value} card claims {feature.value!r} "
                f"({row!r}), but TIER_DEFAULTS grants it only to "
                f"{[e.value for e in Edition if feature in TIER_DEFAULTS[e][0]]}"
            )


@pytest.mark.parametrize("edition", list(Edition), ids=lambda e: e.value)
def test_a_card_does_not_disclaim_a_feature_it_actually_has(edition):
    """The inverse error, and the one that costs sales: Community showed
    sandboxed execution as unavailable when every Community install has it."""
    granted = TIER_DEFAULTS[edition][0]
    _, excluded = _claims(_cards()[edition])

    for feature, phrases in FEATURE_PHRASES.items():
        if feature not in granted:
            continue
        for row in excluded:
            assert not any(p in row for p in phrases), (
                f"the {edition.value} card shows {feature.value!r} as excluded "
                f"({row!r}), but the tier grants it"
            )


@pytest.mark.parametrize("edition", list(Edition), ids=lambda e: e.value)
def test_the_advertised_caps_match_the_licence(edition):
    """Seats, environments, runners and deployments are the real difference
    between the tiers, so the numbers on the page have to be the real ones."""
    limits = TIER_DEFAULTS[edition][1]
    card = _cards()[edition]

    shown = {
        label.strip().lower().rstrip("s"): value.strip()
        for value, label in re.findall(
            r'<span class="price-limit-n">(.*?)</span>'
            r'\s*<span class="price-limit-l">(.*?)</span>',
            card,
            re.S,
        )
    }
    assert shown, f"the {edition.value} card shows no resource caps"

    for key, actual in (
        ("user", limits.seats),
        ("environment", limits.environments),
        ("runner", limits.runners),
        ("deployment", limits.deployments),
    ):
        assert key in shown, (edition.value, key, shown)
        expected = "&infin;" if actual == 0 else str(actual)
        assert shown[key] == expected, (
            f"the {edition.value} card advertises {shown[key]} {key}s "
            f"but the licence allows {expected}"
        )


def test_the_page_does_not_advertise_capabilities_that_do_not_exist():
    """SCIM provisioning was on the Enterprise card and implemented nowhere.

    This list is for capabilities that were once advertised without existing. If
    one is genuinely built, delete its entry here in the same change.
    """
    unimplemented = ("scim",)
    section = _pricing_section().lower()
    for term in unimplemented:
        assert term not in section, (
            f"the pricing page advertises {term!r}, which is not implemented"
        )


def test_the_advertised_node_count_matches_the_registry():
    """The page names a node count. Nodes get added; the number on the page does
    not follow on its own, and "512 nodes" quietly becoming false is the same
    class of error as the entitlement drift above.

    Two honest forms are allowed. An exact "512 nodes" must equal the registry.
    An approximate "500+ nodes" is a lower bound and must merely hold —
    ``actual >= 500`` — which is the *better* claim precisely because it does
    not go false the moment a 513th node lands (the drift this test exists to
    catch). What stays forbidden is claiming more than exists: "600+ nodes"
    with 512 in the registry fails either way.

    The count runs in a subprocess. ``registry`` is process-global and other
    tests register their own nodes into it, so counting in-process makes this
    assertion depend on test order — it passed alone and failed in the suite,
    reporting 513.
    """
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c",
         "import nodyra_nodes; from nodyra.sdk import registry;"
         " print(len(registry.manifests()))"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    actual = int(proc.stdout.strip())

    # Each claim is (count, is_lower_bound): "500+ nodes" -> (500, True),
    # "512 nodes" -> (512, False).
    claims = [
        (int(n.replace(",", "")), bool(plus))
        for n, plus in re.findall(r"([0-9][0-9,]*)\s*(\+?)\s+nodes\b", _pricing_section())
    ]
    assert claims, "the pricing page no longer states a node count"
    for count, is_lower_bound in claims:
        if is_lower_bound:
            assert actual >= count, (
                f"the pricing page advertises {count}+ nodes; "
                f"the registry has only {actual}"
            )
        else:
            assert count == actual, (
                f"the pricing page advertises exactly {count} nodes; "
                f"the registry has {actual}"
            )


def test_no_placeholder_survives_into_the_marketing_pages():
    """``REPLACE-ORG/REPLACE-REPO`` sat in eight links, including the one the
    fair-code notice points at. A launch page whose License link 404s is a worse
    first impression than no link at all, and nothing was watching for it."""
    markers = ("REPLACE-ORG", "REPLACE-REPO", "TODO:", "lorem ipsum", "FIXME")
    offenders = [
        (page.name, marker)
        for page in PAGE.parent.glob("*.html")
        for marker in markers
        if marker.lower() in page.read_text(encoding="utf-8", errors="replace").lower()
    ]
    assert not offenders, offenders


# ── Every marketing page, not just the one that had the bug ────────────────
#
# index.html is a second, independently written landing page — it is what a
# static host serves by default — and it carried the same errors on its own:
# sandboxing badged Pro, 2 seats instead of 5, 3 deployments instead of 10.
#
# Naive "nearest tier word above" attribution does not work on these pages.
# The Pro card opens with "Everything in <b>Community</b>, plus:", so a bare
# proximity search reads every Pro bullet as a Community one and the check
# silently passes. Both places a tier is asserted are matched explicitly
# instead.

# These landing pages advertise pricing tiers. The adjacent docs.html is a
# deployment guide, so requiring pricing cards there would test the wrong surface.
PAGES = [PAGE.parent / name for name in ("index.html", "nodyra.html")]

#: A badge pinned to a feature card, e.g. ``<span class="tag pro">Pro</span>``
#: or ``<span class="pro-badge">Pro</span>``. Whatever follows it, up to the
#: end of that card's heading, is being sold as belonging to that tier.
TIER_BADGE = re.compile(
    r'<span[^>]*class="[^"]*(?:badge|tag)[^"]*"[^>]*>\s*'
    r"(Community|Pro|Enterprise)\s*</span>\s*(?:<[^>]+>\s*)*([^<]{0,120})",
    re.I,
)

#: The title of a pricing card: a real heading, or a div whose class names it
#: as the tier name. Deliberately excludes ``<b>Community</b>`` in prose.
TIER_TITLE = re.compile(
    r'(?:<h[1-6][^>]*>|<div[^>]*class="[^"]*(?:tname|tier-name)[^"]*"[^>]*>)\s*'
    r"(Community|Pro|Enterprise)\s*<",
    re.I,
)


def _granted(edition: Edition) -> frozenset[Feature]:
    return frozenset(TIER_DEFAULTS[edition][0])


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_a_feature_badged_with_a_tier_is_granted_by_that_tier(page):
    """nodyra.html's platform section badged "Sandboxed execution" as Pro, and
    index.html did the same. Sandboxing is Community: badging it Pro understates
    the free tier and makes the first Pro invoice look like it bought nothing.

    It also badged "Runner Pools" as Pro, when every tier gets runners and only
    the count differs.
    """
    html = page.read_text(encoding="utf-8")
    seen = 0

    for match in TIER_BADGE.finditer(html):
        edition = Edition(match.group(1).lower())
        subject = match.group(2).strip().lower()
        if not subject:
            continue
        seen += 1
        for feature, phrases in FEATURE_PHRASES.items():
            if feature in _granted(edition):
                continue
            for phrase in phrases:
                assert phrase not in subject, (
                    f"{page.name}: {subject!r} is badged {edition.value}, but "
                    f"{feature.value} is granted to "
                    f"{[e.value for e in Edition if feature in _granted(e)]}"
                )
        # The inverse, which is the error both pages actually made.
        for feature in _granted(Edition.COMMUNITY):
            if edition is Edition.COMMUNITY:
                continue
            for phrase in FEATURE_PHRASES[feature]:
                assert phrase not in subject, (
                    f"{page.name}: {subject!r} is badged {edition.value}, but "
                    f"{feature.value} is a Community entitlement"
                )

    assert seen, f"{page.name}: no tier badges found — has the markup changed?"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_a_pricing_card_does_not_sell_a_community_feature_as_an_upgrade(page):
    """The same claim in the other place it is made: the bullet list under a
    pricing card's title."""
    html = page.read_text(encoding="utf-8")
    titles = list(TIER_TITLE.finditer(html))
    assert titles, f"{page.name}: no pricing card titles found"

    for index, title in enumerate(titles):
        edition = Edition(title.group(1).lower())
        if edition is Edition.COMMUNITY:
            continue
        end = titles[index + 1].start() if index + 1 < len(titles) else len(html)
        card = html[title.end() : end].lower()
        # "Everything in Community" is a legitimate reference, not a claim.
        card = re.sub(r"everything in \s*(<[^>]+>\s*)?community", " ", card)

        for feature in _granted(Edition.COMMUNITY):
            for phrase in FEATURE_PHRASES[feature]:
                assert phrase not in card, (
                    f"{page.name}: the {edition.value} card sells {phrase!r} as "
                    f"an upgrade, but {feature.value} is a Community entitlement"
                )


def test_the_secondary_landing_page_states_the_real_caps():
    """index.html is a second, independently written landing page — it is what a
    static host serves by default — and it drifted on its own: 2 seats instead
    of 5, 3 deployments instead of 10.

    Its caps are prose, so they are asserted as exact strings built from
    TIER_DEFAULTS rather than parsed out of the markup.
    """
    page = PAGE.parent / "index.html"
    if not page.exists():
        pytest.skip("index.html is not present in this checkout")
    html = page.read_text(encoding="utf-8")

    community = TIER_DEFAULTS[Edition.COMMUNITY][1]
    pro = TIER_DEFAULTS[Edition.PRO][1]

    expected = [
        f"{community.environments} environments · {community.runners} runner"
        f" · {community.seats} seats",
        f"Up to {community.deployments} active deployments",
        f"{pro.environments} environments · {pro.runners} runners"
        f" · {pro.seats} seats",
    ]
    missing = [line for line in expected if line not in html]
    assert not missing, (
        f"index.html no longer states these caps, or states them wrongly: {missing}"
    )


# ── The same claim, in the other places a buyer reads it ──────────────────

REPO = PAGE.parents[2]

#: Files a prospective customer reads before they read any code. SCIM was
#: promised in four of them and implemented in none.
CUSTOMER_FACING = ("README.md", "docs/deployment.md", "SECURITY.md")


def _unimplemented_terms() -> tuple[str, ...]:
    """Capabilities that were advertised without existing.

    If one is genuinely built, delete its entry here in the same change — the
    test then starts allowing the word, which is the point.
    """
    return ("scim",)


@pytest.mark.parametrize("relative", CUSTOMER_FACING)
def test_customer_facing_docs_do_not_promise_unbuilt_capabilities(relative):
    """The marketing pages were not the only place the promise appeared.

    README.md listed SCIM among the "Enterprise-gated features ... covered by
    the Nodyra Enterprise License", and docs/deployment.md put it in the tier
    matrix with a tick. Neither is implemented: the string does not appear
    anywhere under apps/api.
    """
    path = REPO / relative
    if not path.exists():
        pytest.skip(f"{relative} is not present in this checkout")

    text = path.read_text(encoding="utf-8", errors="replace").lower()
    for term in _unimplemented_terms():
        assert term not in text, (
            f"{relative} advertises {term!r}, which is not implemented anywhere "
            f"in apps/api — say so, or build it"
        )


def test_the_unimplemented_list_is_still_accurate():
    """Guard the guard.

    A term stays on the list only while it is genuinely absent from the API. If
    someone implements one and forgets this file, the test above would keep
    banning a word the product has earned the right to use.
    """
    api = REPO / "apps" / "api" / "app"
    sources = "\n".join(
        p.read_text(encoding="utf-8", errors="replace").lower()
        for p in api.rglob("*.py")
    )
    for term in _unimplemented_terms():
        assert term not in sources, (
            f"{term!r} now appears in apps/api/app — it may have been "
            f"implemented. Remove it from the unimplemented list."
        )
