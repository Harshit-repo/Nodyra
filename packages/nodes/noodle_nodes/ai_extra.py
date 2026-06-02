"""Additional AI / ML nodes.

OpenAI Chat and Anthropic message nodes already live in ``integrations.py``.
This module adds the surrounding pieces — embeddings, transcription, TTS,
translation, vector store — as thin HTTP wrappers so the workflow env
doesn't need a heavyweight SDK.

Credential metadata replaces inline ``api_key`` fields with a single
"Credentials" picker per service. Picking a credential decrypts to the
right field(s) at run time.
"""

from __future__ import annotations

import base64
from typing import Any

import requests

from noodle.artifacts import write_bytes
from noodle.sdk import node
from noodle_nodes._creds import cred_multi, cred_single

_HTTP_TIMEOUT = 60  # AI calls can be slow; pad past the default 30 s


def _expect_ok(response: requests.Response, service: str) -> dict:
    if response.status_code >= 400:
        body = response.text[:500]
        raise RuntimeError(
            f"{service}: HTTP {response.status_code} — {body}"
        )
    try:
        return response.json()
    except ValueError:
        return {"text": response.text}


# ============================================================================
# OpenAI embeddings
# ============================================================================


@node(
    name="OpenAI Embeddings",
    id="openai_embeddings",
    category="AI",
    icon="brand:openai",
    params={
        "credentials": {
            **cred_single("openai", "api_key", "OpenAI API key"),
            "description": "OpenAI API key.",
        },
        "model": {
            "choices": [
                "text-embedding-3-small",
                "text-embedding-3-large",
                "text-embedding-ada-002",
            ],
            "description": "Embedding model.",
        },
        "text": {
            "description": "Text to embed. Falls back to the wired input.",
            "multiline": True,
        },
    },
)
def openai_embeddings(
    input: Any = None,
    credentials: str = "",
    model: str = "text-embedding-3-small",
    text: str = "",
) -> dict:
    """Compute a vector embedding for the supplied text."""
    api_key = credentials
    if not api_key:
        raise ValueError("openai_embeddings: credentials are required")
    payload_text = text or (str(input) if input is not None else "")
    if not payload_text:
        raise ValueError("openai_embeddings: text is required")
    response = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"model": model or "text-embedding-3-small", "input": payload_text},
        timeout=_HTTP_TIMEOUT,
    )
    body = _expect_ok(response, "openai")
    # Surface the bare vector for downstream nodes that want it directly.
    data = body.get("data") or []
    return {
        "embedding": data[0]["embedding"] if data else [],
        "model": body.get("model"),
        "usage": body.get("usage"),
        "raw": body,
    }


# ============================================================================
# OpenAI Whisper transcribe
# ============================================================================


@node(
    name="OpenAI Whisper Transcribe",
    id="openai_whisper_transcribe",
    category="AI",
    icon="brand:openai",
    params={
        "credentials": {
            **cred_single("openai", "api_key", "OpenAI API key"),
            "description": "OpenAI API key.",
        },
        "audio_base64": {
            "description": (
                "Audio bytes as base64. Falls back to the wired input "
                "(expected to be a base64 string or {'data': base64, "
                "'filename': name})."
            ),
            "multiline": True,
        },
        "filename": {
            "placeholder": "audio.mp3",
            "description": "Filename for the upload (affects format detection).",
        },
        "model": {
            "choices": ["whisper-1"],
            "description": "Whisper model.",
        },
        "language": {
            "placeholder": "en",
            "description": "Optional ISO-639-1 language hint (e.g. 'en').",
        },
    },
)
def openai_whisper_transcribe(
    input: Any = None,
    credentials: str = "",
    audio_base64: str = "",
    filename: str = "audio.mp3",
    model: str = "whisper-1",
    language: str = "",
) -> dict:
    """Transcribe audio with OpenAI Whisper. Audio is uploaded as multipart."""
    api_key = credentials
    if not api_key:
        raise ValueError("openai_whisper_transcribe: credentials are required")
    encoded = audio_base64
    upload_name = filename
    if not encoded and isinstance(input, dict):
        encoded = str(input.get("data", "") or input.get("audio_base64", ""))
        upload_name = str(input.get("filename") or filename)
    elif not encoded and isinstance(input, str):
        encoded = input
    if not encoded:
        raise ValueError("openai_whisper_transcribe: audio is required")
    try:
        audio_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("openai_whisper_transcribe: audio_base64 is invalid") from exc
    data: dict[str, Any] = {"model": model or "whisper-1"}
    if language:
        data["language"] = language
    response = requests.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        data=data,
        files={"file": (upload_name, audio_bytes)},
        timeout=_HTTP_TIMEOUT * 2,  # transcription is slow
    )
    return _expect_ok(response, "openai")


# ============================================================================
# OpenAI TTS
# ============================================================================


@node(
    name="OpenAI Text-to-Speech",
    id="openai_tts",
    category="AI",
    icon="brand:openai",
    output_kinds={"main": "artifact"},
    params={
        "credentials": {
            **cred_single("openai", "api_key", "OpenAI API key"),
            "description": "OpenAI API key.",
        },
        "text": {
            "description": "Text to synthesize. Falls back to the wired input.",
            "multiline": True,
        },
        "voice": {
            "choices": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
            "description": "Voice preset.",
        },
        "model": {
            "choices": ["tts-1", "tts-1-hd"],
            "description": "TTS model.",
        },
        "response_format": {
            "choices": ["mp3", "opus", "aac", "flac", "wav", "pcm"],
            "description": "Audio container format.",
        },
        "filename": {
            "placeholder": "speech.mp3",
            "description": "Artifact filename for the generated audio.",
        },
    },
)
def openai_tts(
    input: Any = None,
    credentials: str = "",
    text: str = "",
    voice: str = "alloy",
    model: str = "tts-1",
    response_format: str = "mp3",
    filename: str = "speech.mp3",
) -> dict:
    """Synthesize speech with OpenAI and store audio as an artifact."""
    api_key = credentials
    if not api_key:
        raise ValueError("openai_tts: credentials are required")
    payload_text = text or (str(input) if input is not None else "")
    if not payload_text:
        raise ValueError("openai_tts: text is required")
    response = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model or "tts-1",
            "voice": voice or "alloy",
            "input": payload_text,
            "response_format": response_format or "mp3",
        },
        timeout=_HTTP_TIMEOUT * 2,
    )
    if response.status_code >= 400:
        body = response.text[:500]
        raise RuntimeError(f"openai_tts: HTTP {response.status_code} — {body}")
    content_type = response.headers.get("content-type", "audio/mpeg")
    ext = response_format or "mp3"
    artifact_name = filename or f"speech.{ext}"
    if "." not in artifact_name:
        artifact_name = f"{artifact_name}.{ext}"
    return write_bytes(
        response.content,
        name=artifact_name,
        content_type=content_type,
        kind="audio",
        metadata={"model": model or "tts-1", "voice": voice or "alloy"},
    )


# ============================================================================
# Cohere embeddings
# ============================================================================


@node(
    name="Cohere Embed",
    id="cohere_embed",
    category="AI",
    icon="brand:cohere",
    params={
        "credentials": {
            **cred_single("cohere", "api_key", "Cohere API key"),
            "description": "Cohere API key.",
        },
        "model": {
            "choices": [
                "embed-english-v3.0",
                "embed-multilingual-v3.0",
                "embed-english-light-v3.0",
            ],
            "description": "Embedding model.",
        },
        "input_type": {
            "choices": [
                "search_document",
                "search_query",
                "classification",
                "clustering",
            ],
            "description": "How the embedding will be used.",
        },
        "text": {
            "description": "Text to embed. Falls back to the wired input.",
            "multiline": True,
        },
    },
)
def cohere_embed(
    input: Any = None,
    credentials: str = "",
    model: str = "embed-english-v3.0",
    input_type: str = "search_document",
    text: str = "",
) -> dict:
    """Compute a Cohere embedding for the supplied text."""
    api_key = credentials
    if not api_key:
        raise ValueError("cohere_embed: credentials are required")
    payload_text = text or (str(input) if input is not None else "")
    if not payload_text:
        raise ValueError("cohere_embed: text is required")
    response = requests.post(
        "https://api.cohere.ai/v1/embed",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model or "embed-english-v3.0",
            "input_type": input_type or "search_document",
            "texts": [payload_text],
        },
        timeout=_HTTP_TIMEOUT,
    )
    body = _expect_ok(response, "cohere")
    embeddings = body.get("embeddings") or []
    return {
        "embedding": embeddings[0] if embeddings else [],
        "model": model,
        "raw": body,
    }


# ============================================================================
# DeepL translate
# ============================================================================


@node(
    name="DeepL Translate",
    id="deepl_translate",
    category="AI",
    icon="brand:deepl",
    params={
        "credentials": {
            **cred_single("deepl", "api_key", "DeepL API key"),
            "description": (
                "DeepL Auth key. Free-tier keys end in ':fx' and route "
                "through the free endpoint automatically."
            ),
        },
        "text": {
            "description": "Text to translate. Falls back to the wired input.",
            "multiline": True,
        },
        "target_lang": {
            "placeholder": "EN",
            "description": "Target language code (EN, DE, FR, JA, …).",
        },
        "source_lang": {
            "placeholder": "auto",
            "description": "Optional source language. Blank = auto-detect.",
        },
    },
)
def deepl_translate(
    input: Any = None,
    credentials: str = "",
    text: str = "",
    target_lang: str = "EN",
    source_lang: str = "",
) -> dict:
    """Translate text via the DeepL API."""
    api_key = credentials
    if not api_key or not target_lang:
        raise ValueError(
            "deepl_translate: credentials and target_lang are required"
        )
    payload_text = text or (str(input) if input is not None else "")
    base = (
        "https://api-free.deepl.com"
        if api_key.endswith(":fx")
        else "https://api.deepl.com"
    )
    data = {"text": payload_text, "target_lang": target_lang.upper()}
    if source_lang:
        data["source_lang"] = source_lang.upper()
    response = requests.post(
        f"{base}/v2/translate",
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        data=data,
        timeout=_HTTP_TIMEOUT,
    )
    body = _expect_ok(response, "deepl")
    translations = body.get("translations") or []
    first = translations[0] if translations else {}
    return {
        "text": first.get("text", ""),
        "detected_source": first.get("detected_source_language", ""),
        "raw": body,
    }


# ============================================================================
# Pinecone (vector store)
# ============================================================================


@node(
    name="Pinecone Upsert",
    id="pinecone_upsert",
    category="AI",
    icon="brand:pinecone",
    params={
        "credentials": {
            **cred_multi(
                "pinecone",
                "Pinecone credentials",
                ["api_key", "index_host"],
            ),
            "description": "Pinecone API key + index host (no scheme).",
        },
        "namespace": {
            "placeholder": "default",
            "description": "Index namespace.",
        },
        "vector_id": {"description": "Vector id (string)."},
        "values_json": {
            "placeholder": "[0.1, 0.2, 0.3, ...]",
            "description": "Vector values as a JSON array of floats.",
            "multiline": True,
        },
        "metadata_json": {
            "placeholder": '{"source": "doc-1"}',
            "description": "Optional metadata as JSON.",
            "multiline": True,
        },
    },
)
def pinecone_upsert(
    input: Any = None,
    credentials: dict | None = None,
    namespace: str = "default",
    vector_id: str = "",
    values_json: str = "",
    metadata_json: str = "",
) -> dict:
    """Upsert a single vector into a Pinecone index."""
    import json as json_mod

    _ = input
    creds = credentials or {}
    api_key = str(creds.get("api_key") or "")
    index_host = str(creds.get("index_host") or "")
    if not api_key or not index_host or not vector_id:
        raise ValueError(
            "pinecone_upsert: credentials (api_key + index_host) and "
            "vector_id are required"
        )
    if values_json:
        try:
            values = json_mod.loads(values_json)
        except json_mod.JSONDecodeError as exc:
            raise ValueError(f"pinecone_upsert: values_json invalid: {exc}") from exc
    elif isinstance(input, list):
        values = input
    elif isinstance(input, dict) and isinstance(input.get("embedding"), list):
        values = input["embedding"]
    else:
        values = []
    if not isinstance(values, list) or not values:
        raise ValueError("pinecone_upsert: values_json must be a non-empty array")
    metadata = {}
    if metadata_json:
        try:
            metadata = json_mod.loads(metadata_json)
        except json_mod.JSONDecodeError as exc:
            raise ValueError(
                f"pinecone_upsert: metadata_json invalid: {exc}"
            ) from exc
    elif isinstance(input, dict) and isinstance(input.get("metadata"), dict):
        metadata = input["metadata"]
    vector = {"id": vector_id, "values": values}
    if metadata:
        vector["metadata"] = metadata
    response = requests.post(
        f"https://{index_host}/vectors/upsert",
        headers={"Api-Key": api_key, "Content-Type": "application/json"},
        json={"vectors": [vector], "namespace": namespace or "default"},
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "pinecone")


@node(
    name="Pinecone Query",
    id="pinecone_query",
    category="AI",
    icon="brand:pinecone",
    params={
        "credentials": {
            **cred_multi(
                "pinecone",
                "Pinecone credentials",
                ["api_key", "index_host"],
            ),
            "description": "Pinecone API key + index host (no scheme).",
        },
        "namespace": {
            "placeholder": "default",
            "description": "Index namespace.",
        },
        "vector_json": {
            "placeholder": "[0.1, 0.2, ...]",
            "description": (
                "Query vector as a JSON array. Falls back to the wired "
                "input when it is a list of floats."
            ),
            "multiline": True,
        },
        "top_k": {"description": "How many neighbours to return."},
        "include_metadata": {
            "description": "Include metadata in the response.",
        },
    },
)
def pinecone_query(
    input: Any = None,
    credentials: dict | None = None,
    namespace: str = "default",
    vector_json: str = "",
    top_k: int = 5,
    include_metadata: bool = True,
) -> dict:
    """Query a Pinecone index for the nearest vectors."""
    import json as json_mod

    creds = credentials or {}
    api_key = str(creds.get("api_key") or "")
    index_host = str(creds.get("index_host") or "")
    if not api_key or not index_host:
        raise ValueError(
            "pinecone_query: credentials (api_key + index_host) are required"
        )
    if vector_json:
        try:
            vector = json_mod.loads(vector_json)
        except json_mod.JSONDecodeError as exc:
            raise ValueError(
                f"pinecone_query: vector_json invalid: {exc}"
            ) from exc
    elif isinstance(input, list):
        vector = input
    elif isinstance(input, dict) and isinstance(input.get("embedding"), list):
        vector = input["embedding"]
    else:
        raise ValueError(
            "pinecone_query: provide vector_json or wire a vector/embedding "
            "into the input"
        )
    if not isinstance(vector, list) or not vector:
        raise ValueError("pinecone_query: vector must be a non-empty array")
    response = requests.post(
        f"https://{index_host}/query",
        headers={"Api-Key": api_key, "Content-Type": "application/json"},
        json={
            "vector": vector,
            "topK": max(1, int(top_k or 5)),
            "namespace": namespace or "default",
            "includeMetadata": bool(include_metadata),
        },
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "pinecone")
