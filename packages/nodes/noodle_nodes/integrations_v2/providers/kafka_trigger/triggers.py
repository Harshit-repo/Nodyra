"""Kafka consumer polling trigger — fires on messages from Kafka topics."""

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

_MAX_CURSOR_OFFSETS = 200


def _creds_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _decode_value(raw: bytes | None, fmt: str) -> Any:
    if raw is None:
        return None
    if fmt == "json":
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return raw.decode("utf-8", errors="replace")
    if fmt == "text":
        return raw.decode("utf-8", errors="replace")
    return raw.hex()


def poll_kafka(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    try:
        from confluent_kafka import Consumer, KafkaError
    except ImportError as exc:
        raise ImportError(
            "kafka_trigger requires confluent-kafka>=2.0. "
            "Install with: pip install 'confluent-kafka>=2.0'"
        ) from exc

    params = ctx.params
    creds = _creds_dict(params.get("credentials"))
    topic = str(params.get("topic") or "").strip()
    if not topic:
        raise ValueError("kafka_trigger: topic is required")

    bootstrap_servers = str(creds.get("bootstrap_servers") or "localhost:9092")
    security_protocol = str(creds.get("security_protocol") or "PLAINTEXT")
    sasl_mechanism = str(creds.get("sasl_mechanism") or "")
    sasl_username = str(creds.get("sasl_username") or "")
    sasl_password = str(creds.get("sasl_password") or "")
    consumer_group = str(params.get("consumer_group") or "noodle")
    auto_offset_reset = str(params.get("auto_offset_reset") or "latest")
    try:
        max_records = int(params.get("max_poll_records") or 10)
    except (ValueError, TypeError):
        max_records = 10
    value_format = str(params.get("value_format") or "json").lower()
    include_metadata = str(params.get("include_metadata", "true")).lower() not in (
        "false",
        "0",
        "no",
    )

    conf: dict[str, Any] = {
        "bootstrap.servers": bootstrap_servers,
        "group.id": consumer_group,
        "auto.offset.reset": auto_offset_reset,
        "enable.auto.commit": False,
        "security.protocol": security_protocol,
    }
    if sasl_mechanism:
        conf["sasl.mechanism"] = sasl_mechanism
    if sasl_username:
        conf["sasl.username"] = sasl_username
    if sasl_password:
        conf["sasl.password"] = sasl_password

    consumer = Consumer(conf)
    events: list[dict[str, Any]] = []
    try:
        consumer.subscribe([topic])
        msgs = consumer.consume(num_messages=max_records, timeout=5.0)
        for msg in msgs:
            if msg.error():
                err = msg.error()
                if err.code() == KafkaError._PARTITION_EOF:
                    continue
                continue
            value = _decode_value(msg.value(), value_format)
            event: dict[str, Any] = {"value": value}
            if include_metadata:
                event.update(
                    {
                        "topic": msg.topic(),
                        "partition": msg.partition(),
                        "offset": msg.offset(),
                        "timestamp": msg.timestamp()[1] if msg.timestamp()[0] != 0 else None,
                        "key": msg.key().decode("utf-8", errors="replace") if msg.key() else None,
                    }
                )
            events.append(event)
        if msgs:
            consumer.commit(asynchronous=False)
    finally:
        consumer.close()

    return ProviderTriggerPollResult(events=events, cursor=ctx.cursor)


_CREDENTIALS_PARAM = OperationParamSpec(
    name="credentials",
    type="credential",
    required=True,
    credential=CredentialSpec(
        type="kafka",
        key="*",
        label="Kafka credentials",
        fields=[
            "bootstrap_servers",
            "security_protocol",
            "sasl_mechanism",
            "sasl_username",
            "sasl_password",
        ],
        multi=True,
        test_service="kafka",
    ),
    description="Kafka connection credentials.",
)

KAFKA_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="kafka_trigger",
    name="Kafka",
    provider="kafka",
    resource="topic",
    event="message",
    description="Start a workflow when messages arrive on a Kafka topic.",
    icon="brand:kafka",
    params=(
        _CREDENTIALS_PARAM,
        OperationParamSpec(
            name="topic",
            required=True,
            description="Kafka topic to consume from.",
        ),
        OperationParamSpec(
            name="consumer_group",
            default="noodle",
            description="Kafka consumer group ID.",
        ),
        OperationParamSpec(
            name="auto_offset_reset",
            choices=["latest", "earliest"],
            default="latest",
            description="Where to start consuming when no offset is committed.",
        ),
        OperationParamSpec(
            name="max_poll_records",
            type="number",
            default=10,
            description="Maximum records to fetch per poll.",
        ),
        OperationParamSpec(
            name="value_format",
            choices=["json", "text", "binary"],
            default="json",
            description="Message value format.",
        ),
        OperationParamSpec(
            name="include_metadata",
            type="boolean",
            default=True,
            description="Include Kafka metadata (topic, partition, offset, timestamp) in output.",
        ),
    ),
    requirements=("confluent-kafka>=2.0",),
    poll=poll_kafka,
    poll_interval_seconds=5,
)

register_provider_trigger(KAFKA_TRIGGER_SPEC)
