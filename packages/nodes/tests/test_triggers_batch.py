"""Batch tests for Tasks 15-19: WebSocket, Kafka, MQTT, Postgres LISTEN, S3 Event triggers."""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from noodle_nodes.integrations_v2.registry import is_registered_provider_trigger
from noodle_nodes.integrations_v2.specs import ProviderTriggerPollContext, ProviderTriggerRequest


def _ctx(params: dict, cursor: dict | None = None) -> ProviderTriggerPollContext:
    return ProviderTriggerPollContext(params=params, cursor=cursor or {})


# ---------------------------------------------------------------------------
# Task 15: WebSocket Trigger
# ---------------------------------------------------------------------------


class TestWebSocketTrigger:
    def test_trigger_registered(self):
        assert is_registered_provider_trigger("websocket_trigger")

    def test_activate_returns_subscription(self):
        from noodle_nodes.integrations_v2.providers.websocket_trigger.triggers import (
            activate_websocket,
        )
        from noodle_nodes.integrations_v2.specs import ProviderTriggerActivationContext

        ctx = ProviderTriggerActivationContext(
            workflow_id="wf1",
            workflow_version_id="wfv1",
            node_id="n1",
            callback_url="https://noodle.test/cb",
            params={"path": "my-ws"},
        )
        sub = activate_websocket(ctx)
        assert "my-ws" in sub.external_id

    def test_handle_json_message(self):
        from noodle_nodes.integrations_v2.providers.websocket_trigger.triggers import (
            handle_websocket_event,
        )

        payload = json.dumps({"action": "ping"}).encode()
        req = ProviderTriggerRequest(
            headers={"x-noodle-ws-event": "message", "x-noodle-client-id": "c1"},
            body=payload,
            raw_body=payload,
            query={},
        )
        event = handle_websocket_event(req, {"message_format": "json"})
        assert event.payload["action"] == "ping"
        assert event.payload["event"] == "message"
        assert event.payload["client_id"] == "c1"

    def test_handle_text_message(self):
        from noodle_nodes.integrations_v2.providers.websocket_trigger.triggers import (
            handle_websocket_event,
        )

        req = ProviderTriggerRequest(
            headers={},
            body="hello world",
            raw_body=b"hello world",
            query={},
        )
        event = handle_websocket_event(req, {"message_format": "text"})
        assert event.payload["message"] == "hello world"

    def test_oversized_message_raises(self):
        from noodle_nodes.integrations_v2.providers.websocket_trigger.triggers import (
            handle_websocket_event,
        )

        body = b"x" * 1000
        req = ProviderTriggerRequest(
            headers={},
            body=body,
            raw_body=body,
            query={},
        )
        with pytest.raises(ValueError, match="exceeds max"):
            handle_websocket_event(req, {"message_format": "text", "max_message_size": 100})


# ---------------------------------------------------------------------------
# Task 16: Kafka Trigger
# ---------------------------------------------------------------------------


class TestKafkaTrigger:
    def test_trigger_registered(self):
        assert is_registered_provider_trigger("kafka_trigger")

    def test_raises_without_topic(self):
        from noodle_nodes.integrations_v2.providers.kafka_trigger.triggers import poll_kafka

        # confluent_kafka not installed — will raise ImportError first
        ctx = _ctx({"credentials": {}, "topic": ""})
        with pytest.raises((ValueError, ImportError)):
            poll_kafka(ctx)

    def test_poll_with_mock_consumer(self):
        from noodle_nodes.integrations_v2.providers.kafka_trigger.triggers import poll_kafka

        mock_msg = MagicMock()
        mock_msg.error.return_value = None
        mock_msg.value.return_value = b'{"event": "order_placed"}'
        mock_msg.topic.return_value = "orders"
        mock_msg.partition.return_value = 0
        mock_msg.offset.return_value = 42
        mock_msg.timestamp.return_value = (1, 1_700_000_000_000)
        mock_msg.key.return_value = b"order-1"

        mock_consumer = MagicMock()
        mock_consumer.consume.return_value = [mock_msg]

        mock_confluent = MagicMock()
        mock_confluent.Consumer.return_value = mock_consumer
        mock_confluent.KafkaError._PARTITION_EOF = -191

        with patch.dict(sys.modules, {"confluent_kafka": mock_confluent}):
            ctx = _ctx(
                {
                    "credentials": {"bootstrap_servers": "localhost:9092"},
                    "topic": "orders",
                }
            )
            result = poll_kafka(ctx)

        assert len(result.events) == 1
        assert result.events[0]["value"]["event"] == "order_placed"
        assert result.events[0]["topic"] == "orders"
        mock_consumer.commit.assert_called_once()
        mock_consumer.close.assert_called_once()


# ---------------------------------------------------------------------------
# Task 17: MQTT Trigger
# ---------------------------------------------------------------------------


class TestMqttTrigger:
    def test_trigger_registered(self):
        assert is_registered_provider_trigger("mqtt_trigger")

    def test_raises_without_topic(self):
        from noodle_nodes.integrations_v2.providers.mqtt_trigger.triggers import poll_mqtt

        mock_mqtt_module = MagicMock()
        with patch.dict(sys.modules, {"paho": mock_mqtt_module, "paho.mqtt": mock_mqtt_module, "paho.mqtt.client": mock_mqtt_module}):
            ctx = _ctx({"credentials": {"broker_url": "localhost"}, "topic": ""})
            with pytest.raises(ValueError, match="required"):
                poll_mqtt(ctx)

    def test_poll_collects_messages(self):
        from noodle_nodes.integrations_v2.providers.mqtt_trigger.triggers import poll_mqtt

        received_payloads: list[dict] = []

        class FakeMqttClient:
            MQTTv5 = 5

            def __init__(self, client_id="", protocol=5):
                self._on_connect = None
                self._on_message = None

            def username_pw_set(self, u, p):
                pass

            @property
            def on_connect(self):
                return self._on_connect

            @on_connect.setter
            def on_connect(self, fn):
                self._on_connect = fn

            @property
            def on_message(self):
                return self._on_message

            @on_message.setter
            def on_message(self, fn):
                self._on_message = fn

            def connect(self, host, port, keepalive=10):
                # Immediately fire on_connect
                if self._on_connect:
                    self._on_connect(self, None, None, 0)

            def loop_start(self):
                # Simulate one message arriving
                if self._on_message:
                    fake_msg = MagicMock()
                    fake_msg.topic = "sensors/temp"
                    fake_msg.payload = b'{"temp": 22.5}'
                    fake_msg.qos = 1
                    fake_msg.retain = False
                    self._on_message(self, None, fake_msg)

            def loop_stop(self):
                pass

            def disconnect(self):
                pass

            def subscribe(self, topic, qos=1):
                pass

        mock_paho = MagicMock()
        mock_paho.mqtt = MagicMock()
        mock_paho.mqtt.client = MagicMock()
        mock_paho.mqtt.client.Client = FakeMqttClient
        mock_paho.mqtt.client.MQTTv5 = 5

        with patch.dict(sys.modules, {"paho": mock_paho, "paho.mqtt": mock_paho.mqtt, "paho.mqtt.client": mock_paho.mqtt.client}):
            with patch("time.sleep"):
                ctx = _ctx(
                    {"credentials": {"broker_url": "localhost"}, "topic": "sensors/temp"}
                )
                result = poll_mqtt(ctx)

        assert len(result.events) == 1
        assert result.events[0]["topic"] == "sensors/temp"
        assert result.events[0]["payload"]["temp"] == 22.5


# ---------------------------------------------------------------------------
# Task 18: Postgres LISTEN Trigger
# ---------------------------------------------------------------------------


class TestPostgresListenTrigger:
    def test_trigger_registered(self):
        assert is_registered_provider_trigger("postgres_listen_trigger")

    def test_raises_on_import_error(self):
        from noodle_nodes.integrations_v2.providers.postgres_listen_trigger.triggers import (
            poll_postgres_listen,
        )

        with patch.dict(sys.modules, {"psycopg": None}):
            ctx = _ctx({"credentials": {"dsn": "postgres://localhost/test"}, "channel": "events"})
            with pytest.raises(ImportError, match="psycopg"):
                poll_postgres_listen(ctx)

    def test_poll_collects_notifications(self):
        from noodle_nodes.integrations_v2.providers.postgres_listen_trigger.triggers import (
            poll_postgres_listen,
        )

        mock_notification = MagicMock()
        mock_notification.channel = "noodle_events"
        mock_notification.pid = 12345
        mock_notification.payload = json.dumps({"type": "order_created", "id": 1})

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.notifies.return_value = iter([mock_notification])

        mock_psycopg = MagicMock()
        mock_psycopg.connect.return_value = mock_conn
        mock_psycopg.sql = MagicMock()
        mock_psycopg.sql.SQL.return_value = MagicMock(format=MagicMock(return_value="LISTEN events"))
        mock_psycopg.sql.Identifier.return_value = "events"

        with patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            ctx = _ctx(
                {"credentials": {"dsn": "postgres://localhost/db"}, "channel": "noodle_events"}
            )
            result = poll_postgres_listen(ctx)

        assert len(result.events) == 1
        assert result.events[0]["type"] == "order_created"
        assert result.events[0]["channel"] == "noodle_events"


# ---------------------------------------------------------------------------
# Task 19: S3 Event Trigger
# ---------------------------------------------------------------------------


class TestS3EventTrigger:
    def test_trigger_registered(self):
        assert is_registered_provider_trigger("s3_event_trigger")

    def test_raises_without_queue_url(self):
        from noodle_nodes.integrations_v2.providers.s3_event_trigger.triggers import poll_s3_events

        mock_boto3 = MagicMock()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            ctx = _ctx({"credentials": {}, "queue_url": ""})
            with pytest.raises(ValueError, match="queue_url"):
                poll_s3_events(ctx)

    def test_poll_parses_s3_event(self):
        from noodle_nodes.integrations_v2.providers.s3_event_trigger.triggers import poll_s3_events

        s3_record = {
            "eventName": "ObjectCreated:Put",
            "awsRegion": "us-east-1",
            "eventTime": "2026-06-19T10:00:00Z",
            "s3": {
                "bucket": {"name": "my-bucket"},
                "object": {"key": "data/file.csv", "size": 1234},
            },
        }
        sqs_body = json.dumps({"Records": [s3_record]})

        mock_sqs = MagicMock()
        mock_sqs.receive_message.return_value = {
            "Messages": [
                {"Body": sqs_body, "ReceiptHandle": "rh-1"}
            ]
        }

        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_sqs

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            ctx = _ctx(
                {
                    "credentials": {"access_key_id": "k", "secret_access_key": "s", "region": "us-east-1"},
                    "queue_url": "https://sqs.us-east-1.amazonaws.com/123/q",
                }
            )
            result = poll_s3_events(ctx)

        assert len(result.events) == 1
        evt = result.events[0]
        assert evt["bucket"] == "my-bucket"
        assert evt["key"] == "data/file.csv"
        assert evt["event_name"] == "ObjectCreated:Put"
        mock_sqs.delete_message.assert_called_once()

    def test_bucket_filter_excludes_other_buckets(self):
        from noodle_nodes.integrations_v2.providers.s3_event_trigger.triggers import poll_s3_events

        s3_record = {
            "eventName": "ObjectCreated:Put",
            "awsRegion": "us-east-1",
            "eventTime": "2026-06-19T10:00:00Z",
            "s3": {
                "bucket": {"name": "other-bucket"},
                "object": {"key": "file.csv", "size": 100},
            },
        }
        sqs_body = json.dumps({"Records": [s3_record]})
        mock_sqs = MagicMock()
        mock_sqs.receive_message.return_value = {
            "Messages": [{"Body": sqs_body, "ReceiptHandle": "rh-1"}]
        }
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_sqs

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            ctx = _ctx(
                {
                    "credentials": {},
                    "queue_url": "https://sqs.us-east-1.amazonaws.com/123/q",
                    "bucket_filter": "my-bucket",  # different from record's bucket
                }
            )
            result = poll_s3_events(ctx)

        assert result.events == []
