from __future__ import annotations

import json

import pytest

import nodyra_nodes  # noqa: F401 - registers nodes
from nodyra.artifacts import LocalArtifactStore
from nodyra.context import artifact_store, current_node_id
from nodyra.datasets import is_dataset_ref
from nodyra.sdk import registry
from nodyra_nodes.browser_automation import sitemap_crawl
from nodyra_nodes.data_quality import (
    currency_normalize,
    outlier_detect_statistical,
    schema_validate,
    string_normalize,
)
from nodyra_nodes.datasets import dataset_to_records
from nodyra_nodes.geospatial import geospatial_distance
from nodyra_nodes.security_automation import password_strength_check


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="test-run")
    artifact_token = artifact_store.set(store)
    node_token = current_node_id.set("test-node")
    yield store
    current_node_id.reset(node_token)
    artifact_store.reset(artifact_token)


def test_planned_package_nodes_are_registered() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}
    expected = {
        "data_profile_report",
        "schema_validate",
        "expectation_suite_run",
        "record_linkage",
        "data_reconcile",
        "outlier_detect_statistical",
        "string_normalize",
        "date_parse_normalize",
        "currency_normalize",
        "certificate_inspect",
        "rsa_sign_verify",
        "pgp_encrypt_decrypt",
        "jwt_sign_verify",
        "ldap_query",
        "sftp_transfer",
        "password_strength_check",
        "network_port_probe",
        "file_integrity_manifest",
        "map_generate",
        "geocode",
        "reverse_geocode",
        "shapefile_read",
        "geospatial_join",
        "geospatial_buffer",
        "geospatial_distance",
        "coordinate_transform",
        "isochrone_generate",
        "seasonality_detect",
        "survival_analysis",
        "sensitivity_analysis",
        "html_extract",
        "sitemap_crawl",
    }

    assert expected <= set(manifests)
    assert manifests["schema_validate"].category == "Data Quality"
    assert manifests["certificate_inspect"].category == "Security"
    assert manifests["geocode"].category == "Geospatial"
    assert manifests["seasonality_detect"].category == "Statistical Analysis"
    assert manifests["graphql_request"].category == "API"


def test_schema_validate_splits_invalid_rows(store_ctx) -> None:
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "number"},
        },
    }
    result = schema_validate(
        input=[{"name": "Ada", "age": 37}, {"age": "unknown"}],
        schema_json=json.dumps(schema),
    )

    assert result["main"]["n_valid"] == 1
    assert result["main"]["n_invalid"] == 1
    assert is_dataset_ref(result["main"]["valid_rows"])
    rows = dataset_to_records(input=result["invalid"], max_rows=10)
    assert rows[0]["_row_index"] == 1
    assert "name" in rows[0]["_validation_errors"]


def test_string_normalize_outputs_dataset(store_ctx) -> None:
    result = string_normalize(
        input=[{"name": "  Café    AU  "}],
        columns="name",
        output_suffix="_normalized",
    )

    assert is_dataset_ref(result)
    rows = dataset_to_records(input=result, max_rows=10)
    assert rows == [{"name": "  Café    AU  ", "name_normalized": "cafe au"}]


def test_currency_normalize_keeps_us_thousands_separator(store_ctx) -> None:
    result = currency_normalize(
        input=[
            {"amount": "$1,000"},
            {"amount": "EUR 1.234,56"},
            {"amount": "1,25 GBP"},
        ],
    )

    rows = dataset_to_records(input=result, max_rows=10)
    assert [row["amount_normalized"] for row in rows] == [1000.0, 1234.56, 1.25]
    assert [row["currency"] for row in rows] == ["USD", "EUR", "GBP"]


def test_currency_normalize_emits_a_number_downstream_nodes_can_use(store_ctx) -> None:
    """The normalized amount must be numeric, not text.

    Emitting a string here silently breaks every numeric consumer downstream:
    a schema_validate gate on ``{"type": "number"}`` rejects every row, and
    outlier detection reports that it found no numeric values at all.
    """
    result = currency_normalize(
        input=[{"amount": "$1,000"}, {"amount": "EUR 1.234,56"}, {"amount": "nope"}],
    )

    types = {field["name"]: field["type"] for field in result["schema"]}
    assert types["amount_normalized"] == "DOUBLE"

    rows = dataset_to_records(input=result, max_rows=10)
    assert [row["amount_normalized"] for row in rows] == [1000.0, 1234.56, None]
    # Unparseable amounts become null rather than poisoning the column type.
    assert all(
        isinstance(row["amount_normalized"], float | type(None)) for row in rows
    )


def test_sitemap_crawl_parses_inline_xml(store_ctx) -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://example.com/</loc>
        <lastmod>2026-06-01</lastmod>
      </url>
      <url>
        <loc>https://example.com/docs</loc>
        <priority>0.8</priority>
      </url>
    </urlset>
    """

    result = sitemap_crawl(input=xml, max_urls=10)

    assert result["url_count"] == 2
    rows = dataset_to_records(input=result["dataset"], max_rows=10)
    assert [row["loc"] for row in rows] == [
        "https://example.com/",
        "https://example.com/docs",
    ]


def test_geospatial_distance_rejects_excessive_cross_product() -> None:
    with pytest.raises(ValueError, match="pair count"):
        geospatial_distance(
            input=[
                {"lat": 0, "lon": 0},
                {"lat": 1, "lon": 1},
                {"lat": 2, "lon": 2},
            ],
            right_records_json=json.dumps(
                [
                    {"lat": 3, "lon": 3},
                    {"lat": 4, "lon": 4},
                    {"lat": 5, "lon": 5},
                ]
            ),
            max_pairs=8,
        )


def test_password_strength_check_scores_strong_password() -> None:
    result = password_strength_check(password="CorrectHorseBatteryStaple42!")

    assert result["strength"] == "strong"
    assert result["passed"] is True
    assert result["checks"]["has_symbol"] is True


def test_outlier_detect_names_the_missing_column(store_ctx) -> None:
    """A wiring mistake must be reported as a wiring mistake.

    Feeding this node something that lacks the requested column used to fail
    with "At least three numeric values are required", which sends the reader
    looking for a data problem instead of the actual cause.
    """
    with pytest.raises(ValueError) as excinfo:
        outlier_detect_statistical(
            input=[{"n_rows": 12, "n_valid": 10, "valid_rate": 0.83}],
            column="amount_normalized",
        )

    message = str(excinfo.value)
    assert "amount_normalized" in message
    assert "not found" in message
    assert "n_valid" in message  # tells the reader what it *did* receive


def test_outlier_detect_reports_how_many_numbers_it_found(store_ctx) -> None:
    with pytest.raises(ValueError) as excinfo:
        outlier_detect_statistical(
            input=[{"amount": 1.0}, {"amount": "n/a"}, {"amount": None}],
            column="amount",
        )

    assert "1 numeric value(s) across 3 row(s)" in str(excinfo.value)


def test_schema_validate_exposes_valid_rows_as_its_own_output(store_ctx) -> None:
    """Validating then processing the good rows must not need a code node.

    "main" is a summary object, so wiring it straight into a data node feeds
    that node a single summary record instead of the rows. The "valid" output
    is the dataset a quality gate is actually for.
    """
    schema = {"type": "object", "required": ["name"]}
    result = schema_validate(
        input=[{"name": "Ada"}, {"nope": 1}, {"name": "Grace"}],
        schema_json=json.dumps(schema),
    )

    assert is_dataset_ref(result["valid"])
    assert dataset_to_records(input=result["valid"], max_rows=10) == [
        {"name": "Ada"},
        {"name": "Grace"},
    ]
    # main keeps its existing shape for graphs that already read the summary.
    assert result["main"]["n_valid"] == 2
    assert is_dataset_ref(result["main"]["valid_rows"])


def test_schema_validate_valid_output_is_none_when_rows_are_not_kept(store_ctx) -> None:
    result = schema_validate(
        input=[{"name": "Ada"}],
        schema_json=json.dumps({"type": "object", "required": ["name"]}),
        include_valid_rows=False,
    )
    assert result["valid"] is None


def test_outlier_detect_emits_the_rows_that_passed(store_ctx) -> None:
    """"Flag the anomalies, then process the clean rows" must be wireable.

    The node only ever emitted the outliers and a summary, so the rows that
    passed the check were not available to any downstream node at all.
    """
    rows = [{"amount": v} for v in (10.0, 11.0, 12.0, 10.5, 9.5, 5000.0)]

    result = outlier_detect_statistical(input=rows, column="amount", method="iqr")

    assert result["main"]["n_outliers"] == 1
    assert result["main"]["n_kept"] == 5
    assert [r["amount"] for r in dataset_to_records(input=result["outliers"], max_rows=10)] == [5000.0]
    kept = dataset_to_records(input=result["kept"], max_rows=10)
    assert [r["amount"] for r in kept] == [10.0, 11.0, 12.0, 10.5, 9.5]
    # The kept rows are the untouched originals, not annotated copies.
    assert set(kept[0]) == {"amount"}


def test_outlier_detect_keeps_everything_when_nothing_is_anomalous(store_ctx) -> None:
    """The clean path: no outliers, so every row flows on through "kept"."""
    rows = [{"amount": v} for v in (10.0, 11.0, 12.0, 10.5, 9.5)]

    result = outlier_detect_statistical(input=rows, column="amount", method="iqr")

    assert result["main"]["n_outliers"] == 0
    assert result["main"]["n_kept"] == 5
    assert result["outliers"] is None
    assert dataset_to_records(input=result["kept"], max_rows=10) == rows
