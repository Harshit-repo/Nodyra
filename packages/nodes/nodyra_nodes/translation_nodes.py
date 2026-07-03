"""Translation nodes (DeepL - newer version)."""

from __future__ import annotations

from typing import Any

from nodyra.sdk import node
from nodyra_nodes._creds import cred_single


def _deepl():
    try:
        import deepl  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "DeepL requires the `deepl` package. "
            "Install with: uv pip install deepl"
        ) from exc
    return deepl


@node(
    name="DeepL Translate",
    id="deepl_translate_v2",
    category="Transform",
    icon="brand:deepl",
    params={
        "text": {
            "placeholder": "Hello world",
            "description": "Text to translate. Blank uses wired input.",
        },
        "target_lang": {
            "placeholder": "DE",
            "description": "Target language code (e.g. DE, FR, ES, JA, ZH).",
        },
        "source_lang": {
            "group": "Options",
            "placeholder": "EN",
            "description": "Source language code (auto-detected if blank).",
        },
        "formality": {
            "group": "Options",
            "choices": ["default", "more", "less"],
            "description": "Control formality (supported languages only).",
        },
        "api_key": {
            **cred_single("api_key", "deepl", "DeepL API Key"),
        },
    },
)
def deepl_translate_v2(
    input: Any = None,
    text: str = "",
    target_lang: str = "EN",
    source_lang: str = "",
    formality: str = "default",
    api_key: str = "",
) -> dict[str, Any]:
    """Translate text using DeepL."""
    if not api_key:
        raise ValueError("deepl_translate_v2: api_key is required")
    payload = text if text else (str(input) if input is not None else "")
    if not payload:
        raise ValueError("deepl_translate_v2: text or input is required")
    if not target_lang:
        target_lang = "EN"

    deepl = _deepl()
    client = deepl.Translator(api_key)
    params: dict[str, Any] = {"target_lang": target_lang.upper()}
    if source_lang:
        params["source_lang"] = source_lang.upper()
    if formality in ("more", "less"):
        params["formality"] = formality

    result = client.translate_text(payload, **params)
    return {
        "translated_text": result.text,
        "detected_source_lang": result.detected_source_lang,
        "text": payload,
        "target_lang": target_lang.upper(),
    }


__all__ = ["deepl_translate_v2"]
