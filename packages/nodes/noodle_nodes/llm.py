"""Production-oriented AI workflow nodes.

This module keeps model calls provider-normalized and keeps data-heavy AI
operations DatasetRef-aware. Provider SDKs are intentionally avoided: built-in
nodes should enumerate and run in lean workflow environments.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlencode

import requests

from noodle.artifacts import is_artifact_ref, read_text, write_bytes
from noodle.context import emit_chunk, node_emitter, workflow_caller
from noodle.datasets import is_dataset_ref
from noodle.sdk import node
from noodle_nodes._creds import cred_multi, cred_single
from noodle_nodes.datasets import materialize_dataset, records_to_dataset
from noodle_nodes.http_security import assert_public_http_url

AI_CATEGORY = "AI"
DEFAULT_TIMEOUT = 75
MAX_AGENT_STEPS = 10
MAX_DATASET_AI_ROWS = 1000
MAX_EMBEDDING_BATCH = 96
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CHAT_PROVIDER_CHOICES = [
    "openai",
    "anthropic",
    "openrouter",
    "openai_compatible",
    "ollama",
    "azure_openai",
]
CHAT_MODEL_CHOICES = [
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4o-mini",
    "gpt-4o",
    "o4-mini",
    "o3-mini",
    "claude-3-5-haiku-latest",
    "claude-3-5-sonnet-latest",
    "claude-3-7-sonnet-latest",
    "openai/gpt-4.1-mini",
    "openai/gpt-4o-mini",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3.5-haiku",
    "google/gemini-2.0-flash-001",
    "meta-llama/llama-3.3-70b-instruct",
    "mistralai/mistral-large",
    "llama3.2",
    "llama3.1",
    "qwen2.5",
    "mistral",
    "gemma2",
]
EMBEDDING_PROVIDER_CHOICES = ["openai", "openai_compatible", "ollama", "cohere"]
EMBEDDING_MODEL_CHOICES = [
    "text-embedding-3-small",
    "text-embedding-3-large",
    "nomic-embed-text",
    "mxbai-embed-large",
    "embed-english-v3.0",
    "embed-multilingual-v3.0",
]
VISION_MODEL_CHOICES = [
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4o-mini",
    "gpt-4o",
    "openai/gpt-4.1-mini",
    "openai/gpt-4o-mini",
    "google/gemini-2.0-flash-001",
]
IMAGE_MODEL_CHOICES = ["gpt-image-1", "dall-e-3"]
MODERATION_MODEL_CHOICES = ["omni-moderation-latest"]
LLM_CREDENTIAL_FIELDS = [
    "provider",
    "api_key",
    "base_url",
    "organization",
    "azure_endpoint",
    "azure_api_version",
    "deployment",
    "site_url",
    "app_name",
]
EMBEDDING_CREDENTIAL_FIELDS = ["provider", "api_key", "base_url"]


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, indent=2)


def _text_from_input(input: Any, text: str = "") -> str:
    if text:
        return text
    if input is None:
        return ""
    if is_artifact_ref(input):
        return read_text(input)
    if isinstance(input, dict | list):
        return _pretty_json(input)
    return str(input)


def _json_loads(value: Any, *, label: str, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, dict | list):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON: {exc}") from exc


def _expect_json(response: requests.Response, service: str) -> dict[str, Any]:
    if response.status_code >= 400:
        body = response.text[:800]
        raise RuntimeError(f"{service}: HTTP {response.status_code}: {body}")
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{service}: expected JSON response") from exc
    if not isinstance(body, dict):
        raise RuntimeError(f"{service}: expected JSON object response")
    return body


def _with_chat_completions(base_url: str) -> str:
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _hosted_https_url(host: str, path: str, *, context: str) -> str:
    host = str(host or "").strip()
    if not host:
        raise ValueError(f"{context}: host is required")
    if "://" in host or any(ch in host for ch in "/?#@"):
        raise ValueError(f"{context}: host must be a hostname, not a URL")
    url = f"https://{host}{path}"
    assert_public_http_url(url, context=context)
    return url


def _provider_creds(credentials: dict | None) -> dict[str, str]:
    creds = credentials if isinstance(credentials, dict) else {}
    return {str(k): str(v) for k, v in creds.items() if v not in (None, "")}


def _effective_provider(provider: str, credentials: dict[str, str]) -> str:
    value = (provider or credentials.get("provider") or "openai").strip().lower()
    aliases = {
        "openai compatible": "openai_compatible",
        "openai-compatible": "openai_compatible",
        "open router": "openrouter",
        "open-router": "openrouter",
        "azure": "azure_openai",
        "azure-openai": "azure_openai",
    }
    return aliases.get(value, value)


def _model_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if value.get("type") not in (None, "chat_model"):
        return {}
    keys = {
        "credentials",
        "provider",
        "model",
        "temperature",
        "max_tokens",
        "response_format",
        "timeout_seconds",
        "include_raw",
    }
    if not any(key in value for key in keys):
        return {}
    return {key: value[key] for key in keys if key in value and value[key] not in (None, "")}


def _resolve_chat_config(
    model_config: Any,
    *,
    credentials: dict | None,
    provider: str,
    model: str,
    temperature: float,
    max_tokens: int | None = None,
    response_format: str = "text",
    timeout_seconds: int = DEFAULT_TIMEOUT,
    include_raw: bool = False,
) -> dict[str, Any]:
    config = _model_config(model_config)
    return {
        "credentials": config.get("credentials", credentials),
        "provider": str(config.get("provider") or provider or "openai"),
        "model": str(config.get("model") or model or "gpt-4.1-mini"),
        "temperature": float(config.get("temperature", temperature)),
        "max_tokens": config.get("max_tokens", max_tokens),
        "response_format": str(config.get("response_format") or response_format or "text"),
        "timeout_seconds": int(config.get("timeout_seconds", timeout_seconds or DEFAULT_TIMEOUT)),
        "include_raw": bool(config.get("include_raw", include_raw)),
    }


def _messages_from_inputs(
    input: Any,
    *,
    system: str = "",
    prompt: str = "",
    messages_json: str = "",
) -> list[dict[str, str]]:
    raw_messages = _json_loads(messages_json, label="messages_json", default=None)
    if raw_messages is None and isinstance(input, dict) and isinstance(input.get("messages"), list):
        raw_messages = input["messages"]
    if raw_messages is not None:
        if not isinstance(raw_messages, list):
            raise ValueError("messages_json must be a JSON array")
        messages: list[dict[str, str]] = []
        for idx, item in enumerate(raw_messages):
            if not isinstance(item, dict):
                raise ValueError(f"messages_json[{idx}] must be an object")
            role = str(item.get("role") or "user")
            content = item.get("content")
            if isinstance(content, dict | list):
                content_text = _pretty_json(content)
            else:
                content_text = str(content or "")
            if role not in {"system", "user", "assistant", "tool"}:
                raise ValueError(f"messages_json[{idx}].role is not supported: {role}")
            messages.append({"role": role, "content": content_text})
        if system and not any(msg["role"] == "system" for msg in messages):
            messages.insert(0, {"role": "system", "content": system})
        return messages

    if isinstance(input, dict):
        system = system or str(input.get("system") or "")
        prompt = prompt or str(input.get("prompt") or input.get("text") or "")
    user_text = _text_from_input(input, prompt)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_text})
    return messages


def _memory_messages(value: Any, *, max_messages: int | None = None) -> list[dict[str, str]]:
    if not value:
        return []
    raw = None
    if isinstance(value, dict):
        raw = value.get("messages")
    elif isinstance(value, list):
        raw = value
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("ai_agent: memory.messages must be a list")
    messages: list[dict[str, str]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"ai_agent: memory.messages[{idx}] must be an object")
        role = str(item.get("role") or "user")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"ai_agent: unsupported memory role {role!r}")
        content = item.get("content")
        messages.append(
            {
                "role": role,
                "content": _pretty_json(content)
                if isinstance(content, dict | list)
                else str(content or ""),
            }
        )
    limit = int(max_messages or 0)
    if limit > 0:
        return messages[-limit:]
    return messages


def _with_memory(
    messages: list[dict[str, str]],
    memory: Any,
    *,
    max_messages: int | None = None,
) -> list[dict[str, str]]:
    memory_items = _memory_messages(memory, max_messages=max_messages)
    if not memory_items:
        return messages
    system_items = [msg for msg in messages if msg.get("role") == "system"]
    non_system_items = [msg for msg in messages if msg.get("role") != "system"]
    return [*system_items, *memory_items, *non_system_items]


def _split_system(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    system_parts = [msg["content"] for msg in messages if msg.get("role") == "system"]
    non_system = [
        {"role": msg.get("role") or "user", "content": str(msg.get("content") or "")}
        for msg in messages
        if msg.get("role") != "system"
    ]
    if not non_system:
        non_system = [{"role": "user", "content": ""}]
    return "\n\n".join(part for part in system_parts if part), non_system


def _normalize_openai(body: dict[str, Any]) -> dict[str, Any]:
    choices = body.get("choices") if isinstance(body.get("choices"), list) else []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    text = str(message.get("content") or "")
    return {
        "text": text,
        "message": message,
        "tool_calls": message.get("tool_calls") or [],
        "usage": body.get("usage") or {},
        "model": body.get("model"),
        "finish_reason": first.get("finish_reason"),
    }


def _normalize_anthropic(body: dict[str, Any]) -> dict[str, Any]:
    content = body.get("content") if isinstance(body.get("content"), list) else []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for chunk in content:
        if not isinstance(chunk, dict):
            continue
        if chunk.get("type") == "text":
            text_parts.append(str(chunk.get("text") or ""))
        elif chunk.get("type") == "tool_use":
            tool_calls.append(chunk)
    return {
        "text": "".join(text_parts),
        "message": {"role": body.get("role", "assistant"), "content": content},
        "tool_calls": tool_calls,
        "usage": body.get("usage") or {},
        "model": body.get("model"),
        "finish_reason": body.get("stop_reason"),
    }


def _sse_data(line: Any) -> str | None:
    """Return the ``data:`` payload of one SSE line, or None for non-data lines."""
    text = line.decode("utf-8") if isinstance(line, (bytes, bytearray)) else str(line)
    text = text.strip()
    if not text or not text.startswith("data:"):
        return None
    return text[len("data:") :].strip()


def _assemble_openai_stream(lines: Iterable[Any], emit: Callable[[str], None]) -> dict[str, Any]:
    """Consume an OpenAI-format streaming chat completion.

    Emits each text delta via ``emit`` as it arrives and returns the same
    normalized envelope shape as :func:`_normalize_openai`, with streamed tool
    calls reassembled from their per-index argument fragments.
    """
    parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    usage: dict[str, Any] = {}
    model: str | None = None
    for line in lines:
        data = _sse_data(line)
        if data is None:
            continue
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        model = obj.get("model") or model
        if isinstance(obj.get("usage"), dict):
            usage = obj["usage"]
        for choice in obj.get("choices") or []:
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                parts.append(piece)
                emit(piece)
            for raw_tc in delta.get("tool_calls") or []:
                idx = int(raw_tc.get("index") or 0)
                slot = tool_calls.setdefault(
                    idx,
                    {"id": None, "type": "function",
                     "function": {"name": "", "arguments": ""}},
                )
                if raw_tc.get("id"):
                    slot["id"] = raw_tc["id"]
                fn = raw_tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
    text = "".join(parts)
    ordered_tools = [tool_calls[i] for i in sorted(tool_calls)]
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if ordered_tools:
        message["tool_calls"] = ordered_tools
    return {
        "text": text,
        "message": message,
        "tool_calls": ordered_tools,
        "usage": usage,
        "model": model,
        "finish_reason": finish_reason,
    }


def _assemble_anthropic_stream(lines: Iterable[Any], emit: Callable[[str], None]) -> dict[str, Any]:
    """Consume an Anthropic-format streaming message, emitting text deltas, and
    return the same normalized envelope shape as :func:`_normalize_anthropic`."""
    parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, Any] = {}
    model: str | None = None
    for line in lines:
        data = _sse_data(line)
        if data is None or data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        event_type = obj.get("type")
        if event_type == "message_start":
            message = obj.get("message") or {}
            model = message.get("model") or model
            if isinstance(message.get("usage"), dict):
                usage = {**usage, **message["usage"]}
        elif event_type == "content_block_delta":
            delta = obj.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                parts.append(delta["text"])
                emit(delta["text"])
        elif event_type == "message_delta":
            delta = obj.get("delta") or {}
            if delta.get("stop_reason"):
                finish_reason = delta["stop_reason"]
            if isinstance(obj.get("usage"), dict):
                usage = {**usage, **obj["usage"]}
    text = "".join(parts)
    return {
        "text": text,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        "tool_calls": [],
        "usage": usage,
        "model": model,
        "finish_reason": finish_reason,
    }


def _post_stream(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
    service: str,
    assemble: Callable[[Iterable[Any], Callable[[str], None]], dict[str, Any]],
) -> dict[str, Any]:
    """POST a streaming chat request and assemble the normalized envelope.

    Mirrors :func:`_expect_json`'s error surfacing for non-2xx responses before
    consuming the event stream. Text deltas are forwarded live via
    ``noodle.emit_chunk`` so the editor canvas shows tokens as they're produced.
    """
    response = requests.post(
        url,
        headers=headers,
        json={**payload, "stream": True},
        timeout=timeout,
        stream=True,
    )
    try:
        status = int(getattr(response, "status_code", 200) or 200)
        if status >= 400:
            try:
                detail: Any = response.json()
            except Exception:  # noqa: BLE001 - fall back to raw body
                detail = getattr(response, "text", "")
            raise ValueError(f"{service} request failed ({status}): {detail}")
        return assemble(response.iter_lines(decode_unicode=True), emit_chunk)
    finally:
        # Release the connection even when assembly stops early at ``[DONE]``.
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _streaming_enabled(response_format: str) -> bool:
    """Stream only when a live consumer is listening and the response is plain
    text — JSON / structured responses are parsed whole, not token-by-token."""
    return node_emitter.get() is not None and response_format == "text"


def _call_llm(
    *,
    provider: str,
    credentials: dict | None = None,
    model: str = "",
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int | None = None,
    response_format: str = "text",
    timeout_seconds: int = DEFAULT_TIMEOUT,
    include_raw: bool = False,
) -> dict[str, Any]:
    creds = _provider_creds(credentials)
    provider_key = _effective_provider(provider, creds)
    timeout = max(1, min(300, int(timeout_seconds or DEFAULT_TIMEOUT)))
    streaming = _streaming_enabled(response_format)
    body: Any = None  # raw provider body; stays None on the streaming path

    if provider_key == "anthropic":
        api_key = creds.get("api_key", "")
        if not api_key:
            raise ValueError("ai_chat: credentials.api_key is required for Anthropic")
        system, anthropic_messages = _split_system(messages)
        payload: dict[str, Any] = {
            "model": model or creds.get("model") or "claude-3-5-haiku-latest",
            "max_tokens": int(max_tokens or 1024),
            "messages": anthropic_messages,
            "temperature": float(temperature),
        }
        if system:
            payload["system"] = system
        anthropic_url = creds.get("base_url") or "https://api.anthropic.com/v1/messages"
        anthropic_headers = {
            "x-api-key": api_key,
            "anthropic-version": creds.get("anthropic_version", "2023-06-01"),
            "Content-Type": "application/json",
        }
        if streaming:
            out = _post_stream(
                anthropic_url,
                headers=anthropic_headers,
                payload=payload,
                timeout=timeout,
                service="anthropic",
                assemble=_assemble_anthropic_stream,
            )
        else:
            body = _expect_json(
                requests.post(
                    anthropic_url,
                    headers=anthropic_headers,
                    json=payload,
                    timeout=timeout,
                ),
                "anthropic",
            )
            out = _normalize_anthropic(body)
    elif provider_key == "azure_openai":
        api_key = creds.get("api_key", "")
        endpoint = (creds.get("azure_endpoint") or creds.get("base_url") or "").rstrip("/")
        deployment = creds.get("deployment") or model
        api_version = creds.get("azure_api_version") or "2024-02-15-preview"
        if not api_key or not endpoint or not deployment:
            raise ValueError(
                "ai_chat: Azure OpenAI requires api_key, azure_endpoint/base_url, "
                "and deployment/model"
            )
        url = (
            f"{endpoint}/openai/deployments/{deployment}/chat/completions?"
            f"{urlencode({'api-version': api_version})}"
        )
        payload = _openai_payload(
            model=deployment,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            include_model=False,
        )
        azure_headers = {"api-key": api_key, "Content-Type": "application/json"}
        if streaming:
            out = _post_stream(
                url,
                headers=azure_headers,
                payload=payload,
                timeout=timeout,
                service="azure_openai",
                assemble=_assemble_openai_stream,
            )
        else:
            body = _expect_json(
                requests.post(url, headers=azure_headers, json=payload, timeout=timeout),
                "azure_openai",
            )
            out = _normalize_openai(body)
    else:
        if provider_key == "ollama":
            base_url = creds.get("base_url") or "http://localhost:11434/v1"
            api_key = creds.get("api_key", "ollama")
        elif provider_key == "openrouter":
            base_url = creds.get("base_url") or OPENROUTER_BASE_URL
            api_key = creds.get("api_key", "")
            if not api_key:
                raise ValueError("ai_chat: credentials.api_key is required for OpenRouter")
        elif provider_key == "openai_compatible":
            base_url = creds.get("base_url")
            api_key = creds.get("api_key", "")
            if not base_url:
                raise ValueError("ai_chat: base_url is required for openai_compatible")
        else:
            base_url = creds.get("base_url") or "https://api.openai.com/v1"
            api_key = creds.get("api_key", "")
            if not api_key:
                raise ValueError("ai_chat: credentials.api_key is required for OpenAI")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if creds.get("organization"):
            headers["OpenAI-Organization"] = creds["organization"]
        if provider_key == "openrouter":
            if creds.get("site_url"):
                headers["HTTP-Referer"] = creds["site_url"]
            if creds.get("app_name"):
                headers["X-Title"] = creds["app_name"]
        default_model = "openai/gpt-4.1-mini" if provider_key == "openrouter" else "gpt-4.1-mini"
        payload = _openai_payload(
            model=model or creds.get("model") or default_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        chat_url = _with_chat_completions(base_url)
        if streaming:
            out = _post_stream(
                chat_url,
                headers=headers,
                payload=payload,
                timeout=timeout,
                service=provider_key,
                assemble=_assemble_openai_stream,
            )
        else:
            body = _expect_json(
                requests.post(chat_url, headers=headers, json=payload, timeout=timeout),
                provider_key,
            )
            out = _normalize_openai(body)

    out["provider"] = provider_key
    if include_raw:
        out["raw"] = body
    return out


def _openai_payload(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int | None,
    response_format: str,
    include_model: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messages": messages,
        "temperature": float(temperature),
    }
    if include_model:
        payload["model"] = model
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    if response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}
    return payload


def _extract_json_object(text: str) -> Any:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            return json.loads(value[start : end + 1])
        raise


def _render_template(template: str, context: dict[str, Any]) -> str:
    from jinja2 import Environment, StrictUndefined, Undefined

    env = Environment(
        autoescape=False,
        undefined=StrictUndefined if context.get("_strict_undefined") else Undefined,
    )
    return env.from_string(template or "").render(**context)


def _template_context(input: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    row = input if isinstance(input, dict) else {}
    context = {
        "input": input,
        "json": input,
        "row": row,
        "data": input,
    }
    context.update(extra or {})
    return context


def _text_records(
    input: Any,
    *,
    text_field: str,
    max_rows: int,
) -> tuple[list[dict[str, Any]], bool]:
    if is_dataset_ref(input):
        rows = materialize_dataset(input, cap=max_rows, allow_truncate=False)
        return rows, True
    if isinstance(input, list):
        rows: list[dict[str, Any]] = []
        for idx, item in enumerate(input):
            if isinstance(item, dict):
                rows.append(dict(item))
            else:
                rows.append({text_field or "text": str(item), "_index": idx})
        return rows, False
    if isinstance(input, dict):
        for key in ("rows", "records", "items", "data"):
            candidate = input.get(key)
            if isinstance(candidate, list):
                return _text_records(candidate, text_field=text_field, max_rows=max_rows)
        return [dict(input)], False
    return [{text_field or "text": _text_from_input(input)}], False


def _texts_from_records(records: list[dict[str, Any]], text_field: str) -> list[str]:
    field = text_field or "text"
    texts = []
    for row in records:
        value = row.get(field)
        if value is None:
            value = row.get("content") or row.get("body") or row.get("chunk") or row
        texts.append(value if isinstance(value, str) else _compact_json(value))
    return texts


def _call_embeddings(
    *,
    provider: str,
    credentials: dict | None,
    model: str,
    texts: list[str],
    input_type: str = "search_document",
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> tuple[list[list[float]], dict[str, Any]]:
    creds = _provider_creds(credentials)
    provider_key = _effective_provider(provider, creds)
    timeout = max(1, min(300, int(timeout_seconds or DEFAULT_TIMEOUT)))
    if not texts:
        return [], {}

    if provider_key == "cohere":
        api_key = creds.get("api_key", "")
        if not api_key:
            raise ValueError("ai_batch_embeddings: credentials.api_key is required")
        body = _expect_json(
            requests.post(
                creds.get("base_url") or "https://api.cohere.ai/v1/embed",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model or "embed-english-v3.0",
                    "input_type": input_type or "search_document",
                    "texts": texts,
                },
                timeout=timeout,
            ),
            "cohere",
        )
        embeddings = body.get("embeddings") or []
        return [list(map(float, emb)) for emb in embeddings], body

    base_url = creds.get("base_url") or "https://api.openai.com/v1"
    api_key = creds.get("api_key", "")
    if provider_key == "ollama":
        base_url = creds.get("base_url") or "http://localhost:11434/v1"
        api_key = api_key or "ollama"
    elif not api_key:
        raise ValueError("ai_batch_embeddings: credentials.api_key is required")
    body = _expect_json(
        requests.post(
            f"{base_url.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model or "text-embedding-3-small", "input": texts},
            timeout=timeout,
        ),
        provider_key,
    )
    data = body.get("data") if isinstance(body.get("data"), list) else []
    ordered = sorted(
        [item for item in data if isinstance(item, dict)],
        key=lambda item: int(item.get("index", 0)),
    )
    return [list(map(float, item.get("embedding") or [])) for item in ordered], body


def _as_tool_list(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, dict) and isinstance(value.get("tools"), list):
        return [tool for tool in value["tools"] if isinstance(tool, dict)]
    if isinstance(value, list):
        return [tool for tool in value if isinstance(tool, dict)]
    if isinstance(value, dict) and value.get("name"):
        return [value]
    return []


def _merge_tools(*values: Any) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        for tool in _as_tool_list(value):
            name = str(tool.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            merged.append(tool)
    return merged


def _tool_text(tools: list[dict[str, Any]]) -> str:
    compact = [
        {
            "name": tool.get("name"),
            "description": tool.get("description"),
            "schema": tool.get("parameters_schema") or {},
        }
        for tool in tools
    ]
    return _pretty_json(compact)


def _tool_is_side_effecting(tool: dict[str, Any]) -> bool:
    tool_type = str(tool.get("type") or "").lower()
    if tool_type == "workflow":
        return True
    if tool_type == "http":
        return str(tool.get("method") or "GET").upper() not in {"GET", "HEAD", "OPTIONS"}
    return False


async def _execute_tool(tool: dict[str, Any], arguments: dict[str, Any]) -> Any:
    tool_type = str(tool.get("type") or "").lower()
    if tool_type == "http":
        method = str(tool.get("method") or "GET").upper()
        url = str(tool.get("url") or "")
        if not url:
            raise ValueError(f"tool {tool.get('name')}: url is required")
        assert_public_http_url(url, context=f"tool:{tool.get('name')}")
        kwargs: dict[str, Any] = {"timeout": 45}
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            kwargs["json"] = arguments
        else:
            kwargs["params"] = arguments
        response = await asyncio.to_thread(requests.request, method, url, **kwargs)
        return _expect_json(response, f"tool:{tool.get('name')}")
    if tool_type == "workflow":
        workflow_id = str(tool.get("workflow_id") or "")
        if not workflow_id:
            raise ValueError(f"tool {tool.get('name')}: workflow_id is required")
        caller = workflow_caller.get()
        if caller is None:
            raise RuntimeError("ai_agent: workflow tools need a host workflow caller")
        return await caller(workflow_id, arguments)
    raise ValueError(f"unsupported tool type: {tool_type}")


@node(
    name="AI Prompt Template",
    id="ai_prompt_template",
    param_groups={"Options": ["strict_undefined"]},
    category=AI_CATEGORY,
    icon="braces",
    params={
        "system_template": {
            "multiline": True,
            "description": "Jinja template for the system message.",
        },
        "prompt_template": {
            "multiline": True,
            "description": "Jinja template for the user prompt.",
        },
        "strict_undefined": {
            "description": "Fail when a template references a missing value.",
        },
    },
)
def ai_prompt_template(
    input: Any = None,
    system_template: str = "",
    prompt_template: str = "{{ input }}",
    strict_undefined: bool = False,
) -> dict[str, Any]:
    """Render system/user prompt messages from upstream data."""
    context = _template_context(input, {"_strict_undefined": bool(strict_undefined)})
    system = _render_template(system_template, context)
    prompt = _render_template(prompt_template, context)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return {"system": system, "prompt": prompt, "messages": messages}


@node(
    name="AI Chat Model",
    id="ai_chat_model",
    hidden=True,
    deprecated=True,
    replacement_id="ai_chat_model_openai",
    param_groups={
        "Options": [
            "temperature",
            "max_tokens",
            "response_format",
            "timeout_seconds",
            "include_raw",
        ]
    },
    category=AI_CATEGORY,
    icon="ai",
    outputs=["model"],
    params={
        "credentials": {
            **cred_multi("llm_provider", "LLM provider credential", LLM_CREDENTIAL_FIELDS),
            "description": "Credential for the selected chat provider.",
        },
        "provider": {
            "choices": CHAT_PROVIDER_CHOICES,
            "description": "Provider used by connected AI Chat, AI Agent, and RAG nodes.",
        },
        "model": {
            "choices": CHAT_MODEL_CHOICES,
            "placeholder": "gpt-4.1-mini",
            "description": "Model/deployment id. OpenRouter models use provider/model ids.",
            "load_options": "llm_models",
            "depends_on": ["credentials"],
        },
        "temperature": {"description": "Sampling temperature for connected calls."},
        "max_tokens": {"description": "Optional response token limit."},
        "response_format": {
            "choices": ["text", "json_object"],
            "description": "Default response format for compatible providers.",
        },
        "timeout_seconds": {"description": "HTTP timeout, 1-300 seconds."},
        "include_raw": {"description": "Include raw provider payload in connected calls."},
    },
)
def ai_chat_model(
    credentials: dict | None = None,
    provider: str = "openai",
    model: str = "gpt-4.1-mini",
    temperature: float = 0.2,
    max_tokens: int | None = None,
    response_format: str = "text",
    timeout_seconds: int = DEFAULT_TIMEOUT,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Supply reusable chat model configuration to AI nodes."""
    provider_key = _effective_provider(provider, _provider_creds(credentials))
    default_model = "openai/gpt-4.1-mini" if provider_key == "openrouter" else "gpt-4.1-mini"
    return {
        "type": "chat_model",
        "credentials": credentials or {},
        "provider": provider_key,
        "model": model or default_model,
        "temperature": float(temperature),
        "max_tokens": max_tokens,
        "response_format": response_format or "text",
        "timeout_seconds": int(timeout_seconds or DEFAULT_TIMEOUT),
        "include_raw": bool(include_raw),
    }


@node(
    name="AI Chat",
    id="ai_chat",
    param_groups={
        "Options": [
            "system",
            "messages_json",
            "temperature",
            "max_tokens",
            "response_format",
            "timeout_seconds",
            "include_raw",
        ]
    },
    category=AI_CATEGORY,
    icon="ai",
    params={
        "credentials": {
            **cred_multi(
                "llm_provider",
                "LLM provider credential",
                [
                    "provider",
                    "api_key",
                    "base_url",
                    "organization",
                    "azure_endpoint",
                    "azure_api_version",
                    "deployment",
                ],
            ),
            "description": "Provider credential. For Ollama, api_key may be blank.",
        },
        "provider": {
            "choices": CHAT_PROVIDER_CHOICES,
            "description": "LLM provider API shape.",
        },
        "model": {
            "choices": CHAT_MODEL_CHOICES,
            "placeholder": "gpt-4.1-mini",
            "load_options": "llm_models",
            "depends_on": ["credentials"],
        },
        "system": {"multiline": True},
        "prompt": {"multiline": True, "description": "User prompt. Blank uses input."},
        "messages_json": {
            "multiline": True,
            "description": "Optional JSON array of {role, content} messages.",
        },
        "temperature": {"description": "Sampling temperature."},
        "max_tokens": {"description": "Optional response token limit."},
        "response_format": {
            "choices": ["text", "json_object"],
            "description": "Ask compatible providers for text or JSON object output.",
        },
        "timeout_seconds": {"description": "HTTP timeout, 1-300 seconds."},
        "include_raw": {"description": "Include the raw provider response."},
    },
)
def ai_chat(
    input: Any = None,
    credentials: dict | None = None,
    provider: str = "openai",
    model: str = "gpt-4.1-mini",
    system: str = "",
    prompt: str = "",
    messages_json: str = "",
    temperature: float = 0.2,
    max_tokens: int | None = None,
    response_format: str = "text",
    timeout_seconds: int = DEFAULT_TIMEOUT,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Call an LLM and return a normalized response envelope."""
    messages = _messages_from_inputs(
        input,
        system=system,
        prompt=prompt,
        messages_json=messages_json,
    )
    return _call_llm(
        provider=provider,
        credentials=credentials,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        timeout_seconds=timeout_seconds,
        include_raw=include_raw,
    )


@node(
    name="AI Structured Output",
    id="ai_structured_output",
    param_groups={"Options": ["system", "temperature", "max_tokens", "timeout_seconds"]},
    category=AI_CATEGORY,
    icon="braces",
    params={
        "credentials": {
            **cred_multi(
                "llm_provider",
                "LLM provider credential",
                LLM_CREDENTIAL_FIELDS,
            ),
            "description": "Provider credential.",
        },
        "provider": {
            "choices": CHAT_PROVIDER_CHOICES,
        },
        "model": {
            "choices": CHAT_MODEL_CHOICES,
            "placeholder": "gpt-4.1-mini",
            "load_options": "llm_models",
            "depends_on": ["credentials"],
        },
        "system": {"multiline": True},
        "prompt": {"multiline": True, "description": "Extraction prompt. Blank uses input."},
        "schema_json": {
            "multiline": True,
            "description": "JSON Schema the model output must validate against.",
        },
        "temperature": {"description": "Sampling temperature."},
        "max_tokens": {"description": "Optional response token limit."},
        "timeout_seconds": {"description": "HTTP timeout, 1-300 seconds."},
    },
)
def ai_structured_output(
    input: Any = None,
    credentials: dict | None = None,
    provider: str = "openai",
    model: str = "gpt-4.1-mini",
    system: str = "",
    prompt: str = "",
    schema_json: str = '{"type":"object"}',
    temperature: float = 0.0,
    max_tokens: int | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Ask an LLM for JSON and validate it against a JSON Schema."""
    import jsonschema

    schema = _json_loads(schema_json, label="schema_json", default={"type": "object"})
    if not isinstance(schema, dict):
        raise ValueError("schema_json must decode to a JSON object")
    schema_instruction = (
        "Return only valid JSON matching this JSON Schema. Do not wrap it in "
        f"Markdown.\n\nSchema:\n{_pretty_json(schema)}"
    )
    messages = _messages_from_inputs(
        input,
        system="\n\n".join(part for part in [system, schema_instruction] if part),
        prompt=prompt,
    )
    result = _call_llm(
        provider=provider,
        credentials=credentials,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format="json_object",
        timeout_seconds=timeout_seconds,
    )
    parsed = _extract_json_object(result["text"])
    jsonschema.validate(parsed, schema)
    return {
        "json": parsed,
        "text": result["text"],
        "valid": True,
        "usage": result.get("usage", {}),
        "provider": result.get("provider"),
        "model": result.get("model"),
    }


@node(
    name="AI Text Chunker",
    id="ai_text_chunk",
    param_groups={"Options": ["chunk_size", "overlap", "metadata_json"]},
    category=AI_CATEGORY,
    icon="list",
    params={
        "text": {
            "multiline": True,
            "description": "Text to chunk. Blank uses input or ArtifactRef text.",
        },
        "chunk_size": {"description": "Approximate chunk size in characters."},
        "overlap": {"description": "Overlapping characters between chunks."},
        "metadata_json": {"multiline": True, "description": "Metadata added to every chunk."},
    },
)
def ai_text_chunk(
    input: Any = None,
    text: str = "",
    chunk_size: int = 1200,
    overlap: int = 120,
    metadata_json: str = "",
) -> list[dict[str, Any]]:
    """Split text into overlapping chunks for retrieval workflows."""
    payload = _text_from_input(input, text)
    size = max(100, min(20000, int(chunk_size or 1200)))
    ov = max(0, min(size - 1, int(overlap or 0)))
    metadata = _json_loads(metadata_json, label="metadata_json", default={})
    if not isinstance(metadata, dict):
        raise ValueError("metadata_json must decode to an object")
    chunks: list[dict[str, Any]] = []
    start = 0
    idx = 0
    while start < len(payload):
        end = min(len(payload), start + size)
        chunk_text = payload[start:end]
        chunks.append(
            {
                "id": f"chunk-{idx}",
                "index": idx,
                "text": chunk_text,
                "start": start,
                "end": end,
                "char_count": len(chunk_text),
                "metadata": metadata,
            }
        )
        idx += 1
        if end == len(payload):
            break
        start = max(0, end - ov)
    return chunks


@node(
    name="AI Batch Embeddings",
    id="ai_batch_embeddings",
    param_groups={
        "Options": ["output_field", "input_type", "batch_size", "max_rows", "timeout_seconds"]
    },
    category=AI_CATEGORY,
    icon="brand:openai",
    params={
        "credentials": {
            **cred_multi(
                "llm_provider",
                "Embedding provider credential",
                ["provider", "api_key", "base_url"],
            ),
            "description": "OpenAI/OpenAI-compatible/Cohere/Ollama credential.",
        },
        "provider": {"choices": EMBEDDING_PROVIDER_CHOICES},
        "model": {
            "choices": EMBEDDING_MODEL_CHOICES,
            "placeholder": "text-embedding-3-small",
            "load_options": "embedding_models",
            "depends_on": ["credentials"],
        },
        "text_field": {"placeholder": "text"},
        "output_field": {"placeholder": "embedding"},
        "input_type": {
            "choices": ["search_document", "search_query", "classification", "clustering"],
            "description": "Cohere embedding input type.",
        },
        "batch_size": {"description": "Texts per request, max 96."},
        "max_rows": {"description": "Max DatasetRef/list rows to embed, max 1000."},
        "timeout_seconds": {"description": "HTTP timeout per batch."},
    },
)
def ai_batch_embeddings(
    input: Any = None,
    credentials: dict | None = None,
    provider: str = "openai",
    model: str = "text-embedding-3-small",
    text_field: str = "text",
    output_field: str = "embedding",
    input_type: str = "search_document",
    batch_size: int = 64,
    max_rows: int = 500,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> Any:
    """Embed lists or DatasetRefs; DatasetRef inputs return DatasetRef outputs."""
    cap = max(1, min(MAX_DATASET_AI_ROWS, int(max_rows or 500)))
    records, was_dataset = _text_records(input, text_field=text_field, max_rows=cap)
    if len(records) > cap:
        raise ValueError(f"ai_batch_embeddings: input has more than {cap} rows")
    texts = _texts_from_records(records, text_field)
    batch = max(1, min(MAX_EMBEDDING_BATCH, int(batch_size or 64)))
    all_embeddings: list[list[float]] = []
    raw_usage: list[Any] = []
    for start in range(0, len(texts), batch):
        embeddings, raw = _call_embeddings(
            provider=provider,
            credentials=credentials,
            model=model,
            texts=texts[start : start + batch],
            input_type=input_type,
            timeout_seconds=timeout_seconds,
        )
        all_embeddings.extend(embeddings)
        raw_usage.append(raw.get("usage") or raw.get("meta") or {})
    if len(all_embeddings) != len(records):
        raise RuntimeError(
            "ai_batch_embeddings: provider returned "
            f"{len(all_embeddings)} embeddings for {len(records)} texts"
        )
    field = output_field or "embedding"
    enriched = []
    for row, embedding in zip(records, all_embeddings, strict=True):
        enriched.append({**row, field: embedding})
    if was_dataset:
        return records_to_dataset(enriched, name="ai-embeddings.parquet")
    return {"rows": enriched, "count": len(enriched), "usage": raw_usage}


@node(
    name="AI Map Dataset",
    id="ai_dataset_map",
    param_groups={
        "Options": [
            "system",
            "output_column",
            "max_rows",
            "temperature",
            "max_tokens",
            "timeout_seconds",
        ]
    },
    category=AI_CATEGORY,
    icon="sparkles",
    input_kinds={"input": "dataset"},
    output_kinds={"main": "dataset"},
    params={
        "credentials": {
            **cred_multi(
                "llm_provider",
                "LLM provider credential",
                LLM_CREDENTIAL_FIELDS,
            ),
            "description": "Provider credential.",
        },
        "provider": {
            "choices": CHAT_PROVIDER_CHOICES,
        },
        "model": {
            "choices": CHAT_MODEL_CHOICES,
            "placeholder": "gpt-4.1-mini",
            "load_options": "llm_models",
            "depends_on": ["credentials"],
        },
        "system": {"multiline": True},
        "prompt_template": {
            "multiline": True,
            "description": "Jinja template rendered once per row.",
        },
        "output_column": {"placeholder": "ai_output"},
        "max_rows": {"description": "Safety cap, max 1000 rows per run."},
        "temperature": {"description": "Sampling temperature."},
        "max_tokens": {"description": "Optional response token limit."},
        "timeout_seconds": {"description": "HTTP timeout per row."},
    },
)
def ai_dataset_map(
    input: Any = None,
    credentials: dict | None = None,
    provider: str = "openai",
    model: str = "gpt-4.1-mini",
    system: str = "",
    prompt_template: str = "Summarize this row: {{ row }}",
    output_column: str = "ai_output",
    max_rows: int = 100,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run an LLM over each row of a DatasetRef and append an output column."""
    if not is_dataset_ref(input):
        raise ValueError("ai_dataset_map requires a DatasetRef input")
    cap = max(1, min(MAX_DATASET_AI_ROWS, int(max_rows or 100)))
    rows = materialize_dataset(input, cap=cap, allow_truncate=False)
    output_col = output_column or "ai_output"
    enriched: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        prompt = _render_template(
            prompt_template,
            _template_context(row, {"index": idx, "_strict_undefined": False}),
        )
        result = _call_llm(
            provider=provider,
            credentials=credentials,
            model=model,
            messages=_messages_from_inputs(row, system=system, prompt=prompt),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        enriched.append({**row, output_col: result["text"]})
    return records_to_dataset(enriched, name="ai-map.parquet")


@node(
    name="AI Vector Retriever",
    id="ai_vector_retriever",
    param_groups={
        "Options": [
            "embedding_provider",
            "embedding_model",
            "namespace",
            "top_k",
            "include_metadata",
            "timeout_seconds",
        ]
    },
    category=AI_CATEGORY,
    icon="brand:pinecone",
    params={
        "embedding_credentials": {
            **cred_multi(
                "llm_provider",
                "Embedding provider credential",
                EMBEDDING_CREDENTIAL_FIELDS,
            ),
            "description": "Credential for OpenAI/OpenAI-compatible embeddings.",
        },
        "pinecone_credentials": {
            **cred_multi("pinecone", "Pinecone credentials", ["api_key", "index_host"]),
            "description": "Pinecone API key + index host.",
        },
        "query": {"multiline": True, "description": "Search query. Blank uses input."},
        "embedding_provider": {"choices": EMBEDDING_PROVIDER_CHOICES},
        "embedding_model": {
            "choices": EMBEDDING_MODEL_CHOICES,
            "placeholder": "text-embedding-3-small",
            "load_options": "embedding_models",
            "depends_on": ["embedding_credentials"],
        },
        "namespace": {"placeholder": "default"},
        "top_k": {"description": "Number of matches to return."},
        "include_metadata": {"description": "Include vector metadata."},
        "timeout_seconds": {"description": "HTTP timeout."},
    },
)
def ai_vector_retriever(
    input: Any = None,
    embedding_credentials: dict | None = None,
    pinecone_credentials: dict | None = None,
    query: str = "",
    embedding_provider: str = "openai",
    embedding_model: str = "text-embedding-3-small",
    namespace: str = "default",
    top_k: int = 5,
    include_metadata: bool = True,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Embed a query and retrieve nearest chunks from Pinecone."""
    query_text = _text_from_input(input, query)
    embeddings, _raw = _call_embeddings(
        provider=embedding_provider,
        credentials=embedding_credentials,
        model=embedding_model,
        texts=[query_text],
        input_type="search_query",
        timeout_seconds=timeout_seconds,
    )
    if not embeddings:
        raise RuntimeError("ai_vector_retriever: embedding provider returned no vector")
    creds = _provider_creds(pinecone_credentials)
    api_key = creds.get("api_key", "")
    index_host = creds.get("index_host", "")
    if not api_key or not index_host:
        raise ValueError("ai_vector_retriever: Pinecone api_key and index_host are required")
    body = _expect_json(
        requests.post(
            _hosted_https_url(index_host, "/query", context="pinecone"),
            headers={"Api-Key": api_key, "Content-Type": "application/json"},
            json={
                "vector": embeddings[0],
                "topK": max(1, int(top_k or 5)),
                "namespace": namespace or "default",
                "includeMetadata": bool(include_metadata),
            },
            timeout=max(1, min(300, int(timeout_seconds or DEFAULT_TIMEOUT))),
        ),
        "pinecone",
    )
    matches = body.get("matches") if isinstance(body.get("matches"), list) else []
    return {
        "query": query_text,
        "matches": matches,
        "count": len(matches),
        "raw": body,
    }


@node(
    name="AI RAG Answer",
    id="ai_rag_answer",
    param_groups={
        "Options": ["max_context_chars", "system", "temperature", "max_tokens", "timeout_seconds"]
    },
    category=AI_CATEGORY,
    icon="sparkles",
    params={
        "credentials": {
            **cred_multi(
                "llm_provider",
                "LLM provider credential",
                LLM_CREDENTIAL_FIELDS,
            ),
            "description": "Provider credential.",
        },
        "provider": {
            "choices": CHAT_PROVIDER_CHOICES,
        },
        "model": {
            "choices": CHAT_MODEL_CHOICES,
            "placeholder": "gpt-4.1-mini",
            "load_options": "llm_models",
            "depends_on": ["credentials"],
        },
        "question": {"multiline": True, "description": "Question. Blank uses input.query."},
        "context_field": {"placeholder": "text"},
        "max_context_chars": {"description": "Total context character budget."},
        "system": {"multiline": True},
        "temperature": {"description": "Sampling temperature."},
        "max_tokens": {"description": "Optional response token limit."},
        "timeout_seconds": {"description": "HTTP timeout."},
    },
)
def ai_rag_answer(
    input: Any = None,
    credentials: dict | None = None,
    provider: str = "openai",
    model: str = "gpt-4.1-mini",
    question: str = "",
    context_field: str = "text",
    max_context_chars: int = 12000,
    system: str = "",
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Answer a question using retrieved context chunks."""
    q = question
    matches: Iterable[Any] = []
    if isinstance(input, dict):
        q = q or str(input.get("query") or input.get("question") or "")
        matches = input.get("matches") or input.get("contexts") or []
    elif isinstance(input, list):
        matches = input
    if not q:
        q = _text_from_input(input)
    budget = max(1000, min(100000, int(max_context_chars or 12000)))
    context_parts: list[str] = []
    used = 0
    for idx, match in enumerate(matches):
        metadata = match.get("metadata") if isinstance(match, dict) else {}
        source = metadata if isinstance(metadata, dict) else match
        if isinstance(source, dict):
            text = str(
                source.get(context_field)
                or source.get("text")
                or source.get("content")
                or source.get("chunk")
                or ""
            )
        else:
            text = str(source)
        if not text:
            continue
        piece = f"[{idx + 1}] {text}"
        if used + len(piece) > budget:
            break
        context_parts.append(piece)
        used += len(piece)
    context = "\n\n".join(context_parts)
    default_system = (
        "Answer using only the supplied context. If the context is insufficient, "
        "say what is missing. Cite context numbers in brackets."
    )
    prompt = f"Question:\n{q}\n\nContext:\n{context}"
    result = _call_llm(
        provider=provider,
        credentials=credentials,
        model=model,
        messages=_messages_from_inputs(None, system=system or default_system, prompt=prompt),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    return {
        "answer": result["text"],
        "question": q,
        "context": context_parts,
        "usage": result.get("usage", {}),
        "provider": result.get("provider"),
        "model": result.get("model"),
    }


@node(
    name="AI Simple Memory",
    id="ai_memory_buffer",
    hidden=True,
    deprecated=True,
    replacement_id="ai_buffer_memory",
    param_groups={"Options": ["input_role", "max_messages"]},
    category=AI_CATEGORY,
    icon="database",
    outputs=["memory"],
    params={
        "session_id": {
            "placeholder": "chat_history",
            "description": "Session or conversation key used for audit metadata.",
        },
        "messages_json": {
            "multiline": True,
            "description": "Optional JSON array of prior {role, content} messages.",
        },
        "input_role": {
            "choices": ["none", "user", "assistant", "system"],
            "description": "Role used when appending upstream input text to memory.",
        },
        "max_messages": {"description": "Most recent messages to keep, 0 keeps all."},
    },
)
def ai_memory_buffer(
    input: Any = None,
    session_id: str = "chat_history",
    messages_json: str = "",
    input_role: str = "none",
    max_messages: int = 20,
) -> dict[str, Any]:
    """Supply bounded chat memory to AI Agent."""
    raw_messages = _json_loads(messages_json, label="messages_json", default=[])
    if not isinstance(raw_messages, list):
        raise ValueError("messages_json must be a JSON array")
    messages = _memory_messages({"messages": raw_messages})
    role = str(input_role or "none")
    if role != "none" and input is not None:
        if role not in {"system", "user", "assistant"}:
            raise ValueError("ai_memory_buffer: input_role is not supported")
        messages.append({"role": role, "content": _text_from_input(input)})
    limit = max(0, min(500, int(max_messages or 0)))
    return {
        "type": "memory",
        "memory_type": "buffer",
        "session_id": session_id or "chat_history",
        "messages": messages[-limit:] if limit else messages,
        "max_messages": limit,
    }


@node(
    name="AI Tool",
    id="ai_tool",
    hidden=True,
    deprecated=True,
    replacement_id="ai_http_tool",
    param_groups={"Options": ["parameters_schema_json", "url", "method", "workflow_id"]},
    category=AI_CATEGORY,
    icon="code",
    params={
        "name": {"placeholder": "lookup_customer"},
        "description": {"multiline": True},
        "tool_type": {"choices": ["http", "workflow"]},
        "parameters_schema_json": {
            "multiline": True,
            "description": "JSON Schema for tool arguments.",
        },
        "url": {"placeholder": "https://api.example.com/search"},
        "method": {"choices": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "workflow_id": {"description": "Workflow to call when tool_type=workflow."},
    },
)
def ai_tool(
    input: Any = None,
    name: str = "",
    description: str = "",
    tool_type: str = "http",
    parameters_schema_json: str = '{"type":"object","properties":{}}',
    url: str = "",
    method: str = "GET",
    workflow_id: str = "",
) -> dict[str, Any]:
    """Define an explicit tool that AI Agent may call."""
    existing = _as_tool_list(input)
    schema = _json_loads(parameters_schema_json, label="parameters_schema_json", default={})
    if not isinstance(schema, dict):
        raise ValueError("parameters_schema_json must decode to an object")
    tool_name = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip())[:64]
    if not tool_name:
        raise ValueError("ai_tool: name is required")
    tool = {
        "name": tool_name,
        "description": description,
        "type": tool_type,
        "parameters_schema": schema,
        "url": url,
        "method": (method or "GET").upper(),
        "workflow_id": workflow_id,
    }
    return {"tools": [*existing, tool]}


@node(
    name="AI Tool Box",
    id="ai_tool_box",
    hidden=True,
    deprecated=True,
    replacement_id="ai_tool_bundle",
    category=AI_CATEGORY,
    icon="wrench",
    inputs=["tool_1", "tool_2", "tool_3", "tool_4", "tool_5"],
    outputs=["tools"],
    params={
        "strict": {
            "description": "Fail when no valid tools are connected.",
        },
    },
)
def ai_tool_box(
    tool_1: Any = None,
    tool_2: Any = None,
    tool_3: Any = None,
    tool_4: Any = None,
    tool_5: Any = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Merge multiple AI Tool outputs for a single AI Agent tools port."""
    tools = _merge_tools(tool_1, tool_2, tool_3, tool_4, tool_5)
    if strict and not tools:
        raise ValueError("ai_tool_box: connect at least one AI Tool")
    return {"tools": tools, "count": len(tools)}


@node(
    name="AI Agent",
    id="ai_agent",
    hidden=True,
    deprecated=True,
    replacement_id="ai_agent_v2",
    param_groups={
        "Options": [
            "fallback_model",
            "system",
            "max_steps",
            "memory_max_messages",
            "allow_side_effects",
            "temperature",
            "timeout_seconds",
        ]
    },
    category=AI_CATEGORY,
    icon="ai",
    inputs=["input", "model", "memory", "tools"],
    params={
        "credentials": {
            **cred_multi(
                "llm_provider",
                "LLM provider credential",
                LLM_CREDENTIAL_FIELDS,
            ),
            "description": "Provider credential.",
        },
        "provider": {
            "choices": CHAT_PROVIDER_CHOICES,
        },
        "fallback_model": {
            "choices": CHAT_MODEL_CHOICES,
            "placeholder": "gpt-4.1-mini",
            "description": "Used only when no AI Chat Model is connected to the model port.",
            "load_options": "llm_models",
            "depends_on": ["credentials"],
        },
        "system": {"multiline": True},
        "task": {"multiline": True, "description": "Agent task. Blank uses input.task/input."},
        "max_steps": {"description": "Max tool/planning steps, hard max 10."},
        "memory_max_messages": {
            "description": "Most recent connected memory messages to include, 0 keeps all.",
        },
        "allow_side_effects": {
            "description": "Allow workflow tools and non-GET HTTP tools.",
        },
        "temperature": {"description": "Sampling temperature."},
        "timeout_seconds": {"description": "HTTP timeout per model call."},
    },
)
async def ai_agent(
    input: Any = None,
    model: Any = None,
    memory: Any = None,
    tools: Any = None,
    credentials: dict | None = None,
    provider: str = "openai",
    fallback_model: str = "gpt-4.1-mini",
    system: str = "",
    task: str = "",
    max_steps: int = 4,
    memory_max_messages: int = 20,
    allow_side_effects: bool = False,
    temperature: float = 0.1,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run a bounded, auditable tool-using agent loop."""
    tool_list = _merge_tools(tools, input)
    chat_config = _resolve_chat_config(
        model,
        credentials=credentials,
        provider=provider,
        model=fallback_model,
        temperature=temperature,
        response_format="json_object",
        timeout_seconds=timeout_seconds,
    )
    if isinstance(input, dict):
        task_text = task or str(input.get("task") or input.get("prompt") or "")
    else:
        task_text = task or _text_from_input(input)
    if not task_text:
        raise ValueError("ai_agent: task is required")
    steps_limit = max(1, min(MAX_AGENT_STEPS, int(max_steps or 4)))
    step_log: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    agent_system = system or (
        "You are a bounded workflow agent. You may use tools only when needed. "
        'Return JSON only: either {"action":"final","answer":"..."} or '
        '{"action":"tool","tool":"tool_name","arguments":{...}}.'
    )
    for step in range(steps_limit):
        prompt = (
            f"Task:\n{task_text}\n\nAvailable tools:\n{_tool_text(tool_list)}\n\n"
            f"Prior observations:\n{_pretty_json(observations)}\n\n"
            "Choose the next action as JSON."
        )
        result = await asyncio.to_thread(
            _call_llm,
            provider=chat_config["provider"],
            credentials=chat_config["credentials"],
            model=chat_config["model"],
            messages=_with_memory(
                _messages_from_inputs(None, system=agent_system, prompt=prompt),
                memory,
                max_messages=memory_max_messages,
            ),
            temperature=chat_config["temperature"],
            max_tokens=chat_config["max_tokens"],
            response_format="json_object",
            timeout_seconds=chat_config["timeout_seconds"],
        )
        decision = _extract_json_object(result["text"])
        if not isinstance(decision, dict):
            raise RuntimeError("ai_agent: model decision was not an object")
        action = str(decision.get("action") or "").lower()
        step_entry = {"step": step + 1, "decision": decision}
        if action == "final":
            answer = str(decision.get("answer") or "")
            step_log.append({**step_entry, "status": "final"})
            return {
                "answer": answer,
                "steps": step_log,
                "observations": observations,
                "provider": chat_config["provider"],
                "model": result.get("model") or chat_config["model"],
            }
        if action != "tool":
            raise RuntimeError(f"ai_agent: unsupported action {action!r}")
        tool_name = str(decision.get("tool") or "")
        tool = next(
            (candidate for candidate in tool_list if candidate.get("name") == tool_name),
            None,
        )
        if tool is None:
            raise RuntimeError(f"ai_agent: unknown tool {tool_name!r}")
        if _tool_is_side_effecting(tool) and not allow_side_effects:
            raise RuntimeError(
                f"ai_agent: tool {tool_name!r} may have side effects; "
                "enable allow_side_effects to run it"
            )
        arguments = decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
        observation = await _execute_tool(tool, arguments)
        observations.append({"tool": tool_name, "arguments": arguments, "result": observation})
        step_log.append({**step_entry, "status": "tool_executed"})
    return {
        "answer": "",
        "steps": step_log,
        "observations": observations,
        "stopped_reason": "max_steps",
        "provider": chat_config["provider"],
        "model": result.get("model") if "result" in locals() else chat_config["model"],
    }


@node(
    name="AI Moderation Guard",
    id="ai_moderation_guard",
    param_groups={"Options": ["model"]},
    category=AI_CATEGORY,
    icon="alert",
    outputs=["safe", "flagged"],
    params={
        "credentials": {
            **cred_single("openai", "api_key", "OpenAI API key"),
            "description": "OpenAI API key.",
        },
        "text": {"multiline": True, "description": "Text to moderate. Blank uses input."},
        "model": {"choices": MODERATION_MODEL_CHOICES, "placeholder": "omni-moderation-latest"},
    },
)
def ai_moderation_guard(
    input: Any = None,
    credentials: str = "",
    text: str = "",
    model: str = "omni-moderation-latest",
) -> dict[str, Any]:
    """Route text to safe/flagged outputs using OpenAI moderation."""
    api_key = credentials
    if not api_key:
        raise ValueError("ai_moderation_guard: credentials are required")
    payload_text = _text_from_input(input, text)
    body = _expect_json(
        requests.post(
            "https://api.openai.com/v1/moderations",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model or "omni-moderation-latest", "input": payload_text},
            timeout=45,
        ),
        "openai",
    )
    results = body.get("results") if isinstance(body.get("results"), list) else []
    first = results[0] if results and isinstance(results[0], dict) else {}
    out = {
        "text": payload_text,
        "flagged": bool(first.get("flagged")),
        "categories": first.get("categories") or {},
        "category_scores": first.get("category_scores") or {},
        "raw": body,
    }
    return {"flagged": out} if out["flagged"] else {"safe": out}


@node(
    name="AI Vision Analyze",
    id="ai_vision_analyze",
    param_groups={"Options": ["mime_type", "timeout_seconds"]},
    category=AI_CATEGORY,
    icon="eye",
    params={
        "credentials": {
            **cred_multi("llm_provider", "Vision provider credential", ["api_key", "base_url"]),
            "description": "OpenAI-compatible vision credential.",
        },
        "model": {
            "choices": VISION_MODEL_CHOICES,
            "placeholder": "gpt-4.1-mini",
            "load_options": "llm_models",
            "depends_on": ["credentials"],
        },
        "prompt": {"multiline": True},
        "image_url": {"description": "Public image URL. Used when image_base64 is blank."},
        "image_base64": {
            "multiline": True,
            "description": "Base64 image bytes. Dict input may provide image_base64/data.",
        },
        "mime_type": {"placeholder": "image/png"},
        "timeout_seconds": {"description": "HTTP timeout."},
    },
)
def ai_vision_analyze(
    input: Any = None,
    credentials: dict | None = None,
    model: str = "gpt-4.1-mini",
    prompt: str = "Describe this image.",
    image_url: str = "",
    image_base64: str = "",
    mime_type: str = "image/png",
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Analyze an image with an OpenAI-compatible vision chat model."""
    creds = _provider_creds(credentials)
    api_key = creds.get("api_key", "")
    if not api_key:
        raise ValueError("ai_vision_analyze: credentials.api_key is required")
    if isinstance(input, dict):
        image_url = image_url or str(input.get("image_url") or "")
        image_base64 = image_base64 or str(input.get("image_base64") or input.get("data") or "")
        prompt = prompt or str(input.get("prompt") or "")
    if image_base64:
        image_ref = f"data:{mime_type or 'image/png'};base64,{image_base64}"
    elif image_url:
        image_ref = image_url
    else:
        raise ValueError("ai_vision_analyze: image_url or image_base64 is required")
    payload = {
        "model": model or "gpt-4.1-mini",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or "Describe this image."},
                    {"type": "image_url", "image_url": {"url": image_ref}},
                ],
            }
        ],
    }
    body = _expect_json(
        requests.post(
            _with_chat_completions(creds.get("base_url") or "https://api.openai.com/v1"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=max(1, min(300, int(timeout_seconds or DEFAULT_TIMEOUT))),
        ),
        "openai",
    )
    out = _normalize_openai(body)
    out["raw"] = body
    return out


@node(
    name="AI Image Generate",
    id="ai_image_generate",
    param_groups={"Options": ["model", "size", "filename"]},
    category=AI_CATEGORY,
    icon="sparkles",
    output_kinds={"main": "artifact"},
    params={
        "credentials": {
            **cred_single("openai", "api_key", "OpenAI API key"),
            "description": "OpenAI API key.",
        },
        "prompt": {"multiline": True, "description": "Image prompt. Blank uses input."},
        "model": {"choices": IMAGE_MODEL_CHOICES, "placeholder": "gpt-image-1"},
        "size": {"choices": ["1024x1024", "1024x1536", "1536x1024", "auto"]},
        "filename": {"placeholder": "generated.png"},
    },
)
def ai_image_generate(
    input: Any = None,
    credentials: str = "",
    prompt: str = "",
    model: str = "gpt-image-1",
    size: str = "1024x1024",
    filename: str = "generated.png",
) -> dict[str, Any]:
    """Generate an image and store it as an artifact."""
    api_key = credentials
    if not api_key:
        raise ValueError("ai_image_generate: credentials are required")
    payload_prompt = _text_from_input(input, prompt)
    body = _expect_json(
        requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model or "gpt-image-1",
                "prompt": payload_prompt,
                "size": size or "1024x1024",
            },
            timeout=180,
        ),
        "openai",
    )
    data = body.get("data") if isinstance(body.get("data"), list) else []
    first = data[0] if data and isinstance(data[0], dict) else {}
    b64 = str(first.get("b64_json") or "")
    if not b64:
        url = first.get("url")
        if not url:
            raise RuntimeError("ai_image_generate: provider returned no image")
        image_response = requests.get(str(url), timeout=180)
        if image_response.status_code >= 400:
            raise RuntimeError(f"ai_image_generate: image URL HTTP {image_response.status_code}")
        data_bytes = image_response.content
    else:
        data_bytes = base64.b64decode(b64)
    return write_bytes(
        data_bytes,
        name=filename or "generated.png",
        content_type="image/png",
        kind="image",
        metadata={"model": model, "prompt": payload_prompt},
    )
