from __future__ import annotations

import json

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.artifacts import LocalArtifactStore
from noodle.context import artifact_store, current_node_id
from noodle.datasets import is_dataset_ref
from noodle.sdk import registry
from noodle_nodes.browser_automation import sitemap_crawl
from noodle_nodes.data_quality import currency_normalize, schema_validate, string_normalize
from noodle_nodes.datasets import dataset_to_records
from noodle_nodes.geospatial import geospatial_distance
from noodle_nodes.security_automation import password_strength_check


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
    assert [row["amount_normalized"] for row in rows] == ["1000", "1234.56", "1.25"]
    assert [row["currency"] for row in rows] == ["USD", "EUR", "GBP"]


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
