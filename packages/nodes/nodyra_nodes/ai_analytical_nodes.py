"""AI Analytical Nodes — Tasks 22-29.

Covers: Named Entity Recognition, Summarizer, Sentiment Analysis,
Semantic Search, HuggingFace Inference, Batch Processor,
Image Classifier, and Code Review.

LLM-backed nodes accept a chat model supplier on the ``model`` port
(``ai_language_model`` kind). Embedding-backed nodes accept an embedding
model supplier on the ``embedding_model`` port (``ai_embedding_model`` kind).
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
from typing import Any

from jinja2 import Template

from nodyra.ai_runtime import (
    AIMessage,
    ChatModelAdapter,
    ChatRequest,
    EmbeddingModelAdapter,
    EmbeddingRequest,
)
from nodyra.artifacts import is_artifact_ref
from nodyra.artifacts import read_bytes as read_artifact_bytes
from nodyra.sdk import node
from nodyra_nodes.http_security import safe_request

AI_CATEGORY = "AI"

SEVERITY_ORDER = ["low", "medium", "high", "critical"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _model_name(adapter: ChatModelAdapter) -> str:
    try:
        return str(adapter.as_config().get("model") or "")
    except Exception:  # noqa: BLE001
        return ""


def _require_chat_model(model: Any, node_id: str) -> ChatModelAdapter:
    if not isinstance(model, ChatModelAdapter):
        raise ValueError(f"{node_id}: connect an AI Chat Model supplier to the 'model' port")
    return model


def _require_embedding_model(model: Any, node_id: str) -> EmbeddingModelAdapter:
    if not isinstance(model, EmbeddingModelAdapter):
        raise ValueError(
            f"{node_id}: connect an AI Embedding Model supplier to the 'embedding_model' port"
        )
    return model


def _resolve_text(text: str, input: Any) -> str:
    """Return text param, falling back to wired input converted to str."""
    if text and text.strip():
        return text
    if input is not None:
        return str(input)
    return ""


def _chunk_text(text: str, chunk_size: int) -> list[str]:
    """Split text into chunks of at most chunk_size characters at word boundaries."""
    if not text or chunk_size <= 0:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            # find last space before end to avoid splitting words
            space = text.rfind(" ", start, end)
            if space > start:
                end = space
        chunks.append(text[start:end].strip())
        start = end
    return [c for c in chunks if c]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _parse_json_response(text: str) -> Any:
    """Extract the first JSON object or array from an LLM text response."""
    # Try direct parse
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # Extract first JSON block from markdown code fences or bare JSON
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass
    # Try finding first { or [ and parse to its matching close
    for start_char in ("{", "["):
        idx = text.find(start_char)
        if idx != -1:
            try:
                return json.loads(text[idx:])
            except (json.JSONDecodeError, ValueError):
                pass
    return None


# ---------------------------------------------------------------------------
# Task 22 — AI Named Entity Recognition
# ---------------------------------------------------------------------------


@node(
    name="AI Named Entity Recognition",
    id="ai_named_entity_recognition",
    category=AI_CATEGORY,
    role="executable",
    icon="tag",
    inputs=["main", "model"],
    input_kinds={"model": "ai_language_model"},
    outputs=["main"],
    param_groups={"Options": ["entity_types", "spacy_model", "chunk_size"]},
    params={
        "text": {"multiline": True, "description": "Text to analyze. Also accepts wired input."},
        "backend": {
            "choices": ["llm", "spacy"],
            "description": "Backend: 'llm' (uses wired AI Chat Model) or 'spacy'.",
        },
        "entity_types": {
            "placeholder": "PERSON, ORG, GPE, DATE, MONEY, PRODUCT",
            "description": "Entity types to extract (LLM mode only).",
            "group": "Options",
        },
        "spacy_model": {
            "placeholder": "en_core_web_trf",
            "description": "spaCy model name (spacy backend only).",
            "group": "Options",
        },
        "chunk_size": {
            "description": "Max characters per LLM call.",
            "group": "Options",
        },
    },
)
def ai_named_entity_recognition(
    input: Any = None,
    model: Any = None,
    text: str = "",
    backend: str = "llm",
    entity_types: str = "PERSON, ORG, GPE, DATE, MONEY, PRODUCT",
    spacy_model: str = "en_core_web_trf",
    chunk_size: int = 2000,
) -> dict[str, Any]:
    """Extract named entities from text using an LLM or spaCy."""
    effective_text = _resolve_text(text, input)
    if not effective_text:
        raise ValueError("ai_named_entity_recognition: text input is required")

    if backend == "spacy":
        try:
            import spacy  # noqa: PLC0415
        except ImportError:
            raise ImportError(
                "ai_named_entity_recognition with spacy backend requires spacy. "
                "Install with: pip install spacy && python -m spacy download en_core_web_trf"
            )
        nlp = spacy.load(spacy_model or "en_core_web_trf")
        doc = nlp(effective_text)
        entities = [
            {"text": ent.text, "type": ent.label_, "start": ent.start_char, "end": ent.end_char}
            for ent in doc.ents
        ]
        return {"entities": entities, "text": effective_text, "backend": "spacy"}

    # LLM backend
    adapter = _require_chat_model(model, "ai_named_entity_recognition")
    types_str = entity_types or "PERSON, ORG, GPE, DATE, MONEY, PRODUCT"
    chunk_sz = max(100, int(chunk_size or 2000))
    chunks = _chunk_text(effective_text, chunk_sz)

    all_entities: list[dict[str, Any]] = []
    for chunk in chunks:
        system = (
            "You are a named entity recognition system. Extract entities from the user's text. "
            f"Entity types to extract: {types_str}. "
            "Respond with a JSON array of objects with keys: text, type, start, end. "
            "Use character offsets relative to the input text. "
            "If no entities are found, return an empty array []."
        )
        request = ChatRequest(
            messages=[AIMessage.system(system), AIMessage.user(chunk)],
            model=_model_name(adapter),
            temperature=0.1,
            response_format="json_object",
        )
        response = adapter.complete(request)
        parsed = _parse_json_response(response.text)
        if isinstance(parsed, list):
            all_entities.extend(parsed)
        elif isinstance(parsed, dict) and "entities" in parsed:
            all_entities.extend(parsed["entities"])

    return {"entities": all_entities, "text": effective_text, "backend": "llm"}


# ---------------------------------------------------------------------------
# Task 23 — AI Summarizer
# ---------------------------------------------------------------------------


@node(
    name="AI Summarizer",
    id="ai_summarizer",
    category=AI_CATEGORY,
    role="executable",
    icon="file-text",
    inputs=["main", "model"],
    input_kinds={"model": "ai_language_model"},
    outputs=["main"],
    param_groups={"Options": ["mode", "summary_length", "focus", "language"]},
    params={
        "text": {"multiline": True, "description": "Text to summarize. Also accepts wired input."},
        "mode": {
            "choices": ["abstractive", "extractive"],
            "description": "abstractive: LLM rewrite. extractive: select key sentences.",
            "group": "Options",
        },
        "summary_length": {
            "choices": ["short", "medium", "long"],
            "description": "short (~1 sentence), medium (~3-5 sentences), long (~1 paragraph).",
            "group": "Options",
        },
        "focus": {
            "placeholder": "financial results, key decisions…",
            "description": "What to emphasize in the summary.",
            "group": "Options",
        },
        "language": {
            "placeholder": "English",
            "description": "Output language. Empty = same as input.",
            "group": "Options",
        },
    },
)
def ai_summarizer(
    input: Any = None,
    model: Any = None,
    text: str = "",
    mode: str = "abstractive",
    summary_length: str = "medium",
    focus: str = "",
    language: str = "",
) -> dict[str, Any]:
    """Summarize text using an LLM (abstractive) or sentence scoring (extractive)."""
    effective_text = _resolve_text(text, input)
    if not effective_text:
        raise ValueError("ai_summarizer: text input is required")

    if mode == "extractive":
        return _extractive_summary(effective_text, summary_length)

    adapter = _require_chat_model(model, "ai_summarizer")

    length_guidance = {
        "short": "one sentence (~20-30 words)",
        "long": "one full paragraph (~150-200 words)",
    }.get(summary_length, "3-5 sentences (~80-120 words)")

    parts = [f"Summarize the following text in {length_guidance}."]
    if focus:
        parts.append(f"Focus on: {focus}.")
    if language:
        parts.append(f"Write the summary in {language}.")
    parts.append("Provide only the summary with no preamble or explanation.")

    request = ChatRequest(
        messages=[AIMessage.system("\n".join(parts)), AIMessage.user(effective_text)],
        model=_model_name(adapter),
        temperature=0.3,
    )
    response = adapter.complete(request)
    return {
        "summary": response.text.strip(),
        "mode": "abstractive",
        "length": summary_length,
        "original_length": len(effective_text),
    }


def _extractive_summary(text: str, summary_length: str) -> dict[str, Any]:
    """Simple TF-IDF-flavored sentence scoring for extractive summaries."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    n_keep = {"short": 1, "long": 7}.get(summary_length, 3)
    n_keep = min(n_keep, len(sentences))

    # Word frequency scoring
    words = re.findall(r"\b\w+\b", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        if len(w) > 3:  # skip short stop-word-like tokens
            freq[w] = freq.get(w, 0) + 1

    scores = []
    for sent in sentences:
        sent_words = re.findall(r"\b\w+\b", sent.lower())
        score = sum(freq.get(w, 0) for w in sent_words) / max(1, len(sent_words))
        scores.append(score)

    ranked = sorted(range(len(sentences)), key=lambda i: scores[i], reverse=True)[:n_keep]
    # Preserve original order
    selected = sorted(ranked)
    summary = " ".join(sentences[i] for i in selected)
    return {
        "summary": summary,
        "mode": "extractive",
        "length": summary_length,
        "original_length": len(text),
    }


# ---------------------------------------------------------------------------
# Task 24 — AI Sentiment Analysis
# ---------------------------------------------------------------------------


@node(
    name="AI Sentiment Analysis",
    id="ai_sentiment_analysis",
    category=AI_CATEGORY,
    role="executable",
    icon="thumbs-up",
    inputs=["main", "model"],
    input_kinds={"model": "ai_language_model"},
    outputs=["main"],
    param_groups={"Options": ["granularity", "aspects", "output_format"]},
    params={
        "text": {"multiline": True, "description": "Text to analyze."},
        "granularity": {
            "choices": ["document", "sentence"],
            "description": "document: overall sentiment. sentence: per-sentence breakdown.",
            "group": "Options",
        },
        "aspects": {
            "placeholder": "price, quality, service",
            "description": "Comma-separated aspects for aspect-level sentiment (optional).",
            "group": "Options",
        },
        "output_format": {
            "choices": ["label", "score", "detailed"],
            "description": (
                "label: positive/negative/neutral. "
                "score: float -1 to 1. "
                "detailed: both + explanation."
            ),
            "group": "Options",
        },
    },
)
def ai_sentiment_analysis(
    input: Any = None,
    model: Any = None,
    text: str = "",
    granularity: str = "document",
    aspects: str = "",
    output_format: str = "label",
) -> dict[str, Any]:
    """Analyze sentiment using an LLM backend."""
    effective_text = _resolve_text(text, input)
    if not effective_text:
        raise ValueError("ai_sentiment_analysis: text input is required")

    adapter = _require_chat_model(model, "ai_sentiment_analysis")

    aspect_list = [a.strip() for a in aspects.split(",") if a.strip()] if aspects else []

    system_parts = [
        "You are a sentiment analysis system. Analyze the sentiment of the user's text.",
        f"Granularity: {granularity}-level analysis.",
    ]
    if aspect_list:
        system_parts.append(f"Also extract sentiment for these aspects: {', '.join(aspect_list)}.")

    if output_format == "score":
        system_parts.append(
            "Return a JSON object with: "
            '{"sentiment": "positive"|"negative"|"neutral", "score": <float -1.0 to 1.0>'
            + (
                ', "sentences": [{"text": ..., "sentiment": ..., "score": ...}]'
                if granularity == "sentence"
                else ""
            )
            + (', "aspects": {"aspect": {"sentiment": ..., "score": ...}}' if aspect_list else "")
            + "}."
        )
    elif output_format == "detailed":
        system_parts.append(
            "Return a JSON object with: "
            '{"sentiment": ..., "score": <float>, "explanation": <string>'
            + (
                ', "sentences": [{"text": ..., "sentiment": ..., "score": ...}]'
                if granularity == "sentence"
                else ""
            )
            + (
                ', "aspects": {"aspect": {"sentiment": ..., "score": ..., "explanation": ...}}'
                if aspect_list
                else ""
            )
            + "}."
        )
    else:
        system_parts.append(
            "Return a JSON object with: "
            '{"sentiment": "positive"|"negative"|"neutral"'
            + (
                ', "sentences": [{"text": ..., "sentiment": ...}]'
                if granularity == "sentence"
                else ""
            )
            + (', "aspects": {"aspect": {"sentiment": ...}}' if aspect_list else "")
            + "}."
        )

    request = ChatRequest(
        messages=[AIMessage.system("\n".join(system_parts)), AIMessage.user(effective_text)],
        model=_model_name(adapter),
        temperature=0.1,
        response_format="json_object",
    )
    response = adapter.complete(request)
    parsed = _parse_json_response(response.text)
    if not isinstance(parsed, dict):
        parsed = {"sentiment": response.text.strip().lower(), "raw": response.text}
    return {**parsed, "text": effective_text, "output_format": output_format}


# ---------------------------------------------------------------------------
# Task 25 — AI Semantic Search
# ---------------------------------------------------------------------------


@node(
    name="AI Semantic Search",
    id="ai_semantic_search",
    category=AI_CATEGORY,
    role="executable",
    icon="search",
    inputs=["main", "embedding_model"],
    input_kinds={"embedding_model": "ai_embedding_model"},
    outputs=["main"],
    params={
        "query": {"placeholder": "search query…", "description": "Text query to search for."},
        "documents": {
            "description": "List of documents (strings or {id, text} dicts). Also accepts wired input.",
        },
        "top_k": {"description": "Maximum number of results to return."},
        "min_score": {
            "description": "Minimum cosine similarity threshold (0.0–1.0). 0 = no threshold.",
        },
    },
)
def ai_semantic_search(
    input: Any = None,
    embedding_model: Any = None,
    query: str = "",
    documents: Any = None,
    top_k: int = 5,
    min_score: float = 0.0,
) -> dict[str, Any]:
    """Semantic search over documents using embedding cosine similarity."""
    adapter = _require_embedding_model(embedding_model, "ai_semantic_search")

    effective_query = query or (str(input) if input is not None else "")
    if not effective_query:
        raise ValueError("ai_semantic_search: query is required")

    raw_docs = documents if documents is not None else (input if input is not None else [])
    if not isinstance(raw_docs, list):
        raw_docs = [raw_docs]
    if not raw_docs:
        raise ValueError("ai_semantic_search: documents input is required")

    # Normalize documents to (id, text) pairs
    doc_pairs: list[tuple[str, str]] = []
    for i, doc in enumerate(raw_docs):
        if isinstance(doc, dict):
            doc_pairs.append(
                (str(doc.get("id") or i), str(doc.get("text") or doc.get("content") or ""))
            )
        else:
            doc_pairs.append((str(i), str(doc)))

    doc_texts = [text for _, text in doc_pairs]

    # Embed query and all documents in one batch
    all_texts = [effective_query] + doc_texts
    model_name = _model_name(adapter) if hasattr(adapter, "as_config") else ""
    embed_resp = adapter.embed(EmbeddingRequest(texts=all_texts, model=model_name or ""))

    if len(embed_resp.embeddings) < 1 + len(doc_texts):
        raise RuntimeError("ai_semantic_search: embedding response length mismatch")

    query_vec = embed_resp.embeddings[0]
    doc_vecs = embed_resp.embeddings[1:]

    results = []
    for (doc_id, doc_text), doc_vec in zip(doc_pairs, doc_vecs, strict=False):
        score = _cosine_similarity(query_vec, doc_vec)
        if score >= float(min_score or 0.0):
            results.append({"id": doc_id, "text": doc_text, "score": round(score, 6)})

    results.sort(key=lambda r: r["score"], reverse=True)
    k = max(1, int(top_k or 5))
    return {"results": results[:k], "query": effective_query, "total_documents": len(doc_pairs)}


# ---------------------------------------------------------------------------
# Task 26 — HuggingFace Inference
# ---------------------------------------------------------------------------


@node(
    name="HuggingFace Inference",
    id="huggingface_inference",
    category=AI_CATEGORY,
    role="executable",
    icon="brand:huggingface",
    requirements=["huggingface-hub>=0.20"],
    inputs=["main"],
    outputs=["main"],
    param_groups={"Options": ["parameters", "endpoint_url"]},
    params={
        "credentials": {
            "type": "credential",
            "credential_type": "huggingface",
            "description": "HuggingFace API token credential.",
        },
        "model": {
            "placeholder": "google/flan-t5-xl",
            "description": "Model ID (e.g. 'google/flan-t5-xl', 'bigcode/starcoder').",
        },
        "task": {
            "choices": [
                "text-generation",
                "text-classification",
                "summarization",
                "translation",
                "question-answering",
                "image-classification",
                "automatic-speech-recognition",
                "fill-mask",
                "zero-shot-classification",
                "token-classification",
            ],
            "description": "HuggingFace pipeline task type.",
        },
        "inputs": {
            "multiline": True,
            "description": "Model inputs (text, JSON, etc.). Also accepts wired input.",
        },
        "parameters": {
            "type": "key_value",
            "description": "Additional model parameters (temperature, max_length, etc.).",
            "group": "Options",
        },
        "endpoint_url": {
            "placeholder": "https://…huggingface.cloud",
            "description": "Dedicated Inference Endpoint URL (overrides Serverless API).",
            "group": "Options",
        },
    },
)
def huggingface_inference(
    input: Any = None,
    credentials: Any = None,
    model: str = "",
    task: str = "text-generation",
    inputs: Any = None,
    parameters: dict | None = None,
    endpoint_url: str = "",
) -> dict[str, Any]:
    """Run any HuggingFace model via the Inference API or a dedicated endpoint."""
    try:
        from huggingface_hub import InferenceClient  # noqa: PLC0415
    except ImportError:
        raise ImportError(
            "huggingface_inference requires huggingface-hub. "
            "Install with: pip install 'huggingface-hub>=0.20'"
        )

    api_token = ""
    if isinstance(credentials, dict):
        api_token = str(
            credentials.get("api_token")
            or credentials.get("token")
            or credentials.get("api_key")
            or ""
        )
    elif isinstance(credentials, str):
        api_token = credentials

    effective_inputs = inputs if inputs is not None else (str(input) if input is not None else "")

    client = InferenceClient(token=api_token or None)

    extra_params = parameters or {}

    if endpoint_url:
        url = endpoint_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        payload: dict[str, Any] = {"inputs": effective_inputs}
        if extra_params:
            payload["parameters"] = extra_params
        resp = safe_request(
            "POST",
            url,
            json=payload,
            headers=headers,
            timeout=120,
            context="huggingface_inference endpoint",
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"huggingface_inference endpoint error {resp.status_code}: {resp.text[:500]}"
            )
        try:
            result = resp.json()
        except ValueError:
            result = resp.text
        return {"output": result, "model": model, "task": task, "endpoint": url}

    # Serverless Inference API via huggingface_hub
    task_method_map: dict[str, str] = {
        "text-generation": "text_generation",
        "text-classification": "text_classification",
        "summarization": "summarization",
        "translation": "translation",
        "question-answering": "question_answering",
        "fill-mask": "fill_mask",
        "zero-shot-classification": "zero_shot_classification",
        "token-classification": "token_classification",
        "automatic-speech-recognition": "automatic_speech_recognition",
    }
    method_name = task_method_map.get(task, "post")

    if hasattr(client, method_name) and method_name != "post":
        method = getattr(client, method_name)
        result = method(effective_inputs, model=model, **extra_params)
    else:
        # Fallback to raw POST
        result = client.post(
            json={
                "inputs": effective_inputs,
                **({"parameters": extra_params} if extra_params else {}),
            },
            model=model,
        )

    # Normalize result to a JSON-serializable form
    if hasattr(result, "__dict__"):
        output = {k: v for k, v in result.__dict__.items() if not k.startswith("_")}
    elif isinstance(result, (list, dict, str, int, float)):
        output = result
    else:
        output = str(result)

    return {"output": output, "model": model, "task": task}


# ---------------------------------------------------------------------------
# Task 27 — AI Batch Processor
# ---------------------------------------------------------------------------


@node(
    name="AI Batch Processor",
    id="ai_batch_processor",
    category=AI_CATEGORY,
    role="executable",
    icon="layers",
    inputs=["main", "model"],
    input_kinds={"model": "ai_language_model"},
    outputs=["main"],
    param_groups={"Options": ["batch_size", "concurrency", "output_field", "continue_on_error"]},
    params={
        "prompt_template": {
            "multiline": True,
            "placeholder": "Classify the following item as positive or negative: {{ item }}",
            "description": "Jinja2 template. Use {{ item }} for each element.",
        },
        "batch_size": {
            "description": "Items per LLM call (items are batched in the prompt).",
            "group": "Options",
        },
        "concurrency": {
            "description": "Parallel API calls.",
            "group": "Options",
        },
        "output_field": {
            "description": "Field name for the LLM output attached to each item.",
            "group": "Options",
        },
        "continue_on_error": {
            "description": "If True, skip failed items instead of raising.",
            "group": "Options",
        },
    },
)
def ai_batch_processor(
    input: Any = None,
    model: Any = None,
    prompt_template: str = "",
    batch_size: int = 10,
    concurrency: int = 3,
    output_field: str = "result",
    continue_on_error: bool = True,
) -> dict[str, Any]:
    """Process a list of items through an LLM using a Jinja2 prompt template."""
    adapter = _require_chat_model(model, "ai_batch_processor")
    if not prompt_template:
        raise ValueError("ai_batch_processor: prompt_template is required")

    items = input if isinstance(input, list) else ([input] if input is not None else [])
    if not items:
        return {"results": [], "total": 0, "errors": 0}

    template = Template(prompt_template)
    batch_sz = max(1, int(batch_size or 10))
    max_conc = max(1, int(concurrency or 3))
    field = output_field or "result"

    # Split items into batches
    batches: list[list[Any]] = []
    for i in range(0, len(items), batch_sz):
        batches.append(items[i : i + batch_sz])

    def _process_batch(batch: list[Any]) -> list[dict[str, Any]]:
        batch_results: list[dict[str, Any]] = []
        for item in batch:
            try:
                prompt_text = template.render(item=item)
                request = ChatRequest(
                    messages=[AIMessage.user(prompt_text)],
                    model=_model_name(adapter),
                    temperature=0.1,
                )
                resp = adapter.complete(request)
                item_out = dict(item) if isinstance(item, dict) else {"value": item}
                item_out[field] = resp.text.strip()
                batch_results.append(item_out)
            except Exception as exc:  # noqa: BLE001
                if not continue_on_error:
                    raise
                item_out = dict(item) if isinstance(item, dict) else {"value": item}
                item_out[field] = None
                item_out["_error"] = str(exc)
                batch_results.append(item_out)
        return batch_results

    async def _run_concurrent() -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(max_conc)

        async def _process_with_sem(batch: list[Any]) -> list[dict[str, Any]]:
            async with sem:
                return await asyncio.to_thread(_process_batch, batch)

        tasks = [asyncio.create_task(_process_with_sem(b)) for b in batches]
        batch_outputs = await asyncio.gather(*tasks)
        combined: list[dict[str, Any]] = []
        for bo in batch_outputs:
            combined.extend(bo)
        return combined

    all_results = asyncio.run(_run_concurrent())
    err_count = sum(1 for r in all_results if r.get("_error") is not None)
    return {"results": all_results, "total": len(all_results), "errors": err_count}


# ---------------------------------------------------------------------------
# Task 28 — AI Image Classifier
# ---------------------------------------------------------------------------


def _image_to_data_url(image: Any) -> str:
    """Convert an image (artifact ref, base64 string, or bytes) to a data URL."""
    if isinstance(image, bytes):
        encoded = base64.b64encode(image).decode()
        return f"data:image/jpeg;base64,{encoded}"
    if isinstance(image, str):
        if image.startswith("data:"):
            return image
        # Already base64
        return f"data:image/jpeg;base64,{image}"
    if is_artifact_ref(image):
        raw = read_artifact_bytes(image)
        encoded = base64.b64encode(raw).decode()
        content_type = str(image.get("content_type") or "image/jpeg")
        return f"data:{content_type};base64,{encoded}"
    raise ValueError(f"ai_image_classifier: unsupported image type {type(image)}")


@node(
    name="AI Image Classifier",
    id="ai_image_classifier",
    category=AI_CATEGORY,
    role="executable",
    icon="image",
    inputs=["main", "model"],
    input_kinds={"model": "ai_language_model"},
    outputs=["main"],
    params={
        "labels": {
            "placeholder": "cat, dog, bird",
            "description": "Comma-separated classification labels.",
        },
        "multi_label": {
            "description": "Allow multiple labels per image.",
        },
        "prompt": {
            "multiline": True,
            "description": "Additional context or instructions for classification.",
        },
    },
)
def ai_image_classifier(
    input: Any = None,
    model: Any = None,
    labels: str = "",
    multi_label: bool = False,
    prompt: str = "",
) -> dict[str, Any]:
    """Classify images using a vision-capable AI Chat Model."""
    adapter = _require_chat_model(model, "ai_image_classifier")

    if not labels:
        raise ValueError("ai_image_classifier: labels are required")
    label_list = [label.strip() for label in labels.split(",") if label.strip()]

    raw_images = input if isinstance(input, list) else ([input] if input is not None else [])
    if not raw_images:
        raise ValueError("ai_image_classifier: image input is required")

    system_prompt = (
        f"You are an image classification system. "
        f"Classify {'each' if len(raw_images) > 1 else 'the'} image into "
        f"{'one or more of' if multi_label else 'exactly one of'} "
        f"these labels: {', '.join(label_list)}. "
    )
    if prompt:
        system_prompt += f"\nAdditional context: {prompt}"
    system_prompt += (
        "\nRespond with a JSON object: "
        '{"classifications": [{"label": "...", "confidence": 0.95, "explanation": "..."}]}'
    )

    classifications: list[dict[str, Any]] = []
    for image in raw_images:
        try:
            data_url = _image_to_data_url(image)
        except Exception as exc:
            classifications.append({"error": str(exc)})
            continue

        # Send as a message with image content
        image_message = AIMessage.user(
            json.dumps({"type": "image_url", "image_url": {"url": data_url}})
        )
        request = ChatRequest(
            messages=[AIMessage.system(system_prompt), image_message],
            model=_model_name(adapter),
            temperature=0.1,
            response_format="json_object",
        )
        try:
            response = adapter.complete(request)
            parsed = _parse_json_response(response.text)
            if isinstance(parsed, dict) and "classifications" in parsed:
                classifications.extend(parsed["classifications"])
            elif isinstance(parsed, list):
                classifications.extend(parsed)
            else:
                classifications.append({"label": response.text.strip(), "confidence": 1.0})
        except Exception as exc:  # noqa: BLE001
            classifications.append({"error": str(exc)})

    return {
        "classifications": classifications,
        "labels": label_list,
        "multi_label": multi_label,
        "image_count": len(raw_images),
    }


# ---------------------------------------------------------------------------
# Task 29 — AI Code Review
# ---------------------------------------------------------------------------


@node(
    name="AI Code Review",
    id="ai_code_review",
    category=AI_CATEGORY,
    role="executable",
    icon="code",
    inputs=["main", "model"],
    input_kinds={"model": "ai_language_model"},
    outputs=["main"],
    param_groups={"Options": ["language", "review_aspects", "severity_threshold"]},
    params={
        "code": {
            "multiline": True,
            "description": "Source code to review. Also accepts wired input.",
        },
        "language": {
            "placeholder": "Python",
            "description": "Programming language. Auto-detected if empty.",
            "group": "Options",
        },
        "review_aspects": {
            "placeholder": "bugs, security, performance, style",
            "description": "Comma-separated aspects to check for.",
            "group": "Options",
        },
        "severity_threshold": {
            "choices": SEVERITY_ORDER,
            "description": "Minimum issue severity to include in the report.",
            "group": "Options",
        },
    },
)
def ai_code_review(
    input: Any = None,
    model: Any = None,
    code: str = "",
    language: str = "",
    review_aspects: str = "bugs, security, performance, style",
    severity_threshold: str = "medium",
) -> dict[str, Any]:
    """Review code for bugs, security issues, performance, and style using an LLM."""
    adapter = _require_chat_model(model, "ai_code_review")
    effective_code = _resolve_text(code, input)
    if not effective_code:
        raise ValueError("ai_code_review: code input is required")

    aspects = [a.strip() for a in review_aspects.split(",") if a.strip()] or [
        "bugs",
        "security",
        "performance",
        "style",
    ]
    lang_hint = f" The code is written in {language}." if language else ""
    threshold_idx = (
        SEVERITY_ORDER.index(severity_threshold) if severity_threshold in SEVERITY_ORDER else 1
    )

    system = (
        f"You are an expert code reviewer.{lang_hint} "
        f"Review the code for: {', '.join(aspects)}. "
        "For each issue, report: severity (low/medium/high/critical), "
        "line number or range if possible, category, description, and suggested fix. "
        "Respond with a JSON object: "
        '{"issues": [{"severity": "...", "line": "...", "category": "...", "description": "...", "suggestion": "..."}], '
        '"summary": "...", "language": "...", "overall_quality": "good"|"fair"|"poor"}'
    )

    request = ChatRequest(
        messages=[AIMessage.system(system), AIMessage.user(effective_code)],
        model=_model_name(adapter),
        temperature=0.1,
        response_format="json_object",
    )
    response = adapter.complete(request)
    parsed = _parse_json_response(response.text)
    if not isinstance(parsed, dict):
        return {
            "issues": [],
            "summary": response.text.strip(),
            "language": language,
            "raw": response.text,
        }

    # Filter by severity threshold
    all_issues: list[dict[str, Any]] = parsed.get("issues") or []
    filtered_issues = [
        issue
        for issue in all_issues
        if SEVERITY_ORDER.index(str(issue.get("severity") or "low").lower()) >= threshold_idx
        if str(issue.get("severity") or "low").lower() in SEVERITY_ORDER
    ]

    return {
        "issues": filtered_issues,
        "all_issue_count": len(all_issues),
        "filtered_issue_count": len(filtered_issues),
        "summary": parsed.get("summary") or "",
        "language": parsed.get("language") or language,
        "overall_quality": parsed.get("overall_quality") or "unknown",
        "severity_threshold": severity_threshold,
    }
