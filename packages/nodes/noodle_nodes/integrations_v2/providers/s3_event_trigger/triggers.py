"""S3 Event trigger — fires on S3 bucket events via SQS polling."""

from __future__ import annotations

import json
from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)

_MAX_SQS_MESSAGES = 10  # SQS ReceiveMessage max


def _creds_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _parse_s3_event(record: dict[str, Any]) -> dict[str, Any] | None:
    """Extract relevant fields from an S3 event record."""
    event_name = str(record.get("eventName") or "")
    s3 = record.get("s3") or {}
    bucket = (s3.get("bucket") or {}).get("name", "")
    obj = s3.get("object") or {}
    key = obj.get("key", "")
    size = obj.get("size", 0)
    return {
        "event_name": event_name,
        "bucket": bucket,
        "key": key,
        "size": size,
        "region": record.get("awsRegion", ""),
        "time": record.get("eventTime", ""),
    }


def poll_s3_events(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    try:
        import boto3
    except ImportError:
        raise ImportError("s3_event_trigger requires boto3. Install with: pip install boto3")

    params = ctx.params
    creds = _creds_dict(params.get("credentials"))
    queue_url = str(params.get("queue_url") or "").strip()
    if not queue_url:
        raise ValueError("s3_event_trigger: queue_url is required")

    event_types_raw = str(params.get("event_types") or "s3:ObjectCreated:*")
    # S3 event records use "ObjectCreated:Put" (no "s3:" prefix); strip it from the filter
    event_type_prefixes = [
        t.strip().removeprefix("s3:").replace("*", "").rstrip(":")
        for t in event_types_raw.split(",")
        if t.strip()
    ]
    bucket_filter = str(params.get("bucket_filter") or "").strip()
    prefix_filter = str(params.get("prefix_filter") or "").strip()

    access_key = str(creds.get("access_key_id") or "")
    secret_key = str(creds.get("secret_access_key") or "")
    region = str(creds.get("region") or "us-east-1")

    session_kwargs: dict[str, str] = {"region_name": region}
    if access_key:
        session_kwargs["aws_access_key_id"] = access_key
    if secret_key:
        session_kwargs["aws_secret_access_key"] = secret_key

    sqs = boto3.client("sqs", **session_kwargs)

    response = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=_MAX_SQS_MESSAGES,
        WaitTimeSeconds=3,
        AttributeNames=["All"],
    )
    messages = response.get("Messages") or []

    events: list[dict[str, Any]] = []
    receipt_handles_to_delete: list[str] = []

    for sqs_msg in messages:
        receipt_handles_to_delete.append(sqs_msg["ReceiptHandle"])
        try:
            body = json.loads(sqs_msg.get("Body") or "{}")
            # SNS-wrapped S3 notification
            if "Message" in body:
                body = json.loads(body["Message"])
            records = body.get("Records") or []
            for record in records:
                parsed = _parse_s3_event(record)
                if parsed is None:
                    continue
                if event_type_prefixes and not any(
                    parsed["event_name"].startswith(p) for p in event_type_prefixes
                ):
                    continue
                if bucket_filter and parsed["bucket"] != bucket_filter:
                    continue
                if prefix_filter and not parsed["key"].startswith(prefix_filter):
                    continue
                events.append(parsed)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    # Delete processed messages from the queue
    if receipt_handles_to_delete:
        for rh in receipt_handles_to_delete:
            try:
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=rh)
            except Exception:  # noqa: BLE001
                pass

    return ProviderTriggerPollResult(events=events, cursor=ctx.cursor)


_CREDENTIALS_PARAM = OperationParamSpec(
    name="credentials",
    type="credential",
    required=True,
    credential=CredentialSpec(
        type="aws",
        key="*",
        label="AWS credentials",
        fields=["access_key_id", "secret_access_key", "region"],
        multi=True,
        test_service="aws",
    ),
    description="AWS credentials for SQS access.",
)

S3_EVENT_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="s3_event_trigger",
    name="S3 Event",
    provider="aws",
    resource="s3_bucket",
    event="s3_event",
    description=(
        "Start a workflow when an S3 bucket event occurs (object created, deleted, etc.) "
        "via an SQS queue that receives S3 Event Notifications."
    ),
    icon="brand:aws",
    params=(
        _CREDENTIALS_PARAM,
        OperationParamSpec(
            name="queue_url",
            required=True,
            placeholder="https://sqs.us-east-1.amazonaws.com/123456/my-queue",
            description="SQS queue URL that receives S3 event notifications.",
        ),
        OperationParamSpec(
            name="event_types",
            default="s3:ObjectCreated:*",
            description="Comma-separated S3 event types to process (e.g. 's3:ObjectCreated:*, s3:ObjectRemoved:*').",
        ),
        OperationParamSpec(
            name="bucket_filter",
            group="Filters",
            description="Only process events from this S3 bucket (leave empty for all buckets).",
        ),
        OperationParamSpec(
            name="prefix_filter",
            group="Filters",
            description="Only process objects with this key prefix (leave empty for all objects).",
        ),
    ),
    requirements=("boto3>=1.34",),
    poll=poll_s3_events,
    poll_interval_seconds=30,
)

register_provider_trigger(S3_EVENT_TRIGGER_SPEC)
