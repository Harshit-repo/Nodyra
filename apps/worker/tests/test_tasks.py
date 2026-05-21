from worker.tasks import ping


def test_ping_runs_synchronously() -> None:
    assert ping() == "pong"
