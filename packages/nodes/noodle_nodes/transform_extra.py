"""Extra transform/utility nodes.

These wrap small, dependable libraries (jmespath, jinja2, pyyaml,
markdown, beautifulsoup4, jsonschema, cryptography) — the kind of
operations users want for routine pipeline work but don't want to write
a Code node for. Each node has a single wired ``input`` port.
"""

from __future__ import annotations

import base64
import gzip
import json
import xml.etree.ElementTree as ET
from typing import Any

from noodle.sdk import node

# ============================================================================
# JSON path / templating
# ============================================================================


@node(
    name="JMESPath Query",
    id="jmespath_query",
    category="Transform",
    icon="braces",
    params={
        "expression": {
            "placeholder": "items[?status=='open'].id",
            "description": (
                "JMESPath expression run against the wired input. See "
                "jmespath.org for the syntax."
            ),
        },
    },
)
def jmespath_query(input: Any = None, expression: str = "") -> Any:
    """Extract values from JSON-like data using a JMESPath expression."""
    import jmespath

    if not expression:
        return input
    return jmespath.search(expression, input)


@node(
    name="Template (Jinja)",
    id="template_render",
    category="Transform",
    icon="braces",
    params={
        "template": {
            "placeholder": "Hello {{ name }}, you have {{ items|length }} items.",
            "description": (
                "Jinja2 template. The wired input is exposed as the "
                "top-level context."
            ),
            "multiline": True,
        },
        "autoescape": {
            "description": "Escape HTML in interpolated values (useful for emails).",
        },
    },
)
def template_render(
    input: Any = None,
    template: str = "",
    autoescape: bool = False,
) -> str:
    """Render a Jinja2 template against the input as context."""
    from jinja2 import Environment, StrictUndefined

    env = Environment(autoescape=autoescape, undefined=StrictUndefined)
    rendered = env.from_string(template or "")
    context = input if isinstance(input, dict) else {"input": input}
    return rendered.render(**context)


# ============================================================================
# CSV / XML / YAML
# ============================================================================

# NOTE: CSV Parse / CSV Write were intentionally moved to
# ``noodle_nodes.datasets`` and now return DatasetRef / ArtifactRef rather
# than inline rows / strings. See the dataset engine plan.


def _xml_to_dict(element: ET.Element) -> Any:
    """Convert an ElementTree element into a dict-of-dicts shape."""
    children: dict[str, list[Any]] = {}
    for child in element:
        children.setdefault(child.tag, []).append(_xml_to_dict(child))
    payload: dict[str, Any] = {}
    if element.attrib:
        payload["@attrib"] = dict(element.attrib)
    if element.text and element.text.strip():
        payload["#text"] = element.text.strip()
    for tag, values in children.items():
        payload[tag] = values[0] if len(values) == 1 else values
    if not payload:
        return element.text.strip() if element.text else None
    return payload


@node(
    name="XML Parse",
    id="xml_parse",
    category="Transform",
    icon="braces",
    params={
        "text": {
            "description": "XML text to parse. Falls back to the wired input if blank.",
            "multiline": True,
        },
    },
)
def xml_parse(input: Any = None, text: str = "") -> dict:
    """Parse an XML string into a dict shaped by tag name."""
    payload = text or (str(input) if input is not None else "")
    if not payload.strip():
        return {}
    root = ET.fromstring(payload)
    return {root.tag: _xml_to_dict(root)}


@node(
    name="YAML Parse",
    id="yaml_parse",
    category="Transform",
    icon="braces",
    params={
        "text": {
            "description": "YAML text. Falls back to the wired input if blank.",
            "multiline": True,
        },
    },
)
def yaml_parse(input: Any = None, text: str = "") -> Any:
    """Parse YAML into a Python value."""
    import yaml

    payload = text or (str(input) if input is not None else "")
    if not payload.strip():
        return None
    return yaml.safe_load(payload)


@node(
    name="YAML Dump",
    id="yaml_dump",
    category="Transform",
    icon="braces",
    params={
        "sort_keys": {
            "description": "Sort mapping keys alphabetically.",
        },
    },
)
def yaml_dump(input: Any = None, sort_keys: bool = False) -> str:
    """Serialize a Python value into YAML."""
    import yaml

    return yaml.safe_dump(input, sort_keys=sort_keys, default_flow_style=False)


# ============================================================================
# Markdown / HTML
# ============================================================================


@node(
    name="Markdown to HTML",
    id="markdown_to_html",
    category="Transform",
    icon="page",
    params={
        "text": {
            "description": "Markdown text. Falls back to the wired input if blank.",
            "multiline": True,
        },
    },
)
def markdown_to_html(input: Any = None, text: str = "") -> str:
    """Render Markdown into HTML."""
    import markdown as md

    payload = text or (str(input) if input is not None else "")
    return md.markdown(payload, extensions=["fenced_code", "tables"])


@node(
    name="HTML Extract",
    id="html_extract",
    category="Transform",
    icon="page",
    params={
        "html": {
            "description": "HTML to parse. Falls back to the wired input if blank.",
            "multiline": True,
        },
        "selector": {
            "placeholder": "article h2",
            "description": "CSS selector. Returns a list of matched element text.",
        },
        "attribute": {
            "group": "Options",
            "placeholder": "href",
            "description": (
                "Optional element attribute to return instead of text "
                "(e.g. 'href' for links)."
            ),
        },
    },
)
def html_extract(
    input: Any = None,
    html: str = "",
    selector: str = "",
    attribute: str = "",
) -> list[str]:
    """Extract elements from HTML using a CSS selector."""
    from bs4 import BeautifulSoup

    payload = html or (str(input) if input is not None else "")
    if not payload.strip() or not selector:
        return []
    soup = BeautifulSoup(payload, "html.parser")
    matches = soup.select(selector)
    if attribute:
        return [str(m.get(attribute, "")) for m in matches]
    return [m.get_text(strip=True) for m in matches]


# ============================================================================
# Validation
# ============================================================================


@node(
    name="JSON Schema Validate",
    id="json_schema_validate",
    category="Transform",
    icon="braces",
    params={
        "schema": {
            "description": "JSON Schema as a JSON string.",
            "multiline": True,
        },
    },
)
def json_schema_validate(input: Any = None, schema: str = "") -> dict:
    """Validate the input against a JSON Schema; return a result envelope."""
    import jsonschema
    from jsonschema import Draft202012Validator

    try:
        parsed_schema = json.loads(schema) if schema else {}
    except json.JSONDecodeError as exc:
        return {"valid": False, "errors": [f"schema is not valid JSON: {exc}"]}

    validator = Draft202012Validator(parsed_schema)
    errors = [
        f"{'/'.join(str(p) for p in err.absolute_path)}: {err.message}"
        for err in validator.iter_errors(input)
    ]
    if errors:
        return {"valid": False, "errors": errors}
    # Confirm by raising on critical errors (e.g. schema is itself invalid).
    try:
        jsonschema.Draft202012Validator.check_schema(parsed_schema)
    except jsonschema.SchemaError as exc:
        return {"valid": False, "errors": [f"schema invalid: {exc.message}"]}
    return {"valid": True, "errors": []}


# ============================================================================
# Compression / Encryption
# ============================================================================


@node(
    name="GZIP Compress",
    id="gzip_compress",
    category="Transform",
    icon="import",
    params={
        "text": {
            "description": "Text to compress. Falls back to the wired input if blank.",
            "multiline": True,
        },
    },
)
def gzip_compress(input: Any = None, text: str = "") -> str:
    """GZIP-compress text and return a base64 string."""
    payload = text or (str(input) if input is not None else "")
    compressed = gzip.compress(payload.encode("utf-8"))
    return base64.b64encode(compressed).decode("ascii")


@node(
    name="GZIP Decompress",
    id="gzip_decompress",
    category="Transform",
    icon="import",
    params={
        "data_b64": {
            "description": "Base64-encoded gzip data. Falls back to the wired input if blank.",
            "multiline": True,
        },
    },
)
def gzip_decompress(input: Any = None, data_b64: str = "") -> str:
    """Decode base64 → gzip-decompress → UTF-8 text."""
    payload = data_b64 or (str(input) if input is not None else "")
    raw = base64.b64decode(payload)
    return gzip.decompress(raw).decode("utf-8", "replace")


@node(
    name="Encrypt (Fernet)",
    id="encrypt_fernet",
    category="Transform",
    icon="hash",
    params={
        "key": {
            "placeholder": "base64-encoded 32-byte key",
            "description": (
                "Fernet key (base64-urlsafe, 32 bytes). Generate with "
                "Fernet.generate_key() in Python or use a credential."
            ),
        },
        "text": {
            "description": "Plaintext. Falls back to the wired input if blank.",
            "multiline": True,
        },
    },
)
def encrypt_fernet(input: Any = None, key: str = "", text: str = "") -> str:
    """Encrypt text with the supplied Fernet key. Returns a Fernet token."""
    from cryptography.fernet import Fernet

    if not key:
        raise ValueError("encrypt_fernet: key is required")
    payload = text or (str(input) if input is not None else "")
    token = Fernet(key.encode("utf-8")).encrypt(payload.encode("utf-8"))
    return token.decode("ascii")


@node(
    name="Decrypt (Fernet)",
    id="decrypt_fernet",
    category="Transform",
    icon="hash",
    params={
        "key": {
            "placeholder": "base64-encoded 32-byte key",
            "description": "Fernet key used to encrypt the token.",
        },
        "token": {
            "description": "Fernet token. Falls back to the wired input if blank.",
            "multiline": True,
        },
    },
)
def decrypt_fernet(input: Any = None, key: str = "", token: str = "") -> str:
    """Decrypt a Fernet token back to plaintext."""
    from cryptography.fernet import Fernet, InvalidToken

    if not key:
        raise ValueError("decrypt_fernet: key is required")
    payload = token or (str(input) if input is not None else "")
    try:
        plaintext = Fernet(key.encode("utf-8")).decrypt(payload.encode("utf-8"))
    except InvalidToken as exc:
        raise ValueError("decrypt_fernet: invalid token or key") from exc
    return plaintext.decode("utf-8", "replace")
