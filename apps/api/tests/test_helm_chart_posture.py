"""Chart hardening gate (F-08).

``helm lint`` only checks that templates render. It says nothing about whether
the rendered pods are actually hardened, which is how the chart shipped without
a securityContext, startupProbe, PodDisruptionBudget or ServiceAccount.

These tests render the chart and assert the posture of the result. They skip
when helm is unavailable so a local run without it stays green; CI's
``deploy-config`` lane has helm and runs them for real.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

CHART = Path(__file__).resolve().parents[3] / "deploy" / "helm" / "nodyra"

BASE_ARGS = [
    "--set", "api.corsOrigins=https://nodyra.example.com",
    "--set-string", "secret.key=chart-test-secret-key-at-least-32-bytes",
    "--set-string", "secret.internalApiToken=chart-test-token",
]

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm is not installed"
)


def _render(*extra: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "release", str(CHART), *BASE_ARGS, *extra],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _deployments(docs: list[dict]) -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}


def test_chart_renders_all_three_deployments() -> None:
    """Guard the guard: an empty render would make every assertion vacuous."""
    names = set(_deployments(_render()))
    assert names == {"release-api", "release-web", "release-worker"}, names


@pytest.mark.parametrize("name", ["release-api", "release-web", "release-worker"])
def test_pods_run_as_non_root_with_a_read_only_root_filesystem(name: str) -> None:
    """Every pod must satisfy the `restricted` Pod Security Standard.

    Without this the pods start as root and a namespace enforcing `restricted`
    rejects the install outright.
    """
    deployment = _deployments(_render())[name]
    spec = deployment["spec"]["template"]["spec"]
    pod_ctx = spec.get("securityContext") or {}
    container = spec["containers"][0]
    ctr_ctx = container.get("securityContext") or {}

    assert pod_ctx.get("runAsNonRoot") is True, f"{name}: runs as root"
    assert int(pod_ctx.get("runAsUser", 0)) > 0, f"{name}: no explicit uid"
    assert (pod_ctx.get("seccompProfile") or {}).get("type") == "RuntimeDefault"
    assert ctr_ctx.get("allowPrivilegeEscalation") is False
    assert ctr_ctx.get("readOnlyRootFilesystem") is True
    assert (ctr_ctx.get("capabilities") or {}).get("drop") == ["ALL"]


@pytest.mark.parametrize("name", ["release-api", "release-web", "release-worker"])
def test_pods_do_not_mount_a_kubernetes_api_token(name: str) -> None:
    """Nodyra never calls the Kubernetes API from these pods, so the default
    ServiceAccount's auto-mounted token is credential surface with no purpose."""
    spec = _deployments(_render())[name]["spec"]["template"]["spec"]
    assert spec.get("automountServiceAccountToken") is False
    assert spec.get("serviceAccountName") == "release-nodyra"


def test_every_writable_path_has_a_volume() -> None:
    """A read-only root filesystem is only correct if the paths the process
    genuinely writes to are mounted; otherwise the pod crash-loops."""
    deployments = _deployments(_render())
    required = {
        "release-api": {"/app/envs", "/app/artifacts", "/tmp"},
        "release-worker": {"/app/envs", "/app/artifacts", "/tmp"},
        "release-web": {"/var/cache/nginx", "/var/run", "/tmp"},
    }
    for name, paths in required.items():
        container = deployments[name]["spec"]["template"]["spec"]["containers"][0]
        mounted = {m["mountPath"] for m in container.get("volumeMounts", [])}
        assert paths <= mounted, f"{name}: missing writable mounts {paths - mounted}"


@pytest.mark.parametrize("name", ["release-api", "release-web"])
def test_startup_probe_gates_liveness(name: str) -> None:
    """Boot work must be waited out, not restarted by livenessProbe."""
    container = _deployments(_render())[name]["spec"]["template"]["spec"]["containers"][0]
    assert "startupProbe" in container, f"{name}: no startupProbe"
    assert "livenessProbe" in container


def test_worker_startup_probe_appears_with_the_health_port() -> None:
    docs = _render("--set", "worker.healthPort=8080")
    container = _deployments(docs)["release-worker"]["spec"]["template"]["spec"]["containers"][0]
    assert "startupProbe" in container
    assert "readinessProbe" in container


def test_disruption_budgets_appear_only_above_one_replica() -> None:
    """A PDB with minAvailable: 1 on a single-replica Deployment makes the pod
    undrainable and blocks node upgrades — worse than having none."""
    single = [d for d in _render() if d["kind"] == "PodDisruptionBudget"]
    assert single == [], "a single-replica release must not emit a PDB"

    scaled = _render(
        "--set", "api.replicas=3", "--set", "worker.replicas=3", "--set", "web.replicas=2"
    )
    names = {d["metadata"]["name"] for d in scaled if d["kind"] == "PodDisruptionBudget"}
    assert names == {"release-api", "release-worker", "release-web"}, names


def test_autoscaling_is_opt_in_and_targets_both_planes() -> None:
    assert not [d for d in _render() if d["kind"] == "HorizontalPodAutoscaler"]

    docs = _render("--set", "autoscaling.enabled=true")
    hpas = {d["metadata"]["name"]: d for d in docs if d["kind"] == "HorizontalPodAutoscaler"}
    assert set(hpas) == {"release-api", "release-worker"}
    # Workers drain in-flight runs for up to 120s; scaling them down eagerly
    # would cancel work the autoscaler only just scheduled.
    behavior = hpas["release-worker"]["spec"].get("behavior") or {}
    assert behavior["scaleDown"]["stabilizationWindowSeconds"] >= 120


def test_network_policy_is_opt_in_and_denies_ingress_to_workers() -> None:
    assert not [d for d in _render() if d["kind"] == "NetworkPolicy"]

    docs = _render("--set", "networkPolicy.enabled=true")
    policies = {d["metadata"]["name"]: d for d in docs if d["kind"] == "NetworkPolicy"}
    assert set(policies) == {"release-api", "release-worker"}
    worker = policies["release-worker"]["spec"]
    # Nothing should ever dial a worker: an empty ingress list is deny-all.
    assert worker["ingress"] == []
    assert "Ingress" in worker["policyTypes"]


def test_restricted_egress_still_permits_dns() -> None:
    """A CIDR allowlist that forgets DNS breaks every hostname lookup."""
    docs = _render(
        "--set", "networkPolicy.enabled=true",
        "--set", "networkPolicy.allowedEgressCIDRs={10.0.0.0/8}",
    )
    worker = next(
        d for d in docs
        if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "release-worker"
    )["spec"]
    assert "Egress" in worker["policyTypes"]
    ports = [p for rule in worker["egress"] for p in rule.get("ports", [])]
    assert {"protocol": "UDP", "port": 53} in ports, json.dumps(worker["egress"])
