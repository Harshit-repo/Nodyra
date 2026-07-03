"""MQTT subscription polling trigger — fires on messages from MQTT topics."""

from __future__ import annotations

import json
import queue
import threading
import uuid
from typing import Any

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_provider_trigger
from nodyra_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)


def _creds_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def poll_mqtt(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        raise ImportError(
            "mqtt_trigger requires paho-mqtt>=2.0. Install with: pip install 'paho-mqtt>=2.0'"
        )

    params = ctx.params
    creds = _creds_dict(params.get("credentials"))
    topic = str(params.get("topic") or "").strip()
    if not topic:
        raise ValueError("mqtt_trigger: topic is required")

    broker_url = str(creds.get("broker_url") or "localhost")
    try:
        port = int(creds.get("port") or 1883)
    except (ValueError, TypeError):
        port = 1883
    username = str(creds.get("username") or "")
    password = str(creds.get("password") or "")
    use_tls = str(creds.get("tls", "")).lower() in ("true", "1", "yes")
    try:
        qos = int(params.get("qos") or 1)
    except (ValueError, TypeError):
        qos = 1
    client_id = str(params.get("client_id") or "") or f"nodyra-{uuid.uuid4().hex[:8]}"

    msg_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    connected = threading.Event()
    error_holder: list[str] = []

    def on_connect(client: Any, userdata: Any, flags: Any, rc: int, props: Any = None) -> None:
        if rc == 0:
            client.subscribe(topic, qos=qos)
            connected.set()
        else:
            error_holder.append(f"MQTT connect failed rc={rc}")
            connected.set()

    def on_message(client: Any, userdata: Any, msg: Any) -> None:
        try:
            payload_bytes: bytes = msg.payload
            try:
                payload = json.loads(payload_bytes.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = payload_bytes.decode("utf-8", errors="replace")
            msg_queue.put(
                {
                    "topic": msg.topic,
                    "payload": payload,
                    "qos": msg.qos,
                    "retained": msg.retain,
                }
            )
        except Exception:  # noqa: BLE001
            pass

    client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv5)
    if username:
        client.username_pw_set(username, password)
    if use_tls:
        client.tls_set()
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(broker_url, port, keepalive=10)
    client.loop_start()

    try:
        connected.wait(timeout=10)
        if error_holder:
            raise RuntimeError(f"mqtt_trigger: {error_holder[0]}")
        # Collect messages for up to 3 seconds
        import time

        time.sleep(3)
    finally:
        client.loop_stop()
        client.disconnect()

    events: list[dict[str, Any]] = []
    while not msg_queue.empty():
        events.append(msg_queue.get_nowait())

    return ProviderTriggerPollResult(events=events, cursor=ctx.cursor)


_CREDENTIALS_PARAM = OperationParamSpec(
    name="credentials",
    type="credential",
    required=True,
    credential=CredentialSpec(
        type="mqtt",
        key="*",
        label="MQTT credentials",
        fields=["broker_url", "port", "username", "password", "tls"],
        multi=True,
        test_service="mqtt",
    ),
    description="MQTT broker credentials.",
)

MQTT_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="mqtt_trigger",
    name="MQTT",
    provider="mqtt",
    resource="topic",
    event="message",
    description="Start a workflow when a message arrives on an MQTT topic.",
    icon="wifi",
    params=(
        _CREDENTIALS_PARAM,
        OperationParamSpec(
            name="topic",
            required=True,
            description="MQTT topic to subscribe to (supports wildcards + and #).",
        ),
        OperationParamSpec(
            name="qos",
            type="number",
            default=1,
            choices=["0", "1", "2"],
            description="Quality of Service level (0, 1, or 2).",
        ),
        OperationParamSpec(
            name="client_id",
            description="MQTT client ID. Auto-generated if empty.",
        ),
    ),
    requirements=("paho-mqtt>=2.0",),
    poll=poll_mqtt,
    poll_interval_seconds=30,
)

register_provider_trigger(MQTT_TRIGGER_SPEC)
