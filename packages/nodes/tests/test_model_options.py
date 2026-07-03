import nodyra_nodes.ai_v2.model_options as mo


def test_openrouter_models_parses_data_ids(monkeypatch):
    def fake_fetch(method, url, **kw):
        assert method == "GET"
        assert url == "https://openrouter.ai/api/v1/models"
        assert kw["headers"]["Authorization"] == "Bearer sk-or"
        return {"data": [{"id": "openai/gpt-4.1-mini"}, {"id": "anthropic/claude-3.7-sonnet"}]}

    monkeypatch.setattr(mo, "_fetch_json", fake_fetch)
    out = mo.llm_models(credentials={"provider": "openrouter", "api_key": "sk-or"})
    assert out == [
        {"value": "openai/gpt-4.1-mini", "label": "openai/gpt-4.1-mini", "description": ""},
        {
            "value": "anthropic/claude-3.7-sonnet",
            "label": "anthropic/claude-3.7-sonnet",
            "description": "",
        },
    ]


def test_full_chat_endpoint_base_url_is_normalized(monkeypatch):
    # A base_url that already includes /chat/completions must not produce
    # .../chat/completions/models — it should hit the API base.
    seen = {}

    def fake_fetch(method, url, **kw):
        seen["url"] = url
        return {"data": [{"id": "openai/gpt-4o-mini"}]}

    monkeypatch.setattr(mo, "_fetch_json", fake_fetch)
    out = mo.llm_models(
        credentials={
            "provider": "openrouter",
            "api_key": "sk-or",
            "base_url": "https://openrouter.ai/api/v1/chat/completions",
        }
    )
    assert seen["url"] == "https://openrouter.ai/api/v1/models"
    assert [o["value"] for o in out] == ["openai/gpt-4o-mini"]


def test_normalize_api_base_trims_known_suffixes():
    assert mo.normalize_api_base("https://x/v1/chat/completions") == "https://x/v1"
    assert mo.normalize_api_base("https://x/v1/") == "https://x/v1"
    assert mo.normalize_api_base("") == ""
    assert mo.normalize_api_base(None) == ""


def test_ollama_models_use_tags_endpoint(monkeypatch):
    def fake_fetch(method, url, **kw):
        assert url == "http://localhost:11434/api/tags"
        return {"models": [{"name": "llama3.1"}, {"name": "qwen2.5"}]}

    monkeypatch.setattr(mo, "_fetch_json", fake_fetch)
    out = mo.llm_models(credentials={"provider": "ollama", "base_url": "http://localhost:11434/v1"})
    assert [o["value"] for o in out] == ["llama3.1", "qwen2.5"]


def test_unreachable_provider_falls_back_to_curated(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(mo, "_fetch_json", boom)
    out = mo.llm_models(credentials={"provider": "openai", "api_key": "sk"})
    assert out  # non-empty curated fallback
    assert all(set(o) == {"value", "label", "description"} for o in out)


def test_loader_is_registered():
    import nodyra_nodes  # noqa: F401 - triggers registration
    from nodyra_nodes.integrations_v2.dynamic_options import list_loader_ids

    assert "llm_models" in list_loader_ids()
    assert "embedding_models" in list_loader_ids()
