import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "report_flaky_tests.py"
    spec = importlib.util.spec_from_file_location("report_flaky_tests", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_collects_rerun_lines_from_pytest_log(tmp_path: Path) -> None:
    reporter = _module()
    log = tmp_path / "pytest.log"
    log.write_text(
        "\n".join(
            [
                "==================== rerun test summary info ====================",
                "RERUN apps/api/tests/test_runs.py::test_eventually_passes",
                "RERUN packages/core/tests/test_engine.py::test_transient",
            ]
        ),
        encoding="utf-8",
    )

    assert reporter.collect_from_log(log) == [
        "apps/api/tests/test_runs.py::test_eventually_passes",
        "packages/core/tests/test_engine.py::test_transient",
    ]


def test_emit_report_writes_github_warning_and_summary(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    reporter = _module()
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert reporter.main(["missing.log"]) == 0
    assert "No tests required a rerun" in summary.read_text(encoding="utf-8")

    summary.write_text("", encoding="utf-8")
    assert reporter.emit_report(["b::test_two", "a::test_one", "a::test_one"], summary_path=str(summary)) == 0

    out = capsys.readouterr().out
    assert "::warning title=Flaky test passed after retry::a::test_one" in out
    assert "`a::test_one`" in summary.read_text(encoding="utf-8")
