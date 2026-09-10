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
    "--set",
    "api.corsOrigins=https://nodyra.example.com",
    "--set-string",
    "secret.key=chart-test-secret-key-at-least-32-bytes",
    "--set-string",
    "secret.internalApiToken=chart-test-token",
]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


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
        "release-web": {"/var/cache/nginx", "/var/run", "/tmp", "/etc/nginx/conf.d"},
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
        "--set",
        "networkPolicy.enabled=true",
        "--set",
        "networkPolicy.allowedEgressCIDRs={10.0.0.0/8}",
    )
    worker = next(
        d
        for d in docs
        if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "release-worker"
    )["spec"]
    assert "Egress" in worker["policyTypes"]
    ports = [p for rule in worker["egress"] for p in rule.get("ports", [])]
    assert {"protocol": "UDP", "port": 53} in ports, json.dumps(worker["egress"])


def test_web_runtime_proxy_targets_the_release_service_and_configured_port() -> None:
    docs = _render(
        "--namespace",
        "production",
        "--set",
        "clusterDomain=internal.example",
        "--set",
        "api.port=8100",
        "--set",
        "web.port=5200",
    )
    web = _deployments(docs)["release-web"]["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item.get("value") for item in web["env"]}
    assert env["NODYRA_API_UPSTREAM"] == "release-api.production.svc.internal.example:8100"
    assert env["NODYRA_WEB_PORT"] == "5200"
    assert env["NODYRA_MAX_BODY_SIZE"] == "52m"


def test_ingress_uses_web_proxy_to_strip_the_browser_api_prefix() -> None:
    docs = _render("--set", "ingress.enabled=true", "--set", "web.port=5200")
    ingress = next(doc for doc in docs if doc["kind"] == "Ingress")
    paths = {
        item["path"]: item["backend"]["service"]
        for item in ingress["spec"]["rules"][0]["http"]["paths"]
    }
    assert paths["/api"] == {"name": "release-web", "port": {"number": 5200}}
    assert paths["/mcp"]["name"] == "release-api"


def test_api_and_workers_share_retained_runtime_storage() -> None:
    docs = _render()
    claims = {
        doc["metadata"]["name"]: doc for doc in docs if doc["kind"] == "PersistentVolumeClaim"
    }
    assert set(claims) == {"release-envs", "release-artifacts"}
    for claim in claims.values():
        assert claim["spec"]["accessModes"] == ["ReadWriteMany"]
        assert claim["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    for name in ("release-api", "release-worker"):
        volumes = _deployments(docs)[name]["spec"]["template"]["spec"]["volumes"]
        by_name = {volume["name"]: volume for volume in volumes}
        for volume in ("envs", "artifacts"):
            assert by_name[volume]["persistentVolumeClaim"]["claimName"] == f"release-{volume}"


def test_existing_runtime_claims_are_reused_without_creating_duplicates() -> None:
    docs = _render(
        "--set",
        "persistence.envs.existingClaim=team-envs",
        "--set",
        "persistence.artifacts.existingClaim=team-artifacts",
    )
    assert not [doc for doc in docs if doc["kind"] == "PersistentVolumeClaim"]
    volumes = _deployments(docs)["release-worker"]["spec"]["template"]["spec"]["volumes"]
    assert {
        volume["persistentVolumeClaim"]["claimName"]
        for volume in volumes
        if "persistentVolumeClaim" in volume
    } == {"team-envs", "team-artifacts"}


def test_migrations_can_run_before_chart_resources_exist_and_use_the_promoted_image() -> None:
    docs = _render("--set", "image.digest=sha256:abcdef")
    job = next(doc for doc in docs if doc["kind"] == "Job")
    spec = job["spec"]["template"]["spec"]
    container = spec["containers"][0]
    assert container["image"] == "nodyra@sha256:abcdef"
    database = next(item for item in container["env"] if item["name"] == "DATABASE_URL")
    assert database["value"].startswith("postgresql+asyncpg://")
    assert spec["automountServiceAccountToken"] is False
    assert spec["securityContext"]["runAsNonRoot"] is True


def test_crypto_migrations_receive_the_runtime_key_before_install() -> None:
    docs = _render()
    job = next(doc for doc in docs if doc["kind"] == "Job")
    env = job["spec"]["template"]["spec"]["containers"][0]["env"]
    key_ref = next(item for item in env if item["name"] == "SECRET_KEY")["valueFrom"][
        "secretKeyRef"
    ]
    secret = next(
        doc
        for doc in docs
        if doc["kind"] == "Secret" and doc["metadata"]["name"] == key_ref["name"]
    )
    assert secret["stringData"][key_ref["key"]] == "chart-test-secret-key-at-least-32-bytes"
    annotations = secret["metadata"]["annotations"]
    assert annotations["helm.sh/hook"] == "pre-install,pre-upgrade"
    assert int(annotations["helm.sh/hook-weight"]) < int(
        job["metadata"]["annotations"]["helm.sh/hook-weight"]
    )
    assert "hook-succeeded" not in annotations["helm.sh/hook-delete-policy"]


def test_migrations_use_external_runtime_secret_when_configured() -> None:
    docs = _render("--set", "secret.existingSecret=external-runtime")
    assert not [doc for doc in docs if doc["kind"] == "Secret"]
    job = next(doc for doc in docs if doc["kind"] == "Job")
    env = job["spec"]["template"]["spec"]["containers"][0]["env"]
    key = next(item for item in env if item["name"] == "SECRET_KEY")
    assert key["valueFrom"]["secretKeyRef"] == {"name": "external-runtime", "key": "secret-key"}


def test_dispatch_and_worker_probes_match_the_shipped_runtime() -> None:
    deployments = _deployments(_render())
    api = deployments["release-api"]["spec"]["template"]["spec"]["containers"][0]
    assert (
        next(item["value"] for item in api["env"] if item["name"] == "DISPATCH_ROLE") == "control"
    )
    worker = deployments["release-worker"]["spec"]["template"]["spec"]["containers"][0]
    command = worker["livenessProbe"]["exec"]["command"]
    assert command[:2] == ["python", "-c"]
    assert "app.worker_main" in command[2]
