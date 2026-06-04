from noodle.packages import canonical_package_name, missing_packages


def test_canonical_strips_specifier_extras_and_normalizes():
    assert canonical_package_name("scikit-learn") == "scikit-learn"
    assert canonical_package_name("scikit_learn") == "scikit-learn"
    assert canonical_package_name("DuckDB>=0.9") == "duckdb"
    assert canonical_package_name("uvicorn[standard]==0.30") == "uvicorn"
    assert canonical_package_name("  pandas ; python_version>'3.8'  ") == "pandas"


def test_missing_packages_ignores_versions_and_case():
    required = ["duckdb>=0.9", "Pandas", "cairosvg"]
    installed = ["duckdb==1.1.0", "pandas"]
    assert missing_packages(required, installed) == ["cairosvg"]


def test_missing_packages_empty_when_all_present():
    assert missing_packages(["numpy"], ["numpy==2.1"]) == []
