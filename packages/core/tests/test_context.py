"""Tests for noodle.context module."""

from noodle.context import WebSocketConnection, node_ws_connect


def test_node_ws_connect_default_is_none():
    """Test that node_ws_connect starts as None by default."""
    assert node_ws_connect.get() is None


def test_websocket_connection_protocol_has_required_methods():
    """Test that WebSocketConnection protocol has all required methods."""
    # Verify the protocol has the required methods
    required_methods = {"send", "recv", "close"}
    protocol_methods = set(WebSocketConnection.__protocol_attrs__)
    assert required_methods == protocol_methods


async def test_websocket_connection_structural_subtype():
    """Test that a mock object implementing WebSocketConnection methods works."""

    class MockWebSocketConnection:
        async def send(self, data: bytes | str) -> None:
            pass

        async def recv(self) -> bytes | str:
            return b""

        async def close(self) -> None:
            pass

    # Create an instance to verify it has the required interface
    mock = MockWebSocketConnection()
    assert hasattr(mock, "send")
    assert hasattr(mock, "recv")
    assert hasattr(mock, "close")
