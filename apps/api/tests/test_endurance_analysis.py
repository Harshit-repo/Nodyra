"""Leak detection for the endurance harness.

The harness itself needs a live stack, but its judgement does not — and the
judgement is the part with logic worth being wrong about. A detector that cries
leak on a healthy process gets disabled within a week; one that misses a real
leak is worse than not having it.

These pin both directions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "endurance_test",
    Path(__file__).resolve().parents[3] / "scripts" / "endurance_test.py",
)
endurance = importlib.util.module_from_spec(_SPEC)
sys.modules["endurance_test"] = endurance
_SPEC.loader.exec_module(endurance)

MIB = 1024 * 1024
RSS = "nodyra_process_resident_bytes"
FDS = "nodyra_process_open_fds"


def _samples(**series: list[float]) -> list:
    """Build a sample series from per-gauge value lists."""
    length = len(next(iter(series.values())))
    return [
        endurance.Sample(at=float(i), gauges={name: values[i] for name, values in series.items()})
        for i in range(length)
    ]


# ── Parsing ────────────────────────────────────────────────────────────────


def test_gauges_are_parsed_from_a_real_exposition():
    """Parsed from the same text the /metrics endpoint actually serves,
    comments and all."""
    from app.services.metrics import get_metrics_text

    parsed = endurance.parse_metrics(get_metrics_text())
    assert RSS in parsed, parsed
    assert parsed[RSS] > 0
    assert parsed["nodyra_process_threads"] >= 1


def test_comments_and_untracked_metrics_are_ignored():
    text = "\n".join(
        [
            "# HELP nodyra_process_resident_bytes Resident set size.",
            "# TYPE nodyra_process_resident_bytes gauge",
            f"{RSS} 12345.0",
            "nodyra_http_requests_total 99",
            'nodyra_process_info{role="worker"} 1',
        ]
    )
    parsed = endurance.parse_metrics(text)
    assert parsed == {RSS: 12345.0}


def test_a_malformed_value_is_skipped_not_fatal():
    parsed = endurance.parse_metrics(f"{RSS} not-a-number\n{FDS} 7")
    assert parsed == {FDS: 7.0}


# ── Detection ──────────────────────────────────────────────────────────────


def test_a_steady_process_reports_no_leak():
    steady = [200 * MIB + (i % 3) * MIB for i in range(20)]
    result = endurance.analyse_samples(_samples(**{RSS: steady}))
    assert result["leaks"] == []
    assert result["gauges"][RSS]["samples"] == 20


def test_monotonic_growth_is_reported():
    """The signature of a real leak: tracks elapsed time, not load."""
    leaking = [100 * MIB + i * 20 * MIB for i in range(20)]
    result = endurance.analyse_samples(_samples(**{RSS: leaking}))
    assert len(result["leaks"]) == 1
    assert RSS in result["leaks"][0]
    assert result["gauges"][RSS]["growth"] > 0


def test_startup_allocation_alone_is_not_a_leak():
    """Every process allocates during warm-up. Comparing raw first-to-last
    would flag every healthy service; comparing outer thirds does not."""
    warming = [40 * MIB, 90 * MIB, 180 * MIB] + [200 * MIB] * 17
    assert endurance.analyse_samples(_samples(**{RSS: warming}))["leaks"] == []


def test_a_single_spike_is_not_a_leak():
    """One large workflow should not condemn the process."""
    spiky = [200 * MIB] * 9 + [900 * MIB] + [200 * MIB] * 10
    result = endurance.analyse_samples(_samples(**{RSS: spiky}))
    assert result["leaks"] == []
    # The peak is still recorded — worth seeing, just not a leak.
    assert result["gauges"][RSS]["peak"] == 900 * MIB


def test_a_small_absolute_rise_on_a_tiny_process_is_not_a_leak():
    """2 MiB to 4 MiB doubles the ratio and means nothing."""
    tiny = [2 * MIB] * 10 + [4 * MIB] * 10
    assert endurance.analyse_samples(_samples(**{RSS: tiny}))["leaks"] == []


def test_both_thresholds_must_be_cleared():
    """Large absolute growth that is a small fraction of the total is load,
    not a leak."""
    big = [4000 * MIB] * 10 + [4200 * MIB] * 10  # +200 MiB, only 1.05x
    assert endurance.analyse_samples(_samples(**{RSS: big}))["leaks"] == []


def test_descriptor_leaks_are_caught_independently():
    """Memory can be flat while descriptors climb — a leaked socket per run."""
    result = endurance.analyse_samples(
        _samples(
            **{
                RSS: [200 * MIB] * 20,
                FDS: [100.0 + i * 40 for i in range(20)],
            }
        )
    )
    assert len(result["leaks"]) == 1
    assert FDS in result["leaks"][0]


def test_every_tracked_gauge_is_reported_even_when_healthy():
    """The report is evidence, not just an alarm — a clean run should show the
    numbers it judged."""
    result = endurance.analyse_samples(
        _samples(**{RSS: [200 * MIB] * 12, FDS: [90.0] * 12})
    )
    assert set(result["gauges"]) == {RSS, FDS}
    for stats in result["gauges"].values():
        assert {"baseline", "final", "peak", "growth", "growth_ratio", "samples"} <= set(stats)


@pytest.mark.parametrize("count", [0, 1])
def test_too_few_samples_is_reported_rather_than_guessed(count):
    """A short run must not be silently reported as healthy."""
    result = endurance.analyse_samples(_samples(**{RSS: [200 * MIB] * count}) if count else [])
    assert result["leaks"] == ["not enough samples to judge drift"]


def test_a_stricter_ratio_catches_slower_growth():
    """A steady climb of 1.4x is under the default bar but over a stricter one."""
    # Tuned to sit between the two ratios while still clearing the 64 MiB
    # absolute floor: the medians of the outer thirds are 174.5 and 244.5 MiB,
    # a 1.40x rise of 70 MiB.
    slow = [(162 + i * 5) * MIB for i in range(20)]
    assert endurance.analyse_samples(_samples(**{RSS: slow}))["leaks"] == []
    strict = endurance.analyse_samples(_samples(**{RSS: slow}), growth_ratio=1.3)
    assert len(strict["leaks"]) == 1


# ── Plateau vs leak ────────────────────────────────────────────────────────
#
# The distinction this harness got wrong on its first real run against Nodyra:
# the warm worker pool fills over minutes under load and then sits at its
# ceiling. Compared start-to-end that is indistinguishable from a leak. It is
# only distinguishable by whether the series is still going up at the end.


def test_a_pool_filling_to_its_ceiling_is_not_a_leak():
    """Large sustained growth that then flattens is a bounded pool, not a leak."""
    filling = [100.0 + i * 60 for i in range(10)] + [640.0] * 14
    result = endurance.analyse_samples(_samples(**{FDS: filling}))
    assert result["leaks"] == [], result["leaks"]
    # The growth is still reported — it is real, and worth seeing; it is just
    # not a leak. (Measured between the medians of the outer thirds, so it is
    # smaller than the 100 -> 640 the endpoints suggest.)
    assert result["gauges"][FDS]["growth"] > 300
    assert result["gauges"][FDS]["peak"] == 640.0
    assert result["gauges"][FDS]["still_climbing"] is False


def test_a_series_that_rises_then_falls_back_is_not_a_leak():
    """Nodyra's own shape: descriptors peaked mid-run and came back down as
    idle workers were reaped."""
    shape = [100.0 + i * 70 for i in range(10)] + [800.0 - i * 25 for i in range(14)]
    assert endurance.analyse_samples(_samples(**{FDS: shape}))["leaks"] == []


def test_a_relentless_climb_is_still_reported():
    """The plateau check must not become a way for real leaks to hide."""
    climbing = [100.0 + i * 30 for i in range(24)]
    result = endurance.analyse_samples(_samples(**{FDS: climbing}))
    assert len(result["leaks"]) == 1
    assert "still rising" in result["leaks"][0]
    assert result["gauges"][FDS]["still_climbing"] is True


def test_a_short_series_falls_back_to_the_endpoint_comparison():
    """Too few samples to judge a plateau must fail safe — report the growth
    rather than silently clear it."""
    short = [100.0, 100.0, 200.0, 300.0, 400.0, 500.0]
    result = endurance.analyse_samples(_samples(**{FDS: short}))
    assert len(result["leaks"]) == 1


def test_the_raw_series_is_published_for_checking():
    """A verdict nobody can check is a verdict nobody should trust."""
    values = [200 * MIB] * 12
    result = endurance.analyse_samples(_samples(**{RSS: values}))
    assert result["gauges"][RSS]["series"] == [float(v) for v in values]
