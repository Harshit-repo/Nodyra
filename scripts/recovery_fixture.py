"""Seed and verify a redacted PostgreSQL + object-storage restore fixture.

This script is intentionally deployment-agnostic. CI uses it around pg_dump /
pg_restore and an S3 bucket copy; operators can use the same three commands in
their own backup pipeline. The fixture proves four things after restore:

* workflow and terminal run rows survived;
* envelope-encrypted credentials remain decryptable with the restored key
  material and deployment secret;
* artifact metadata and object bytes both survived; and
* the restored bytes match the recorded SHA-256 checksum.

No plaintext credential is written to the manifest or evidence report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Artifact, Credential, Run, Workflow, WorkflowVersion  # noqa: E402
from app.services import org_keys  # noqa: E402
from app.tenancy import DEFAULT_ORG_ID, run_as_system  # noqa: E402

FIXTURE_NAMESPACE = uuid.UUID("fe0da142-c293-4f9f-bc63-b981b7308db4")
WORKFLOW_ID = uuid.uuid5(FIXTURE_NAMESPACE, "workflow").hex
RUN_ID = uuid.uuid5(FIXTURE_NAMESPACE, "run").hex
CREDENTIAL_ID = uuid.uuid5(FIXTURE_NAMESPACE, "credential").hex
ARTIFACT_ID = uuid.uuid5(FIXTURE_NAMESPACE, "artifact").hex
FIXTURE_NAME = "Nodyra Recovery Drill Fixture"
FIXTURE_SECRET = {"api_key": "recovery_fixture_not_a_real_secret", "version": 1}
ARTIFACT_BYTES = b"nodyra-recovery-fixture-v1\n"
ARTIFACT_CHECKSUM = hashlib.sha256(ARTIFACT_BYTES).hexdigest()
ARTIFACT_KEY = f"{DEFAULT_ORG_ID}/runs/{RUN_ID}/recovery-fixture.txt"
FIXTURE_GRAPH = {
    "nodes": [
        {
            "id": "recovery-trigger",
            "type": "manual_trigger",
            "params": {"data": {"recovery_fixture": True}},
            "position": {"x": 0, "y": 0},
        }
    ],
    "edges": [],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("seed", "copy-bucket", "verify"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--evidence", default="")
    parser.add_argument("--source-bucket", default="")
    parser.add_argument("--target-bucket", default="")
    parser.add_argument("--restore-seconds", type=float, default=0.0)
    parser.add_argument("--max-rto-seconds", type=float, default=3600.0)
    return parser.parse_args()


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.artifact_s3_endpoint or None,
        region_name=settings.artifact_s3_region or None,
    )


async def seed_fixture(manifest_path: str) -> dict[str, Any]:
    started_at = time.monotonic()
    client = _s3_client()
    bucket = settings.artifact_s3_bucket
    client.put_object(
        Bucket=bucket,
        Key=ARTIFACT_KEY,
        Body=ARTIFACT_BYTES,
        ContentType="text/plain",
        Metadata={"sha256": ARTIFACT_CHECKSUM, "fixture": "recovery-v1"},
    )
    try:
        with run_as_system():
            async with SessionLocal() as session:
                workflow = await session.get(Workflow, WORKFLOW_ID)
                if workflow is None:
                    workflow = Workflow(
                        id=WORKFLOW_ID,
                        org_id=DEFAULT_ORG_ID,
                        name=FIXTURE_NAME,
                        active=False,
                        draft_graph=FIXTURE_GRAPH,
                        published_version=1,
                    )
                    session.add(workflow)
                else:
                    workflow.name = FIXTURE_NAME
                    workflow.draft_graph = FIXTURE_GRAPH

                workflow_version = await session.scalar(
                    select(WorkflowVersion).where(
                        WorkflowVersion.workflow_id == WORKFLOW_ID,
                        WorkflowVersion.version == 1,
                    )
                )
                if workflow_version is None:
                    session.add(
                        WorkflowVersion(
                            workflow_id=WORKFLOW_ID,
                            version=1,
                            graph=FIXTURE_GRAPH,
                            notes="Recovery certification fixture",
                        )
                    )
                else:
                    workflow_version.graph = FIXTURE_GRAPH

                run = await session.get(Run, RUN_ID)
                if run is None:
                    run = Run(
                        id=RUN_ID,
                        org_id=DEFAULT_ORG_ID,
                        workflow_id=WORKFLOW_ID,
                        workflow_version=1,
                        status="success",
                        mode="manual",
                    )
                    session.add(run)
                else:
                    run.status = "success"

                encrypted_data, encrypted_dek = await org_keys.encrypt_credential_for(
                    DEFAULT_ORG_ID, FIXTURE_SECRET, session
                )
                credential = await session.get(Credential, CREDENTIAL_ID)
                if credential is None:
                    credential = Credential(
                        id=CREDENTIAL_ID,
                        org_id=DEFAULT_ORG_ID,
                        name=FIXTURE_NAME,
                        type="generic",
                        scope="global",
                        description="Synthetic credential used only by the restore drill.",
                        encrypted_data=encrypted_data,
                        encrypted_dek=encrypted_dek,
                    )
                    session.add(credential)
                else:
                    credential.encrypted_data = encrypted_data
                    credential.encrypted_dek = encrypted_dek

                artifact = await session.get(Artifact, ARTIFACT_ID)
                if artifact is None:
                    artifact = Artifact(
                        id=ARTIFACT_ID,
                        org_id=DEFAULT_ORG_ID,
                        run_id=RUN_ID,
                        node_id="recovery-trigger",
                        name="recovery-fixture.txt",
                        kind="text",
                        content_type="text/plain",
                        size_bytes=len(ARTIFACT_BYTES),
                        checksum_sha256=ARTIFACT_CHECKSUM,
                        storage_backend="s3",
                        storage_key=ARTIFACT_KEY,
                        artifact_metadata={"fixture": "recovery-v1"},
                    )
                    session.add(artifact)
                else:
                    artifact.checksum_sha256 = ARTIFACT_CHECKSUM
                    artifact.storage_backend = "s3"
                    artifact.storage_key = ARTIFACT_KEY
                    artifact.size_bytes = len(ARTIFACT_BYTES)
                await session.commit()
    except BaseException:
        client.delete_object(Bucket=bucket, Key=ARTIFACT_KEY)
        raise

    manifest = {
        "artifact": {
            "bucket": bucket,
            "checksum_sha256": ARTIFACT_CHECKSUM,
            "id": ARTIFACT_ID,
            "key": ARTIFACT_KEY,
            "size_bytes": len(ARTIFACT_BYTES),
        },
        "credential_id": CREDENTIAL_ID,
        "fixture_version": 1,
        "run_id": RUN_ID,
        "seed_duration_seconds": round(time.monotonic() - started_at, 3),
        "seeded_at": datetime.now(UTC).isoformat(),
        "workflow_id": WORKFLOW_ID,
    }
    _write_json(manifest_path, manifest)
    return manifest


def _empty_bucket(client, bucket: str) -> None:
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if objects:
            client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})


def copy_bucket(manifest_path: str, source_bucket: str, target_bucket: str) -> dict[str, Any]:
    if not source_bucket or not target_bucket or source_bucket == target_bucket:
        raise ValueError("copy-bucket requires distinct --source-bucket and --target-bucket")
    started_at = time.monotonic()
    client = _s3_client()
    try:
        client.create_bucket(Bucket=target_bucket)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
            raise
    _empty_bucket(client, target_bucket)
    copied = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=source_bucket):
        for item in page.get("Contents", []):
            key = str(item["Key"])
            client.copy_object(
                Bucket=target_bucket,
                Key=key,
                CopySource={"Bucket": source_bucket, "Key": key},
                MetadataDirective="COPY",
            )
            copied += 1
    if copied == 0:
        raise RuntimeError("source artifact bucket was empty; refusing false restore evidence")
    manifest = _read_json(manifest_path)
    manifest["object_backup"] = {
        "copy_duration_seconds": round(time.monotonic() - started_at, 3),
        "object_count": copied,
        "source_bucket": source_bucket,
        "target_bucket": target_bucket,
    }
    _write_json(manifest_path, manifest)
    return manifest


async def verify_fixture(
    manifest_path: str,
    evidence_path: str,
    restore_seconds: float,
    max_rto_seconds: float,
) -> dict[str, Any]:
    started_at = time.monotonic()
    manifest = _read_json(manifest_path)
    checks: dict[str, bool] = {}
    with run_as_system():
        async with SessionLocal() as session:
            workflow = await session.get(Workflow, manifest["workflow_id"])
            run = await session.get(Run, manifest["run_id"])
            credential = await session.get(Credential, manifest["credential_id"])
            artifact = await session.get(Artifact, manifest["artifact"]["id"])
            checks["workflow"] = bool(
                workflow
                and workflow.draft_graph
                and workflow.draft_graph.get("nodes", [{}])[0]
                .get("params", {})
                .get("data", {})
                .get("recovery_fixture")
                is True
            )
            checks["terminal_run"] = bool(run and run.status == "success")
            # Credential reads have a legacy master-key fallback. Verify the
            # restored organization key too so that fallback cannot hide a
            # migration run with a different deployment encryption secret.
            org_keys.invalidate_kek_cache(DEFAULT_ORG_ID)
            restored_keys = await org_keys.batch_get_org_keks([DEFAULT_ORG_ID], session)
            checks["organization_key_decryptable"] = bool(restored_keys.get(DEFAULT_ORG_ID))
            checks["artifact_metadata"] = bool(
                artifact
                and artifact.checksum_sha256 == manifest["artifact"]["checksum_sha256"]
                and artifact.storage_key == manifest["artifact"]["key"]
            )
            if credential is None:
                checks["credential_decryptable"] = False
            else:
                plaintext = await org_keys.decrypt_credential_for(credential, session, strict=True)
                checks["credential_decryptable"] = plaintext == FIXTURE_SECRET

    client = _s3_client()
    restored = client.get_object(
        Bucket=settings.artifact_s3_bucket,
        Key=manifest["artifact"]["key"],
    )["Body"].read()
    checks["artifact_bytes"] = (
        hashlib.sha256(restored).hexdigest() == manifest["artifact"]["checksum_sha256"]
        and len(restored) == manifest["artifact"]["size_bytes"]
    )
    checks["rto_budget"] = restore_seconds <= max_rto_seconds
    failures = sorted(name for name, passed in checks.items() if not passed)
    evidence = {
        "checks": checks,
        "fixture_snapshot_age_seconds": round(
            max(
                0.0,
                (datetime.now(UTC) - datetime.fromisoformat(manifest["seeded_at"])).total_seconds(),
            ),
            3,
        ),
        "object_count_restored": manifest.get("object_backup", {}).get("object_count"),
        "restore_seconds": round(restore_seconds, 3),
        "verification_duration_seconds": round(time.monotonic() - started_at, 3),
        "verified_at": datetime.now(UTC).isoformat(),
    }
    _write_json(evidence_path or manifest_path + ".evidence.json", evidence)
    if failures:
        raise RuntimeError("restore verification failed: " + ", ".join(failures))
    return evidence


async def main() -> int:
    args = parse_args()
    if args.command == "seed":
        result = await seed_fixture(args.manifest)
    elif args.command == "copy-bucket":
        result = copy_bucket(args.manifest, args.source_bucket, args.target_bucket)
    else:
        result = await verify_fixture(
            args.manifest,
            args.evidence,
            args.restore_seconds,
            args.max_rto_seconds,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
