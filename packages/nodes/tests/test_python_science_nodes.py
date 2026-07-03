"""Tests for python_science_nodes.py (Tasks 30-39)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared fixtures & helpers
# ---------------------------------------------------------------------------


def _make_rows(*dicts: dict) -> list[dict]:
    return list(dicts)


# ---------------------------------------------------------------------------
# Task 30 — pandas_transform
# ---------------------------------------------------------------------------


def _make_pandas():
    """Return a real pandas if available, else skip."""
    pd = pytest.importorskip("pandas")
    return pd


class TestPandasTransform:
    def test_filter_eq(self):
        _make_pandas()
        from nodyra_nodes.python_science_nodes import pandas_transform

        rows = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        result = pandas_transform(
            input=rows,
            operation="filter",
            filter_column="age",
            filter_operator="==",
            filter_value="30",
        )
        assert result["rows"] == 1
        assert result["data"][0]["name"] == "Alice"

    def test_filter_gt(self):
        _make_pandas()
        from nodyra_nodes.python_science_nodes import pandas_transform

        rows = [{"x": 1}, {"x": 5}, {"x": 3}]
        result = pandas_transform(
            input=rows, operation="filter", filter_column="x", filter_operator=">", filter_value="2"
        )
        assert result["rows"] == 2

    def test_filter_contains(self):
        _make_pandas()
        from nodyra_nodes.python_science_nodes import pandas_transform

        rows = [{"name": "Alice"}, {"name": "Bob"}, {"name": "Charlie"}]
        result = pandas_transform(
            input=rows,
            operation="filter",
            filter_column="name",
            filter_operator="contains",
            filter_value="li",
        )
        names = {r["name"] for r in result["data"]}
        assert "Alice" in names
        assert "Charlie" in names
        assert "Bob" not in names

    def test_select_columns(self):
        _make_pandas()
        from nodyra_nodes.python_science_nodes import pandas_transform

        rows = [{"a": 1, "b": 2, "c": 3}]
        result = pandas_transform(input=rows, operation="select_columns", columns="a, c")
        assert set(result["columns"]) == {"a", "c"}

    def test_sort(self):
        _make_pandas()
        from nodyra_nodes.python_science_nodes import pandas_transform

        rows = [{"v": 3}, {"v": 1}, {"v": 2}]
        result = pandas_transform(input=rows, operation="sort", columns="v", sort_ascending=True)
        vals = [r["v"] for r in result["data"]]
        assert vals == sorted(vals)

    def test_sort_descending(self):
        _make_pandas()
        from nodyra_nodes.python_science_nodes import pandas_transform

        rows = [{"v": 3}, {"v": 1}, {"v": 2}]
        result = pandas_transform(input=rows, operation="sort", columns="v", sort_ascending=False)
        vals = [r["v"] for r in result["data"]]
        assert vals == sorted(vals, reverse=True)

    def test_fill_na(self):
        _make_pandas()
        from nodyra_nodes.python_science_nodes import pandas_transform

        rows = [{"a": None}, {"a": 5}]
        result = pandas_transform(input=rows, operation="fill_na", fill_value="0")
        assert result["data"][0]["a"] == 0

    def test_drop_duplicates(self):
        _make_pandas()
        from nodyra_nodes.python_science_nodes import pandas_transform

        rows = [{"a": 1}, {"a": 1}, {"a": 2}]
        result = pandas_transform(input=rows, operation="drop_duplicates")
        assert result["rows"] == 2

    def test_output_json(self):
        _make_pandas()
        from nodyra_nodes.python_science_nodes import pandas_transform

        rows = [{"x": 1}]
        result = pandas_transform(
            input=rows,
            operation="filter",
            filter_column="x",
            filter_operator="==",
            filter_value="1",
            output_as="json",
        )
        assert isinstance(result["data"], list)

    def test_filter_missing_column_raises(self):
        _make_pandas()
        from nodyra_nodes.python_science_nodes import pandas_transform

        with pytest.raises(ValueError, match="not found"):
            pandas_transform(
                input=[{"a": 1}],
                operation="filter",
                filter_column="z",
                filter_operator="==",
                filter_value="1",
            )

    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pandas", None)
        from nodyra_nodes.python_science_nodes import pandas_transform

        with pytest.raises(ImportError, match="pandas"):
            pandas_transform(
                input=[{"a": 1}],
                operation="filter",
                filter_column="a",
                filter_operator="==",
                filter_value="1",
            )


# ---------------------------------------------------------------------------
# Task 31 — numpy_array_ops
# ---------------------------------------------------------------------------


def _make_numpy():
    np = pytest.importorskip("numpy")
    return np


class TestNumpyArrayOps:
    def test_stats_mean(self):
        _make_numpy()
        from nodyra_nodes.python_science_nodes import numpy_array_ops

        result = numpy_array_ops(input=[1, 2, 3, 4, 5], operation="stats", stats_list="mean")
        assert result["stats"]["mean"] == pytest.approx(3.0)

    def test_stats_std(self):
        _make_numpy()
        from nodyra_nodes.python_science_nodes import numpy_array_ops

        result = numpy_array_ops(input=[1, 1, 1], operation="stats", stats_list="std")
        assert result["stats"]["std"] == pytest.approx(0.0)

    def test_math_add(self):
        _make_numpy()
        from nodyra_nodes.python_science_nodes import numpy_array_ops

        result = numpy_array_ops(input=[1, 2, 3], operation="math", math_op="add", scalar=10.0)
        assert result["result"] == [11, 12, 13]

    def test_math_mul(self):
        _make_numpy()
        from nodyra_nodes.python_science_nodes import numpy_array_ops

        result = numpy_array_ops(input=[2, 4], operation="math", math_op="mul", scalar=3.0)
        assert result["result"] == [6, 12]

    def test_math_div_zero_raises(self):
        _make_numpy()
        from nodyra_nodes.python_science_nodes import numpy_array_ops

        with pytest.raises(ValueError, match="cannot be zero"):
            numpy_array_ops(input=[1, 2], operation="math", math_op="div", scalar=0.0)

    def test_reshape(self):
        _make_numpy()
        from nodyra_nodes.python_science_nodes import numpy_array_ops

        result = numpy_array_ops(input=[1, 2, 3, 4, 5, 6], operation="reshape", new_shape="2, 3")
        assert result["shape"] == [2, 3]

    def test_transpose(self):
        _make_numpy()
        from nodyra_nodes.python_science_nodes import numpy_array_ops

        result = numpy_array_ops(input=[[1, 2], [3, 4]], operation="transpose")
        assert result["shape"] == [2, 2]
        assert result["result"][0][1] == 3  # transposed

    def test_clip(self):
        _make_numpy()
        from nodyra_nodes.python_science_nodes import numpy_array_ops

        result = numpy_array_ops(input=[0, 5, 10], operation="clip", clip_min=2.0, clip_max=8.0)
        assert result["result"] == [2, 5, 8]

    def test_normalize(self):
        _make_numpy()
        from nodyra_nodes.python_science_nodes import numpy_array_ops

        result = numpy_array_ops(input=[0, 5, 10], operation="normalize")
        assert result["result"] == pytest.approx([0.0, 0.5, 1.0])

    def test_dot_product(self):
        _make_numpy()
        from nodyra_nodes.python_science_nodes import numpy_array_ops

        result = numpy_array_ops(input=[[1, 2, 3], [4, 5, 6]], operation="dot_product")
        assert result["result"] == pytest.approx(32.0)

    def test_concat(self):
        _make_numpy()
        from nodyra_nodes.python_science_nodes import numpy_array_ops

        result = numpy_array_ops(input=[[1, 2], [3, 4]], operation="concat")
        assert result["result"] == [1, 2, 3, 4]

    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "numpy", None)
        from nodyra_nodes.python_science_nodes import numpy_array_ops

        with pytest.raises(ImportError, match="numpy"):
            numpy_array_ops(input=[1, 2], operation="stats")


# ---------------------------------------------------------------------------
# Task 32 — matplotlib_chart
# ---------------------------------------------------------------------------


def _make_matplotlib():
    mpl = pytest.importorskip("matplotlib")
    return mpl


_FAKE_CHART = {"kind": "image", "name": "chart.png"}
_PATCH_CHART_WB = patch("nodyra_nodes.python_science_nodes.write_bytes", return_value=_FAKE_CHART)


class TestMatplotlibChart:
    def test_returns_artifact(self):
        _make_matplotlib()
        from nodyra_nodes.python_science_nodes import matplotlib_chart

        rows = [{"x": i, "y": i * 2} for i in range(10)]
        with _PATCH_CHART_WB:
            result = matplotlib_chart(input=rows, chart_type="line", x_column="x", y_columns="y")
        assert result["chart"] == _FAKE_CHART
        assert result["format"] == "png"
        assert result["rows"] == 10

    def test_bar_chart(self):
        _make_matplotlib()
        from nodyra_nodes.python_science_nodes import matplotlib_chart

        rows = [{"cat": "A", "val": 3}, {"cat": "B", "val": 7}]
        with _PATCH_CHART_WB:
            result = matplotlib_chart(input=rows, chart_type="bar", x_column="cat", y_columns="val")
        assert result["chart_type"] == "bar"

    def test_empty_data_no_crash(self):
        _make_matplotlib()
        from nodyra_nodes.python_science_nodes import matplotlib_chart

        with _PATCH_CHART_WB:
            result = matplotlib_chart(input=[], chart_type="line")
        assert "chart" in result

    def test_svg_format(self):
        _make_matplotlib()
        from nodyra_nodes.python_science_nodes import matplotlib_chart

        rows = [{"x": 1, "y": 2}]
        with _PATCH_CHART_WB:
            result = matplotlib_chart(input=rows, chart_type="scatter", output_format="svg")
        assert result["format"] == "svg"

    def test_histogram(self):
        _make_matplotlib()
        from nodyra_nodes.python_science_nodes import matplotlib_chart

        rows = [{"val": i} for i in range(20)]
        with _PATCH_CHART_WB:
            result = matplotlib_chart(input=rows, chart_type="histogram", y_columns="val")
        assert "chart" in result

    def test_pie_chart(self):
        _make_matplotlib()
        from nodyra_nodes.python_science_nodes import matplotlib_chart

        rows = [{"label": "A", "value": 30}, {"label": "B", "value": 70}]
        with _PATCH_CHART_WB:
            result = matplotlib_chart(
                input=rows, chart_type="pie", x_column="label", y_columns="value"
            )
        assert "chart" in result

    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "matplotlib", None)
        from nodyra_nodes.python_science_nodes import matplotlib_chart

        with pytest.raises(ImportError, match="matplotlib"):
            matplotlib_chart(input=[{"x": 1}], chart_type="line")


# ---------------------------------------------------------------------------
# Task 33 — pydantic_validate
# ---------------------------------------------------------------------------


class TestPydanticValidate:
    def test_valid_items(self):
        pytest.importorskip("pydantic")
        from nodyra_nodes.python_science_nodes import pydantic_validate

        schema = "class User(BaseModel):\n    name: str\n    age: int"
        result = pydantic_validate(
            input=[{"name": "Alice", "age": 30}],
            schema=schema,
        )
        assert result["valid_count"] == 1
        assert result["invalid_count"] == 0

    def test_invalid_items_filter(self):
        pytest.importorskip("pydantic")
        from nodyra_nodes.python_science_nodes import pydantic_validate

        schema = "class Item(BaseModel):\n    value: int"
        result = pydantic_validate(
            input=[{"value": 1}, {"value": "not_an_int"}],
            schema=schema,
            on_error="filter",
        )
        assert result["valid_count"] == 1
        assert result["invalid_count"] == 1

    def test_invalid_item_raises(self):
        pytest.importorskip("pydantic")
        from nodyra_nodes.python_science_nodes import pydantic_validate

        schema = "class Item(BaseModel):\n    value: int"
        with pytest.raises(ValueError, match="validation failed"):
            pydantic_validate(input=[{"value": "bad"}], schema=schema, on_error="raise")

    def test_flag_mode(self):
        pytest.importorskip("pydantic")
        from nodyra_nodes.python_science_nodes import pydantic_validate

        schema = "class Item(BaseModel):\n    value: int"
        result = pydantic_validate(
            input=[{"value": "bad"}],
            schema=schema,
            on_error="flag",
        )
        assert result["invalid"][0]["_valid"] is False

    def test_blocks_os_import(self):
        pytest.importorskip("pydantic")
        from nodyra_nodes.python_science_nodes import pydantic_validate

        schema = "import os\nclass Item(BaseModel):\n    value: int"
        with pytest.raises(ValueError, match="not allowed"):
            pydantic_validate(input=[{"value": 1}], schema=schema)

    def test_blocks_subprocess_import(self):
        pytest.importorskip("pydantic")
        from nodyra_nodes.python_science_nodes import pydantic_validate

        schema = "import subprocess\nclass Item(BaseModel):\n    value: str"
        with pytest.raises(ValueError, match="not allowed"):
            pydantic_validate(input=[{"value": "x"}], schema=schema)

    def test_blocks_eval_call(self):
        pytest.importorskip("pydantic")
        from nodyra_nodes.python_science_nodes import pydantic_validate

        schema = "eval('1+1')\nclass Item(BaseModel):\n    value: int"
        with pytest.raises(ValueError, match="not allowed"):
            pydantic_validate(input=[{"value": 1}], schema=schema)

    def test_syntax_error_raises(self):
        pytest.importorskip("pydantic")
        from nodyra_nodes.python_science_nodes import pydantic_validate

        schema = "class Item(BaseModel:\n    value: int"
        with pytest.raises(ValueError, match="syntax error"):
            pydantic_validate(input=[{"value": 1}], schema=schema)

    def test_no_schema_raises(self):
        pytest.importorskip("pydantic")
        from nodyra_nodes.python_science_nodes import pydantic_validate

        with pytest.raises(ValueError, match="schema is required"):
            pydantic_validate(input=[{"v": 1}], schema="")

    def test_no_model_class_raises(self):
        pytest.importorskip("pydantic")
        from nodyra_nodes.python_science_nodes import pydantic_validate

        with pytest.raises(ValueError, match="no BaseModel"):
            pydantic_validate(input=[{"v": 1}], schema="x = 1")


# ---------------------------------------------------------------------------
# Task 34 — opencv_process
# ---------------------------------------------------------------------------


def _make_cv2_mock():
    """Build a minimal cv2 mock."""
    cv2 = MagicMock()
    import numpy as np_real

    img = np_real.zeros((100, 100, 3), dtype=np_real.uint8)
    cv2.imdecode.return_value = img
    # imencode returns (retval, buf)
    _, buf = True, np_real.zeros(100, dtype=np_real.uint8)
    cv2.imencode.return_value = (True, buf)
    cv2.COLOR_BGR2GRAY = 6
    cv2.COLOR_GRAY2BGR = 8
    cv2.COLOR_BGR2RGB = 4
    cv2.IMREAD_COLOR = 1
    cv2.THRESH_BINARY = 0
    cv2.RETR_EXTERNAL = 0
    cv2.CHAIN_APPROX_SIMPLE = 1
    cv2.cvtColor.return_value = np_real.zeros((100, 100), dtype=np_real.uint8)
    cv2.Canny.return_value = np_real.zeros((100, 100), dtype=np_real.uint8)
    cv2.GaussianBlur.return_value = img
    cv2.filter2D.return_value = img
    cv2.threshold.return_value = (127, np_real.zeros((100, 100), dtype=np_real.uint8))
    cv2.findContours.return_value = ([], None)
    cv2.fastNlMeansDenoisingColored.return_value = img
    cv2.convertScaleAbs.return_value = img
    cv2.drawContours.return_value = None
    cv2.rectangle.return_value = None
    cascade = MagicMock()
    cascade.detectMultiScale.return_value = []
    cv2.CascadeClassifier.return_value = cascade
    cv2.data = MagicMock()
    cv2.data.haarcascades = ""
    return cv2


_FAKE_ARTIFACT = {"kind": "image", "name": "processed.png"}
_PATCH_WB = patch("nodyra_nodes.python_science_nodes.write_bytes", return_value=_FAKE_ARTIFACT)


class TestOpenCVProcess:
    def test_blur(self):
        pytest.importorskip("numpy")
        from nodyra_nodes.python_science_nodes import opencv_process

        cv2 = _make_cv2_mock()
        with patch.dict(sys.modules, {"cv2": cv2}), _PATCH_WB:
            result = opencv_process(
                input=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, operation="blur", blur_kernel=5
            )
            assert result["image"] == _FAKE_ARTIFACT
            assert result["operation"] == "blur"

    def test_grayscale(self):
        pytest.importorskip("numpy")
        from nodyra_nodes.python_science_nodes import opencv_process

        cv2 = _make_cv2_mock()
        with patch.dict(sys.modules, {"cv2": cv2}), _PATCH_WB:
            result = opencv_process(input=b"\x00" * 200, operation="grayscale")
            assert result["operation"] == "grayscale"

    def test_detect_faces(self):
        pytest.importorskip("numpy")
        from nodyra_nodes.python_science_nodes import opencv_process

        cv2 = _make_cv2_mock()
        with patch.dict(sys.modules, {"cv2": cv2}), _PATCH_WB:
            result = opencv_process(input=b"\x00" * 200, operation="detect_faces")
            assert "face_count" in result
            assert result["face_count"] == 0

    def test_invalid_image_raises(self):
        pytest.importorskip("numpy")
        from nodyra_nodes.python_science_nodes import opencv_process

        cv2 = _make_cv2_mock()
        cv2.imdecode.return_value = None
        with patch.dict(sys.modules, {"cv2": cv2}):
            with pytest.raises(ValueError, match="could not decode"):
                opencv_process(input=b"\x00" * 10, operation="blur")

    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "cv2", None)
        from nodyra_nodes.python_science_nodes import opencv_process

        with pytest.raises(ImportError, match="opencv"):
            opencv_process(input=b"\x00", operation="blur")


# ---------------------------------------------------------------------------
# Task 35 — scipy_stats
# ---------------------------------------------------------------------------


class TestSciPyStats:
    def test_ttest_1sample(self):
        pytest.importorskip("scipy")
        from nodyra_nodes.python_science_nodes import scipy_stats

        result = scipy_stats(input=[1, 2, 3, 4, 5], test="ttest", alpha=0.05)
        assert "statistic" in result
        assert "p_value" in result

    def test_describe(self):
        pytest.importorskip("scipy")
        from nodyra_nodes.python_science_nodes import scipy_stats

        result = scipy_stats(input=[1, 2, 3, 4, 5], test="describe")
        assert result["count"] == 5
        assert result["mean"] == pytest.approx(3.0)

    def test_ks_test(self):
        pytest.importorskip("scipy")
        from nodyra_nodes.python_science_nodes import scipy_stats

        result = scipy_stats(input=[0.1, 0.5, 0.9, 1.5, -0.3], test="ks")
        assert "statistic" in result
        assert "p_value" in result

    def test_normaltest(self):
        pytest.importorskip("scipy")
        from nodyra_nodes.python_science_nodes import scipy_stats

        result = scipy_stats(input=[1, 2, 3, 4, 5, 6, 7, 8], test="normaltest")
        assert "is_normal" in result

    def test_zscore(self):
        pytest.importorskip("scipy")
        from nodyra_nodes.python_science_nodes import scipy_stats

        result = scipy_stats(input=[1, 2, 3], test="zscore")
        assert "zscores" in result
        assert len(result["zscores"]) == 3

    def test_pearsonr_requires_y_column(self):
        pytest.importorskip("scipy")
        from nodyra_nodes.python_science_nodes import scipy_stats

        with pytest.raises(ValueError, match="y_column is required"):
            scipy_stats(input=[1, 2, 3], test="pearsonr")

    def test_pearsonr_with_data(self):
        pytest.importorskip("scipy")
        from nodyra_nodes.python_science_nodes import scipy_stats

        rows = [{"x": i, "y": i * 2} for i in range(10)]
        result = scipy_stats(input=rows, test="pearsonr", samples_column="x", y_column="y")
        assert result["r"] == pytest.approx(1.0)

    def test_mannwhitney_requires_samples_column(self):
        pytest.importorskip("scipy")
        from nodyra_nodes.python_science_nodes import scipy_stats

        with pytest.raises(ValueError, match="samples_column is required"):
            scipy_stats(input=[1, 2, 3], test="mannwhitney")

    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "scipy", None)
        from nodyra_nodes.python_science_nodes import scipy_stats

        with pytest.raises(ImportError, match="scipy"):
            scipy_stats(input=[1, 2, 3], test="describe")


# ---------------------------------------------------------------------------
# Task 36 — spacy_nlp
# ---------------------------------------------------------------------------


def _make_spacy_mock():
    """Build a minimal spaCy mock that returns a realistic Doc-like object."""
    spacy = MagicMock()
    doc = MagicMock()
    # Entities
    ent = MagicMock()
    ent.text = "London"
    ent.label_ = "GPE"
    ent.start_char = 0
    ent.end_char = 6
    doc.ents = [ent]
    # Tokens
    tok = MagicMock()
    tok.text = "London"
    tok.pos_ = "PROPN"
    tok.tag_ = "NNP"
    tok.lemma_ = "London"
    tok.dep_ = "nsubj"
    tok.head = tok
    tok.children = []
    doc.__iter__ = MagicMock(return_value=iter([tok]))
    # Sentences
    sent = MagicMock()
    sent.text = "London is great."
    doc.sents = [sent]
    # Noun phrases
    chunk = MagicMock()
    chunk.text = "London"
    doc.noun_chunks = [chunk]
    nlp = MagicMock(return_value=doc)
    spacy.load.return_value = nlp
    return spacy


class TestSpacyNLP:
    def test_ner(self):
        from nodyra_nodes.python_science_nodes import spacy_nlp

        spacy_mock = _make_spacy_mock()
        with patch.dict(sys.modules, {"spacy": spacy_mock}):
            result = spacy_nlp(text="London is great.", components="ner")
            assert "entities" in result
            assert result["entities"][0]["text"] == "London"

    def test_pos(self):
        from nodyra_nodes.python_science_nodes import spacy_nlp

        spacy_mock = _make_spacy_mock()
        with patch.dict(sys.modules, {"spacy": spacy_mock}):
            result = spacy_nlp(text="London is great.", components="pos")
            assert "tokens" in result
            assert result["tokens"][0]["pos"] == "PROPN"

    def test_sentences(self):
        from nodyra_nodes.python_science_nodes import spacy_nlp

        spacy_mock = _make_spacy_mock()
        with patch.dict(sys.modules, {"spacy": spacy_mock}):
            result = spacy_nlp(text="London is great.", components="sentences")
            assert "sentences" in result

    def test_empty_text_raises(self):
        from nodyra_nodes.python_science_nodes import spacy_nlp

        spacy_mock = _make_spacy_mock()
        with patch.dict(sys.modules, {"spacy": spacy_mock}):
            with pytest.raises(ValueError, match="text input is required"):
                spacy_nlp(text="", input=None)

    def test_model_not_found_raises(self):
        from nodyra_nodes.python_science_nodes import spacy_nlp

        spacy_mock = _make_spacy_mock()
        spacy_mock.load.side_effect = OSError("model not found")
        with patch.dict(sys.modules, {"spacy": spacy_mock}):
            with pytest.raises(ImportError, match="model"):
                spacy_nlp(text="hello", model="missing_model")

    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "spacy", None)
        from nodyra_nodes.python_science_nodes import spacy_nlp

        with pytest.raises(ImportError, match="spacy"):
            spacy_nlp(text="hello")


# ---------------------------------------------------------------------------
# Task 37 — networkx_graph_ops
# ---------------------------------------------------------------------------


def _make_networkx_mock():
    nx = MagicMock()
    # Make DiGraph / Graph constructors return simple MagicMocks
    graph = MagicMock()
    graph.number_of_nodes.return_value = 4
    graph.number_of_edges.return_value = 3
    graph.is_square = True
    nx.Graph.return_value = graph
    nx.DiGraph.return_value = graph
    # shortest_path
    nx.shortest_path.return_value = ["A", "B", "C"]
    nx.shortest_path_length.return_value = 2
    nx.single_source_shortest_path.return_value = {"B": ["A", "B"]}
    nx.betweenness_centrality.return_value = {"A": 0.5, "B": 0.3, "C": 0.2}
    nx.degree_centrality.return_value = {"A": 1.0, "B": 0.5}
    nx.pagerank.return_value = {"A": 0.4, "B": 0.6}
    nx.clustering.return_value = {"A": 0.0, "B": 0.5}
    nx.average_clustering.return_value = 0.25
    nx.connected_components.return_value = [{"A", "B"}, {"C"}]
    mst = MagicMock()
    mst.edges.return_value = [("A", "B", {"weight": 1})]
    mst.number_of_nodes.return_value = 2
    mst.number_of_edges.return_value = 1
    nx.minimum_spanning_tree.return_value = mst
    nx.all_shortest_paths.return_value = [["A", "B"], ["A", "C", "B"]]
    nx.NetworkXNoPath = Exception
    nx.NodeNotFound = Exception
    return nx


EDGE_LIST = [
    {"source": "A", "target": "B"},
    {"source": "B", "target": "C"},
    {"source": "A", "target": "C"},
]


class TestNetworkXGraphOps:
    def test_shortest_path(self):
        from nodyra_nodes.python_science_nodes import networkx_graph_ops

        nx_mock = _make_networkx_mock()
        with patch.dict(sys.modules, {"networkx": nx_mock}):
            result = networkx_graph_ops(
                input=EDGE_LIST, operation="shortest_path", source="A", target="C"
            )
            assert result["path"] == ["A", "B", "C"]
            assert result["hops"] == 2

    def test_betweenness_centrality(self):
        from nodyra_nodes.python_science_nodes import networkx_graph_ops

        nx_mock = _make_networkx_mock()
        with patch.dict(sys.modules, {"networkx": nx_mock}):
            result = networkx_graph_ops(input=EDGE_LIST, operation="betweenness_centrality")
            assert "centrality" in result
            assert "most_central" in result

    def test_pagerank(self):
        from nodyra_nodes.python_science_nodes import networkx_graph_ops

        nx_mock = _make_networkx_mock()
        with patch.dict(sys.modules, {"networkx": nx_mock}):
            result = networkx_graph_ops(input=EDGE_LIST, operation="pagerank")
            assert "pagerank" in result
            assert "top_node" in result

    def test_connected_components(self):
        from nodyra_nodes.python_science_nodes import networkx_graph_ops

        nx_mock = _make_networkx_mock()
        with patch.dict(sys.modules, {"networkx": nx_mock}):
            result = networkx_graph_ops(input=EDGE_LIST, operation="connected_components")
            assert "components" in result
            assert result["count"] == 2

    def test_empty_input_raises(self):
        from nodyra_nodes.python_science_nodes import networkx_graph_ops

        nx_mock = _make_networkx_mock()
        nx_mock.Graph.return_value.number_of_nodes.return_value = 0
        nx_mock.DiGraph.return_value.number_of_nodes.return_value = 0
        with patch.dict(sys.modules, {"networkx": nx_mock}):
            with pytest.raises(ValueError, match="could not build graph"):
                networkx_graph_ops(input=[], operation="shortest_path")

    def test_shortest_path_no_source_raises(self):
        from nodyra_nodes.python_science_nodes import networkx_graph_ops

        nx_mock = _make_networkx_mock()
        with patch.dict(sys.modules, {"networkx": nx_mock}):
            with pytest.raises(ValueError, match="source is required"):
                networkx_graph_ops(input=EDGE_LIST, operation="shortest_path", source="")

    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "networkx", None)
        from nodyra_nodes.python_science_nodes import networkx_graph_ops

        with pytest.raises(ImportError, match="networkx"):
            networkx_graph_ops(input=EDGE_LIST, operation="shortest_path")


# ---------------------------------------------------------------------------
# Task 38 — beautifulsoup_scrape
# ---------------------------------------------------------------------------

SAMPLE_HTML = """
<html><head><title>Test Page</title></head>
<body>
  <h1>Hello World</h1>
  <p class="price"><span>$9.99</span></p>
  <ul>
    <li><a href="/page/1">Link 1</a></li>
    <li><a href="/page/2">Link 2</a></li>
  </ul>
</body></html>
"""


class TestBeautifulSoupScrape:
    def test_no_selectors_returns_text(self):
        from nodyra_nodes.python_science_nodes import beautifulsoup_scrape

        result = beautifulsoup_scrape(input=SAMPLE_HTML, selectors=None)
        assert "text" in result
        assert "Hello World" in result["text"]

    def test_css_selector_text(self):
        from nodyra_nodes.python_science_nodes import beautifulsoup_scrape

        result = beautifulsoup_scrape(
            input=SAMPLE_HTML,
            selectors={"heading": "h1", "price": ".price span"},
            extract="text",
        )
        assert result["data"]["heading"] == "Hello World"
        assert result["data"]["price"] == "$9.99"

    def test_multiple_matches(self):
        from nodyra_nodes.python_science_nodes import beautifulsoup_scrape

        result = beautifulsoup_scrape(
            input=SAMPLE_HTML,
            selectors={"links": "a"},
            extract="text",
            multiple=True,
        )
        assert len(result["data"]["links"]) == 2

    def test_extract_attribute(self):
        from nodyra_nodes.python_science_nodes import beautifulsoup_scrape

        result = beautifulsoup_scrape(
            input=SAMPLE_HTML,
            selectors={"first_link": "a"},
            extract="attribute",
            attribute_name="href",
        )
        assert result["data"]["first_link"] == "/page/1"

    def test_missing_element_returns_none(self):
        from nodyra_nodes.python_science_nodes import beautifulsoup_scrape

        result = beautifulsoup_scrape(
            input=SAMPLE_HTML,
            selectors={"nope": ".nonexistent"},
            extract="text",
        )
        assert result["data"]["nope"] is None

    def test_no_input_raises(self):
        from nodyra_nodes.python_science_nodes import beautifulsoup_scrape

        with pytest.raises(ValueError, match="html input or url is required"):
            beautifulsoup_scrape(input=None, url="")

    def test_url_ssrf_blocked(self):
        from nodyra_nodes.python_science_nodes import beautifulsoup_scrape

        with pytest.raises(ValueError):
            beautifulsoup_scrape(input=None, url="http://169.254.169.254/metadata")


# ---------------------------------------------------------------------------
# Task 39 — sympy_math
# ---------------------------------------------------------------------------


class TestSymPyMath:
    def test_solve_quadratic(self):
        pytest.importorskip("sympy")
        from nodyra_nodes.python_science_nodes import sympy_math

        result = sympy_math(expression="x**2 - 4", operation="solve", variable="x")
        solutions = set(result["solutions"])
        assert "-2" in solutions
        assert "2" in solutions

    def test_simplify(self):
        pytest.importorskip("sympy")
        from nodyra_nodes.python_science_nodes import sympy_math

        result = sympy_math(
            expression="(x + 1)**2 - x**2 - 2*x - 1", operation="simplify", variable="x"
        )
        assert result["result"] == "0"

    def test_expand(self):
        pytest.importorskip("sympy")
        from nodyra_nodes.python_science_nodes import sympy_math

        result = sympy_math(expression="(x + 1)**2", operation="expand", variable="x")
        # x**2 + 2*x + 1
        assert "x**2" in result["result"]

    def test_factor(self):
        pytest.importorskip("sympy")
        from nodyra_nodes.python_science_nodes import sympy_math

        result = sympy_math(expression="x**2 - 1", operation="factor", variable="x")
        assert "(x - 1)" in result["result"]
        assert "(x + 1)" in result["result"]

    def test_diff(self):
        pytest.importorskip("sympy")
        from nodyra_nodes.python_science_nodes import sympy_math

        result = sympy_math(expression="x**3", operation="diff", variable="x", order=1)
        assert "3*x**2" in result["result"]

    def test_integrate(self):
        pytest.importorskip("sympy")
        from nodyra_nodes.python_science_nodes import sympy_math

        result = sympy_math(expression="x**2", operation="integrate", variable="x")
        assert "x**3" in result["result"]

    def test_limit(self):
        pytest.importorskip("sympy")
        from nodyra_nodes.python_science_nodes import sympy_math

        result = sympy_math(expression="sin(x)/x", operation="limit", variable="x", at_value="0")
        assert result["result"] == "1"

    def test_latex(self):
        pytest.importorskip("sympy")
        from nodyra_nodes.python_science_nodes import sympy_math

        result = sympy_math(expression="x**2 + 1", operation="latex", variable="x")
        assert "x^{2}" in result["latex"] or "x^2" in result["latex"]

    def test_matrix_ops(self):
        pytest.importorskip("sympy")
        from nodyra_nodes.python_science_nodes import sympy_math

        result = sympy_math(
            input=[[1, 2], [3, 4]],
            operation="matrix_ops",
        )
        assert result["shape"] == [2, 2]
        assert result["det"] == "-2" or result["det"] == "-2.0"

    def test_bad_expression_raises(self):
        pytest.importorskip("sympy")
        from nodyra_nodes.python_science_nodes import sympy_math

        with pytest.raises(ValueError, match="could not parse"):
            sympy_math(expression=">>INVALID<<", operation="solve")

    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sympy", None)
        from nodyra_nodes.python_science_nodes import sympy_math

        with pytest.raises(ImportError, match="sympy"):
            sympy_math(expression="x**2", operation="solve")
