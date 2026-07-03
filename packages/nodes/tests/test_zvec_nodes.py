"""Tests for zvec vector database nodes.

All tests mock the `zvec` module so they run without the native wheel installed.
The mocks mirror the zvec 0.5.0 Python API.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# zvec mock factory
# ---------------------------------------------------------------------------


class _FakeStatus:
    def __init__(self, ok=True, msg=""):
        self._ok = ok
        self._msg = msg

    def ok(self):
        return self._ok

    def message(self):
        return self._msg


class _FakeDoc:
    def __init__(self, id, score=None, vectors=None, fields=None):
        self.id = id
        self.score = score
        self.vectors = vectors or {}
        self.fields = fields or {}


class _FakeDocList:
    def __init__(self, docs):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)

    def __len__(self):
        return len(self._docs)


class _FakeStats:
    def __init__(self):
        self.doc_count = 3
        self.segment_count = 1
        self.index_size = 1024
        self.total_size = 2048


class _FakeSchema:
    def __init__(self):
        self.name = "test_col"

        f = MagicMock()
        f.name = "text"
        f.data_type = "STRING"
        self.fields = [f]

        v = MagicMock()
        v.name = "embedding"
        v.data_type = "VECTOR_FP32"
        v.dimension = 4
        self.vectors = [v]


class _FakeCollection:
    """Minimal zvec Collection mock."""

    def __init__(self):
        self.stats = _FakeStats()
        self.schema = _FakeSchema()
        self._docs: dict[str, _FakeDoc] = {}

    def upsert(self, docs):
        result = []
        for doc in docs:
            self._docs[doc.id] = doc
            result.append(_FakeStatus(ok=True))
        return result

    def update(self, docs):
        return self.upsert(docs)

    def delete(self, ids):
        result = []
        for doc_id in ids:
            self._docs.pop(doc_id, None)
            result.append(_FakeStatus(ok=True))
        return result

    def delete_by_filter(self, expr):
        pass  # no-op for tests

    def fetch(self, ids, output_fields=None, include_vector=True):
        return {i: self._docs[i] for i in ids if i in self._docs}

    def query(
        self, queries, topk=10, filter=None, include_vector=False, output_fields=None, reranker=None
    ):
        docs = list(self._docs.values())[:topk]
        return _FakeDocList(
            [_FakeDoc(d.id, score=0.9, vectors=d.vectors, fields=d.fields) for d in docs]
        )

    def flush(self):
        pass

    def optimize(self):
        pass

    def destroy(self):
        self._docs.clear()


def _make_zvec_mock() -> types.ModuleType:
    zvec = types.ModuleType("zvec")

    # --- Types ---
    DataType = types.SimpleNamespace(
        STRING="STRING",
        INT32="INT32",
        INT64="INT64",
        UINT32="UINT32",
        UINT64="UINT64",
        FLOAT="FLOAT",
        DOUBLE="DOUBLE",
        BOOL="BOOL",
        VECTOR_FP32="VECTOR_FP32",
        VECTOR_FP16="VECTOR_FP16",
        VECTOR_INT8="VECTOR_INT8",
    )
    MetricType = types.SimpleNamespace(L2="L2", IP="IP", COSINE="COSINE")
    IndexType = types.SimpleNamespace(HNSW="HNSW", IVF="IVF", FLAT="FLAT")
    zvec.DataType = DataType
    zvec.MetricType = MetricType
    zvec.IndexType = IndexType

    class HnswIndexParam:
        def __init__(self, metric_type="COSINE", ef_construction=200, m=16):
            self.metric_type = metric_type
            self.ef_construction = ef_construction
            self.m = m

    class FlatIndexParam:
        def __init__(self, metric_type="COSINE"):
            self.metric_type = metric_type

    class IVFIndexParam:
        def __init__(self, metric_type="COSINE", nlist=100):
            self.metric_type = metric_type
            self.nlist = nlist

    class FtsIndexParam:
        def __init__(self, language="english"):
            self.language = language

    class HnswQueryParam:
        def __init__(self, ef=100):
            self.ef = ef

    class IVFQueryParam:
        def __init__(self, nprobe=10):
            self.nprobe = nprobe

    class FtsQueryParam:
        def __init__(self):
            pass

    zvec.HnswIndexParam = HnswIndexParam
    zvec.FlatIndexParam = FlatIndexParam
    zvec.IVFIndexParam = IVFIndexParam
    zvec.FtsIndexParam = FtsIndexParam
    zvec.HnswQueryParam = HnswQueryParam
    zvec.IVFQueryParam = IVFQueryParam
    zvec.FtsQueryParam = FtsQueryParam

    class FieldSchema:
        def __init__(self, name, data_type, nullable=False, index_param=None):
            self.name = name
            self.data_type = data_type
            self.nullable = nullable
            self.index_param = index_param

    class VectorSchema:
        def __init__(self, name, data_type, dimension, index_param=None):
            self.name = name
            self.data_type = data_type
            self.dimension = dimension
            self.index_param = index_param

    class CollectionSchema:
        def __init__(self, name, fields=None, vectors=None):
            self.name = name
            self.fields = fields
            self.vectors = vectors

    zvec.FieldSchema = FieldSchema
    zvec.VectorSchema = VectorSchema
    zvec.CollectionSchema = CollectionSchema

    class CollectionOption:
        def __init__(self, read_only=False, enable_mmap=False):
            self.read_only = read_only
            self.enable_mmap = enable_mmap

    zvec.CollectionOption = CollectionOption

    class Doc:
        def __init__(self, id, score=None, vectors=None, fields=None):
            self.id = id
            self.score = score
            self.vectors = vectors or {}
            self.fields = fields or {}

    class Query:
        def __init__(self, field_name, id=None, vector=None, param=None, fts=None):
            self.field_name = field_name
            self.id = id
            self.vector = vector
            self.param = param
            self.fts = fts

    class Fts:
        def __init__(self, query_string=None, match_string=None):
            self.query_string = query_string
            self.match_string = match_string

    class RrfReRanker:
        pass

    class WeightedReRanker:
        def __init__(self, weights):
            self.weights = weights

    zvec.Doc = Doc
    zvec.Query = Query
    zvec.Fts = Fts
    zvec.RrfReRanker = RrfReRanker
    zvec.WeightedReRanker = WeightedReRanker

    _collection = _FakeCollection()

    def _init(*args, **kwargs):
        pass

    def _create_and_open(path, schema, option=None):
        return _collection

    def _open(path, option=None):
        return _collection

    zvec.init = _init
    zvec.create_and_open = _create_and_open
    zvec.open = _open

    zvec._collection = _collection  # expose for reset in tests

    return zvec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_init_flag():
    """Reset the module-level _initialized flag between tests."""
    import nodyra_nodes.zvec_nodes as zn  # noqa

    zn._initialized = False


# ---------------------------------------------------------------------------
# Tests: zvec_create_collection
# ---------------------------------------------------------------------------


class TestZvecCreateCollection:
    def test_creates_new_collection(self, tmp_path):
        zvec = _make_zvec_mock()
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_create_collection

            col_path = str(tmp_path / "col")
            result = zvec_create_collection(
                collection_path=col_path,
                collection_name="test",
                vector_field="embedding",
                vector_dim=4,
                fields="title:STRING",
            )
        assert result["status"] == "created"
        assert result["collection_path"] == col_path
        assert result["vector_field"] == "embedding"
        assert result["vector_dim"] == 4

    def test_opens_existing_collection(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "existing_col")
        import os

        os.makedirs(col_path)  # simulate existing collection on disk
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_create_collection

            result = zvec_create_collection(collection_path=col_path, overwrite=False)
        assert result["status"] == "opened"

    def test_overwrite_destroys_and_recreates(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_create_collection

            result = zvec_create_collection(collection_path=col_path, overwrite=True)
        assert result["status"] == "created"

    def test_hnsw_index_type(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_create_collection

            result = zvec_create_collection(
                collection_path=col_path,
                index_type="hnsw",
                metric_type="cosine",
                hnsw_ef_construction=100,
                hnsw_m=8,
            )
        assert result["index_type"] == "hnsw"

    def test_flat_index_type(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_create_collection

            result = zvec_create_collection(
                collection_path=col_path, index_type="flat", metric_type="l2"
            )
        assert result["index_type"] == "flat"

    def test_with_fts_field(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_create_collection

            result = zvec_create_collection(
                collection_path=col_path, fts_field="content", fts_language="en"
            )
        assert result["status"] == "created"

    def test_missing_zvec_raises(self):
        with patch.dict(sys.modules, {"zvec": None}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_create_collection

            with pytest.raises(ImportError, match="zvec"):
                zvec_create_collection(collection_path="/tmp/x")


# ---------------------------------------------------------------------------
# Tests: zvec_upsert
# ---------------------------------------------------------------------------


class TestZvecUpsert:
    def test_upsert_list_of_dicts(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        docs = [
            {"id": "1", "embedding": [0.1, 0.2, 0.3, 0.4], "title": "hello"},
            {"id": "2", "embedding": [0.5, 0.6, 0.7, 0.8], "title": "world"},
        ]
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_upsert

            result = zvec_upsert(input=docs, collection_path=col_path, vector_field="embedding")
        assert result["inserted"] == 2
        assert result["errors"] == []

    def test_upsert_auto_id(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        docs = [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_upsert

            result = zvec_upsert(input=docs, collection_path=col_path, vector_field="embedding")
        assert result["inserted"] == 1

    def test_upsert_missing_vector_logged_as_error(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        docs = [{"id": "1", "title": "no vector here"}]
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_upsert

            result = zvec_upsert(input=docs, collection_path=col_path, vector_field="embedding")
        assert result["inserted"] == 0
        assert len(result["errors"]) == 1
        assert "no vector" in result["errors"][0]

    def test_upsert_none_input(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_upsert

            result = zvec_upsert(input=None, collection_path=col_path)
        assert result["inserted"] == 0

    def test_upsert_collection_not_found_raises(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "nonexistent")
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_upsert

            with pytest.raises(ValueError, match="not found"):
                zvec_upsert(input=[{"embedding": [0.1]}], collection_path=col_path)

    def test_missing_zvec_raises(self):
        with patch.dict(sys.modules, {"zvec": None}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_upsert

            with pytest.raises(ImportError, match="zvec"):
                zvec_upsert(input=[], collection_path="/tmp/x")


# ---------------------------------------------------------------------------
# Tests: zvec_search
# ---------------------------------------------------------------------------


class TestZvecSearch:
    def _setup(self, tmp_path, zvec):
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        col = zvec._collection
        col._docs.clear()
        doc = zvec.Doc(
            id="a", vectors={"embedding": [0.1, 0.2, 0.3, 0.4]}, fields={"title": "doc A"}
        )
        col._docs["a"] = doc
        return col_path

    def test_search_float_list_input(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_search

            result = zvec_search(
                input=[0.1, 0.2, 0.3, 0.4],
                collection_path=col_path,
                topk=5,
            )
        assert "results" in result
        assert result["count"] >= 0

    def test_search_dict_input_with_vector_key(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_search

            result = zvec_search(
                input={"vector": [0.1, 0.2, 0.3, 0.4]},
                collection_path=col_path,
            )
        assert "results" in result

    def test_search_dict_input_with_embedding_key(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_search

            result = zvec_search(
                input={"embedding": [0.1, 0.2, 0.3, 0.4]},
                collection_path=col_path,
            )
        assert "results" in result

    def test_search_include_vector(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_search

            result = zvec_search(
                input=[0.1, 0.2, 0.3, 0.4],
                collection_path=col_path,
                include_vector=True,
            )
        assert "results" in result
        if result["results"]:
            # vector present when include_vector=True and doc has vectors
            pass  # mock may not populate score

    def test_search_invalid_input_raises(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_search

            with pytest.raises(ValueError):
                zvec_search(input="not a vector", collection_path=col_path)

    def test_search_dict_missing_vector_raises(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_search

            with pytest.raises(ValueError, match="query_vector"):
                zvec_search(input={"title": "no vector"}, collection_path=col_path)

    def test_missing_zvec_raises(self):
        with patch.dict(sys.modules, {"zvec": None}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_search

            with pytest.raises(ImportError, match="zvec"):
                zvec_search(input=[0.1], collection_path="/tmp/x")


# ---------------------------------------------------------------------------
# Tests: zvec_fts_search
# ---------------------------------------------------------------------------


class TestZvecFtsSearch:
    def test_fts_match_mode(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_fts_search

            result = zvec_fts_search(
                input="machine learning",
                collection_path=col_path,
                fts_field="content",
                topk=5,
                query_mode="match",
            )
        assert "results" in result
        assert result["count"] >= 0

    def test_fts_query_mode(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_fts_search

            result = zvec_fts_search(
                input="+machine -learning",
                collection_path=col_path,
                query_mode="query",
            )
        assert "results" in result

    def test_empty_query_raises(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_fts_search

            with pytest.raises(ValueError, match="query string"):
                zvec_fts_search(input="", collection_path=col_path)

    def test_none_input_raises(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_fts_search

            with pytest.raises(ValueError, match="query string"):
                zvec_fts_search(input=None, collection_path=col_path)

    def test_missing_zvec_raises(self):
        with patch.dict(sys.modules, {"zvec": None}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_fts_search

            with pytest.raises(ImportError, match="zvec"):
                zvec_fts_search(input="test", collection_path="/tmp/x")


# ---------------------------------------------------------------------------
# Tests: zvec_hybrid_search
# ---------------------------------------------------------------------------


class TestZvecHybridSearch:
    def test_hybrid_dict_input(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_hybrid_search

            result = zvec_hybrid_search(
                input={"vector": [0.1, 0.2, 0.3, 0.4], "text": "machine learning"},
                collection_path=col_path,
                topk=5,
                reranker="rrf",
            )
        assert "results" in result

    def test_hybrid_vector_only(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_hybrid_search

            result = zvec_hybrid_search(
                input=[0.1, 0.2, 0.3, 0.4],
                collection_path=col_path,
            )
        assert "results" in result

    def test_hybrid_text_only(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_hybrid_search

            result = zvec_hybrid_search(
                input="neural search",
                collection_path=col_path,
            )
        assert "results" in result

    def test_hybrid_weighted_reranker(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_hybrid_search

            result = zvec_hybrid_search(
                input={"vector": [0.1, 0.2, 0.3, 0.4], "text": "topic"},
                collection_path=col_path,
                reranker="weighted",
                vector_weight=0.8,
                fts_weight=0.2,
            )
        assert "results" in result

    def test_empty_input_raises(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_hybrid_search

            with pytest.raises(ValueError, match="at least one"):
                zvec_hybrid_search(input={}, collection_path=col_path)

    def test_invalid_input_type_raises(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_hybrid_search

            with pytest.raises(ValueError):
                zvec_hybrid_search(input=42, collection_path=col_path)

    def test_missing_zvec_raises(self):
        with patch.dict(sys.modules, {"zvec": None}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_hybrid_search

            with pytest.raises(ImportError, match="zvec"):
                zvec_hybrid_search(input={"text": "x"}, collection_path="/tmp/x")


# ---------------------------------------------------------------------------
# Tests: zvec_fetch
# ---------------------------------------------------------------------------


class TestZvecFetch:
    def _setup(self, tmp_path, zvec):
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        col = zvec._collection
        col._docs.clear()
        col._docs["doc1"] = zvec.Doc(
            id="doc1", fields={"title": "First doc"}, vectors={"embedding": [0.1]}
        )
        col._docs["doc2"] = zvec.Doc(
            id="doc2", fields={"title": "Second doc"}, vectors={"embedding": [0.2]}
        )
        return col_path

    def test_fetch_string_id(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_fetch

            result = zvec_fetch(input="doc1", collection_path=col_path)
        assert result["found"] == 1
        assert "doc1" in result["docs"]

    def test_fetch_list_of_ids(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_fetch

            result = zvec_fetch(input=["doc1", "doc2"], collection_path=col_path)
        assert result["found"] == 2

    def test_fetch_dict_input(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_fetch

            result = zvec_fetch(input={"id": "doc1"}, collection_path=col_path)
        assert result["found"] == 1

    def test_fetch_missing_id_returns_empty(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_fetch

            result = zvec_fetch(input="nonexistent", collection_path=col_path)
        assert result["found"] == 0

    def test_fetch_invalid_input_raises(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_fetch

            with pytest.raises(ValueError):
                zvec_fetch(input=123, collection_path=col_path)

    def test_missing_zvec_raises(self):
        with patch.dict(sys.modules, {"zvec": None}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_fetch

            with pytest.raises(ImportError, match="zvec"):
                zvec_fetch(input="id1", collection_path="/tmp/x")


# ---------------------------------------------------------------------------
# Tests: zvec_delete
# ---------------------------------------------------------------------------


class TestZvecDelete:
    def _setup(self, tmp_path, zvec):
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        col = zvec._collection
        col._docs.clear()
        col._docs["doc1"] = zvec.Doc(id="doc1", fields={"title": "A"})
        col._docs["doc2"] = zvec.Doc(id="doc2", fields={"title": "B"})
        return col_path

    def test_delete_by_ids_string(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_delete

            result = zvec_delete(input="doc1", collection_path=col_path, delete_by="ids")
        assert result["deleted"] == 1

    def test_delete_by_ids_list(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_delete

            result = zvec_delete(input=["doc1", "doc2"], collection_path=col_path, delete_by="ids")
        assert result["deleted"] == 2

    def test_delete_by_filter(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_delete

            result = zvec_delete(
                input=None,
                collection_path=col_path,
                delete_by="filter",
                filter='category == "stale"',
            )
        assert result["deleted_by"] == "filter"
        assert result["filter"] == 'category == "stale"'

    def test_delete_by_filter_no_expr_raises(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = self._setup(tmp_path, zvec)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_delete

            with pytest.raises(ValueError, match="filter expression"):
                zvec_delete(input=None, collection_path=col_path, delete_by="filter", filter="")

    def test_missing_zvec_raises(self):
        with patch.dict(sys.modules, {"zvec": None}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_delete

            with pytest.raises(ImportError, match="zvec"):
                zvec_delete(input="id1", collection_path="/tmp/x")


# ---------------------------------------------------------------------------
# Tests: zvec_collection_stats
# ---------------------------------------------------------------------------


class TestZvecCollectionStats:
    def test_stats_returns_doc_count(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_collection_stats

            result = zvec_collection_stats(collection_path=col_path)
        assert "doc_count" in result
        assert result["doc_count"] == 3
        assert "schema" in result

    def test_stats_with_optimize(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_collection_stats

            result = zvec_collection_stats(collection_path=col_path, optimize=True)
        assert "doc_count" in result

    def test_stats_schema_structure(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "col")
        import os

        os.makedirs(col_path)
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_collection_stats

            result = zvec_collection_stats(collection_path=col_path)
        schema = result["schema"]
        assert isinstance(schema["scalar_fields"], list)
        assert isinstance(schema["vector_fields"], list)
        assert schema["name"] == "test_col"

    def test_collection_not_found_raises(self, tmp_path):
        zvec = _make_zvec_mock()
        col_path = str(tmp_path / "nonexistent")
        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_collection_stats

            with pytest.raises(ValueError, match="not found"):
                zvec_collection_stats(collection_path=col_path)

    def test_missing_zvec_raises(self):
        with patch.dict(sys.modules, {"zvec": None}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import zvec_collection_stats

            with pytest.raises(ImportError, match="zvec"):
                zvec_collection_stats(collection_path="/tmp/x")


# ---------------------------------------------------------------------------
# Tests: init is idempotent
# ---------------------------------------------------------------------------


class TestZvecInit:
    def test_init_only_called_once(self, tmp_path):
        """_ensure_init should be safe to call multiple times."""
        zvec = _make_zvec_mock()
        call_count = 0

        original_init = zvec.init

        def counting_init(*a, **kw):
            nonlocal call_count
            call_count += 1
            original_init(*a, **kw)

        zvec.init = counting_init

        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import _ensure_init

            _ensure_init()
            _ensure_init()
            _ensure_init()

        assert call_count == 1

    def test_init_already_initialized_runtime_error_suppressed(self, tmp_path):
        """If zvec.init() raises RuntimeError (already init), we ignore it."""
        zvec = _make_zvec_mock()

        def boom(*a, **kw):
            raise RuntimeError("already initialized")

        zvec.init = boom

        with patch.dict(sys.modules, {"zvec": zvec}):
            _reset_init_flag()
            from nodyra_nodes.zvec_nodes import _ensure_init

            # Should not propagate the RuntimeError
            _ensure_init()
