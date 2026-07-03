from app.config import Settings


def test_code_node_timeout_defaults_to_600():
    assert Settings(_env_file=None).code_node_timeout_seconds == 600.0
