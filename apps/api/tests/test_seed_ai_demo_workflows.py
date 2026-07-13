from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_seed_module():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "seed_ai_demo_workflows.py"
    spec = importlib.util.spec_from_file_location("seed_ai_demo_workflows", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_demo_seed_includes_fake_credential_and_active_webhook() -> None:
    seed = _load_seed_module()

    assert seed.DEMO_CREDENTIAL["name"] == "Demo Fake API Key"
    assert seed.DEMO_CREDENTIAL["data"]["api_key"] == "demo_not_a_real_secret"

    webhook = next(
        item for item in seed.WORKFLOWS if item["name"] == "Demo - Runnable Webhook Intake"
    )
    assert webhook["active"] is True
    assert webhook["webhook_path"] == "demo/intake"

    hook_node = next(node for node in webhook["graph"]["nodes"] if node["id"] == "hook")
    assert hook_node["type"] == "webhook_trigger"
    assert hook_node["params"]["path"] == "demo/intake"
