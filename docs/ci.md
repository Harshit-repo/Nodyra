# Continuous Integration

Python CI uses `pytest-rerunfailures` with `--reruns 1 --reruns-delay 2` on
the main test lanes. A test that passes only after retry keeps the build green
but is reported as a GitHub Actions warning and in the job summary by
`scripts/report_flaky_tests.py`.

Treat flake warnings as actionable debt: fix or quarantine the test before the
same retry shows up repeatedly. The retry exists to distinguish rare scheduler
or network timing from deterministic regressions, not to hide unstable tests.
