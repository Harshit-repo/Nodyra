"""zvec Vector Database Nodes.

Provides 8 nodes for working with zvec — a high-performance on-disk vector
database with HNSW/IVF/Flat ANN, full-text search, and hybrid search.

Collection lifecycle:
  zvec_create_collection → zvec_upsert / zvec_search / zvec_fts_search
                         / zvec_hybrid_search / zvec_fetch / zvec_delete
                         / zvec_collection_stats

All nodes accept a `collection_path` param (path on disk to the zvec
directory) so they can be used independently without wiring a supplier.
`zvec.init()` is called once per process the first time any node runs.
"""

from __future__ import annotations

import threading
from typing import Any

from nodyra.sdk import node

ZVEC_CATEGORY = "Vector DB"

# ---------------------------------------------------------------------------
# zvec.init() — called once
# ---------------------------------------------------------------------------

_init_lock = threading.Lock()
_initialized = False


def _ensure_init() -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        import zvec  # noqa: PLC0415

        try:
            zvec.init()
        except RuntimeError:
            pass  # already initialised in this process
        _initialized = True


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DTYPE_MAP = {
    "STRING": "STRING",
    "INT32": "INT32",
    "INT64": "INT64",
    "UINT32": "UINT32",
    "UINT64": "UINT64",
    "FLOAT": "FLOAT",
    "DOUBLE": "DOUBLE",
    "BOOL": "BOOL",
}

_METRIC_MAP = {
    "l2": "L2",
    "ip": "IP",
    "cosine": "COSINE",
}

_INDEX_MAP = {
    "hnsw": "HnswIndexParam",
    "ivf": "IVFIndexParam",
    "flat": "FlatIndexParam",
}


def _parse_fields(fields_spec: str, zvec: Any) -> list[Any]:
    """Parse 'name:TYPE, name2:TYPE2' into FieldSchema list."""
    result = []
    for part in fields_spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            fname, ftype = [x.strip() for x in part.split(":", 1)]
        else:
            fname, ftype = part, "STRING"
        dtype = getattr(zvec.DataType, _DTYPE_MAP.get(ftype.upper(), "STRING"))
        result.append(zvec.FieldSchema(fname, dtype))
    return result


def _build_index_param(
    index_type: str, metric: str, ef_construction: int, m: int, nlist: int, zvec: Any
) -> Any:
    mt = getattr(zvec.MetricType, _METRIC_MAP.get(metric.lower(), "L2"))
    if index_type == "hnsw":
        return zvec.HnswIndexParam(metric_type=mt, ef_construction=ef_construction, m=m)
    if index_type == "ivf":
        return zvec.IVFIndexParam(metric_type=mt, nlist=nlist)
    return zvec.FlatIndexParam(metric_type=mt)


def _open_collection(path: str, zvec: Any, read_only: bool = False) -> Any:
    import os  # noqa: PLC0415

    if not os.path.exists(path):
        raise ValueError(
            f"zvec collection not found at '{path}'. Create it with zvec_create_collection first."
        )
    return zvec.open(path, option=zvec.CollectionOption(read_only=read_only))


def _doc_to_dict(doc: Any, include_vector: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"id": doc.id}
    if doc.score is not None:
        result["score"] = float(doc.score)
    if doc.fields:
        result.update(doc.fields)
    if include_vector and doc.vectors:
        result["_vectors"] = {k: list(v) for k, v in doc.vectors.items()}
    return result


# ---------------------------------------------------------------------------
# Node 1 — Create / Open Collection
# ---------------------------------------------------------------------------


@node(
    name="zvec Create Collection",
    id="zvec_create_collection",
    category=ZVEC_CATEGORY,
    role="executable",
    icon="database",
    requirements=["zvec>=0.4"],
    inputs=["main"],
    outputs=["main"],
    param_groups={
        "Vector Field": ["vector_field", "vector_dim", "vector_dtype", "index_type", "metric_type"],
        "Index Tuning": ["hnsw_ef_construction", "hnsw_m", "ivf_nlist"],
        "Scalar Fields": ["fields"],
        "FTS": ["fts_field", "fts_language"],
    },
    params={
        "collection_path": {
            "placeholder": "./my_collection",
            "description": "Directory path to store the collection.",
        },
        "collection_name": {
            "placeholder": "my_collection",
            "description": "Logical name of the collection.",
        },
        "vector_field": {"description": "Vector field name.", "group": "Vector Field"},
        "vector_dim": {
            "description": "Vector dimensionality (e.g. 1536 for OpenAI ada-002).",
            "group": "Vector Field",
        },
        "vector_dtype": {
            "choices": ["VECTOR_FP32", "VECTOR_FP16", "VECTOR_INT8"],
            "description": "Vector storage type.",
            "group": "Vector Field",
        },
        "index_type": {
            "choices": ["hnsw", "ivf", "flat"],
            "description": "ANN index type. hnsw is best for most cases.",
            "group": "Vector Field",
        },
        "metric_type": {
            "choices": ["l2", "ip", "cosine"],
            "description": "Distance metric.",
            "group": "Vector Field",
        },
        "hnsw_ef_construction": {
            "description": "[hnsw] ef_construction quality parameter (default 200).",
            "group": "Index Tuning",
        },
        "hnsw_m": {
            "description": "[hnsw] M connections per node (default 16).",
            "group": "Index Tuning",
        },
        "ivf_nlist": {
            "description": "[ivf] Number of clusters (default 100).",
            "group": "Index Tuning",
        },
        "fields": {
            "placeholder": "title:STRING, category:STRING, timestamp:INT64",
            "description": "Scalar fields as name:TYPE pairs. Types: STRING INT32 INT64 FLOAT DOUBLE BOOL.",
            "group": "Scalar Fields",
        },
        "fts_field": {
            "placeholder": "content",
            "description": "Field name for full-text search index (leave empty to skip).",
            "group": "FTS",
        },
        "fts_language": {
            "choices": ["en", "zh", "auto"],
            "description": "FTS tokenizer language.",
            "group": "FTS",
        },
        "overwrite": {"description": "Destroy and recreate collection if it already exists."},
    },
)
def zvec_create_collection(
    input: Any = None,
    collection_path: str = "./zvec_collection",
    collection_name: str = "collection",
    vector_field: str = "embedding",
    vector_dim: int = 1536,
    vector_dtype: str = "VECTOR_FP32",
    index_type: str = "hnsw",
    metric_type: str = "cosine",
    hnsw_ef_construction: int = 200,
    hnsw_m: int = 16,
    ivf_nlist: int = 100,
    fields: str = "text:STRING",
    fts_field: str = "",
    fts_language: str = "en",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create or open a zvec vector collection on disk."""
    try:
        import zvec  # noqa: PLC0415
    except ImportError:
        raise ImportError(
            "zvec_create_collection requires zvec>=0.4. Install with: pip install zvec"
        )

    _ensure_init()
    import os  # noqa: PLC0415

    collection_path = collection_path or "./zvec_collection"
    name = collection_name or os.path.basename(collection_path) or "collection"

    # If collection already exists and overwrite=False, just open it
    if os.path.exists(collection_path) and not overwrite:
        col = zvec.open(collection_path)
        s = col.stats
        return {
            "collection_path": collection_path,
            "status": "opened",
            "doc_count": int(s.doc_count) if hasattr(s, "doc_count") else 0,
        }

    # Build schema
    scalar_fields = _parse_fields(fields, zvec)

    # FTS field
    if fts_field:
        lang_map = {"en": "english", "zh": "chinese", "auto": ""}
        fts_param = zvec.FtsIndexParam(language=lang_map.get(fts_language, "english"))
        scalar_fields.append(
            zvec.FieldSchema(fts_field, zvec.DataType.STRING, index_param=fts_param)
        )

    dtype = getattr(zvec.DataType, vector_dtype, zvec.DataType.VECTOR_FP32)
    idx_param = _build_index_param(
        index_type,
        metric_type,
        int(hnsw_ef_construction or 200),
        int(hnsw_m or 16),
        int(ivf_nlist or 100),
        zvec,
    )
    vec_schema = zvec.VectorSchema(
        name=vector_field,
        data_type=dtype,
        dimension=int(vector_dim),
        index_param=idx_param,
    )
    schema = zvec.CollectionSchema(name=name, fields=scalar_fields or None, vectors=vec_schema)

    if os.path.exists(collection_path) and overwrite:
        import shutil  # noqa: PLC0415

        shutil.rmtree(collection_path)

    col = zvec.create_and_open(collection_path, schema)

    return {
        "collection_path": collection_path,
        "status": "created",
        "vector_field": vector_field,
        "vector_dim": vector_dim,
        "index_type": index_type,
        "metric_type": metric_type,
        "doc_count": 0,
    }


# ---------------------------------------------------------------------------
# Node 2 — Upsert Documents
# ---------------------------------------------------------------------------


@node(
    name="zvec Upsert",
    id="zvec_upsert",
    category=ZVEC_CATEGORY,
    role="executable",
    icon="upload",
    requirements=["zvec>=0.4"],
    inputs=["main"],
    outputs=["main"],
    params={
        "collection_path": {
            "placeholder": "./my_collection",
            "description": "Path to the zvec collection.",
        },
        "vector_field": {"description": "Name of the vector field."},
        "id_field": {
            "placeholder": "id",
            "description": "Field in input docs to use as the document ID. Auto-generated if empty.",
        },
        "batch_size": {"description": "Upsert batch size (default 500)."},
        "flush_after": {"description": "Flush to disk after upsert."},
    },
)
def zvec_upsert(
    input: Any = None,
    collection_path: str = "./zvec_collection",
    vector_field: str = "embedding",
    id_field: str = "id",
    batch_size: int = 500,
    flush_after: bool = True,
) -> dict[str, Any]:
    """Insert or update documents in a zvec collection."""
    try:
        import zvec  # noqa: PLC0415
    except ImportError:
        raise ImportError("zvec_upsert requires zvec>=0.4. Install with: pip install zvec")

    _ensure_init()
    col = _open_collection(collection_path, zvec)

    if input is None:
        return {"inserted": 0, "errors": [], "collection_path": collection_path}

    rows = input if isinstance(input, list) else [input]
    inserted = 0
    errors: list[str] = []

    import uuid  # noqa: PLC0415

    batch: list[Any] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"row {i}: expected dict, got {type(row).__name__}")
            continue

        doc_id = str(row.get(id_field) or uuid.uuid4())
        vectors = row.get(vector_field) or row.get("vector") or row.get("embedding")
        if vectors is None:
            errors.append(f"row {i} (id={doc_id}): no vector found at field '{vector_field}'")
            continue

        # Scalar fields = everything except the vector
        scalar = {
            k: v for k, v in row.items() if k not in (vector_field, "vector", "embedding", id_field)
        }

        doc = zvec.Doc(
            id=doc_id,
            vectors={vector_field: list(vectors)},
            fields=scalar or None,
        )
        batch.append(doc)

        if len(batch) >= int(batch_size or 500):
            statuses = col.upsert(batch)
            inserted += sum(1 for s in statuses if s.ok())
            errors.extend(
                f"id={b.id}: {s.message()}" for b, s in zip(batch, statuses, strict=False) if not s.ok()
            )
            batch = []

    if batch:
        statuses = col.upsert(batch)
        inserted += sum(1 for s in statuses if s.ok())
        errors.extend(f"id={b.id}: {s.message()}" for b, s in zip(batch, statuses, strict=False) if not s.ok())

    if flush_after:
        col.flush()

    return {"inserted": inserted, "errors": errors, "collection_path": collection_path}


# ---------------------------------------------------------------------------
# Node 3 — Vector Search
# ---------------------------------------------------------------------------


@node(
    name="zvec Search",
    id="zvec_search",
    category=ZVEC_CATEGORY,
    role="executable",
    icon="search",
    requirements=["zvec>=0.4"],
    inputs=["main"],
    outputs=["main"],
    param_groups={
        "Filtering": ["filter", "output_fields"],
        "Options": ["include_vector", "ef_search"],
    },
    params={
        "collection_path": {
            "placeholder": "./my_collection",
            "description": "Path to the zvec collection.",
        },
        "vector_field": {"description": "Vector field to search."},
        "topk": {"description": "Number of nearest neighbours to return."},
        "filter": {
            "placeholder": 'category == "news" && score > 0.5',
            "description": "Boolean filter expression.",
            "group": "Filtering",
        },
        "output_fields": {
            "placeholder": "title, category",
            "description": "Comma-separated scalar fields to include in results.",
            "group": "Filtering",
        },
        "include_vector": {"description": "Include raw vector in results.", "group": "Options"},
        "ef_search": {"description": "[hnsw] ef search quality (default 100).", "group": "Options"},
    },
)
def zvec_search(
    input: Any = None,
    collection_path: str = "./zvec_collection",
    vector_field: str = "embedding",
    topk: int = 10,
    filter: str = "",
    output_fields: str = "",
    include_vector: bool = False,
    ef_search: int = 100,
) -> dict[str, Any]:
    """Perform ANN vector similarity search in a zvec collection."""
    try:
        import zvec  # noqa: PLC0415
    except ImportError:
        raise ImportError("zvec_search requires zvec>=0.4. Install with: pip install zvec")

    _ensure_init()

    # Resolve query vector
    if isinstance(input, list) and all(isinstance(x, (int, float)) for x in input):
        query_vector = input
    elif isinstance(input, dict):
        query_vector = input.get("vector") or input.get("embedding") or input.get("query_vector")
        if query_vector is None:
            raise ValueError(
                "zvec_search: input dict must contain 'vector', 'embedding', or 'query_vector'"
            )
    else:
        raise ValueError(
            f"zvec_search: input must be a float list or dict with vector, got {type(input).__name__}"
        )

    col = _open_collection(collection_path, zvec, read_only=True)

    fields_list = (
        [f.strip() for f in output_fields.split(",") if f.strip()] if output_fields else None
    )

    try:
        hnsw_param = zvec.HnswQueryParam(ef=int(ef_search or 100))
    except Exception:
        hnsw_param = None

    query = zvec.Query(
        field_name=vector_field,
        vector=query_vector,
        param=hnsw_param,
    )

    results = col.query(
        queries=query,
        topk=int(topk or 10),
        filter=filter or None,
        include_vector=bool(include_vector),
        output_fields=fields_list,
    )

    hits = [_doc_to_dict(doc, bool(include_vector)) for doc in results]
    return {"results": hits, "count": len(hits), "collection_path": collection_path}


# ---------------------------------------------------------------------------
# Node 4 — Full-Text Search
# ---------------------------------------------------------------------------


@node(
    name="zvec FTS Search",
    id="zvec_fts_search",
    category=ZVEC_CATEGORY,
    role="executable",
    icon="file-text",
    requirements=["zvec>=0.4"],
    inputs=["main"],
    outputs=["main"],
    params={
        "collection_path": {
            "placeholder": "./my_collection",
            "description": "Path to the zvec collection.",
        },
        "fts_field": {"description": "Field with FTS index."},
        "topk": {"description": "Max number of results."},
        "query_mode": {
            "choices": ["match", "query"],
            "description": 'match = natural language; query = boolean (+term -exclude "phrase").',
        },
        "filter": {"placeholder": 'lang == "en"', "description": "Boolean scalar filter."},
        "output_fields": {
            "placeholder": "title, url",
            "description": "Fields to include in results.",
        },
    },
)
def zvec_fts_search(
    input: Any = None,
    collection_path: str = "./zvec_collection",
    fts_field: str = "content",
    topk: int = 10,
    query_mode: str = "match",
    filter: str = "",
    output_fields: str = "",
) -> dict[str, Any]:
    """Full-text search in a zvec collection."""
    try:
        import zvec  # noqa: PLC0415
    except ImportError:
        raise ImportError("zvec_fts_search requires zvec>=0.4. Install with: pip install zvec")

    _ensure_init()

    query_str = str(input) if input is not None else ""
    if not query_str:
        raise ValueError("zvec_fts_search: query string is required")

    col = _open_collection(collection_path, zvec, read_only=True)
    fields_list = (
        [f.strip() for f in output_fields.split(",") if f.strip()] if output_fields else None
    )

    fts = zvec.Fts(
        match_string=query_str if query_mode == "match" else None,
        query_string=query_str if query_mode == "query" else None,
    )
    query = zvec.Query(field_name=fts_field, fts=fts)

    results = col.query(
        queries=query,
        topk=int(topk or 10),
        filter=filter or None,
        include_vector=False,
        output_fields=fields_list,
    )

    hits = [_doc_to_dict(doc, False) for doc in results]
    return {"results": hits, "count": len(hits), "collection_path": collection_path}


# ---------------------------------------------------------------------------
# Node 5 — Hybrid Search (vector + FTS)
# ---------------------------------------------------------------------------


@node(
    name="zvec Hybrid Search",
    id="zvec_hybrid_search",
    category=ZVEC_CATEGORY,
    role="executable",
    icon="layers",
    requirements=["zvec>=0.4"],
    inputs=["main"],
    outputs=["main"],
    param_groups={
        "Vector": ["vector_field", "ef_search"],
        "FTS": ["fts_field", "fts_mode"],
        "Reranking": ["reranker", "vector_weight", "fts_weight"],
    },
    params={
        "collection_path": {
            "placeholder": "./my_collection",
            "description": "Path to the zvec collection.",
        },
        "topk": {"description": "Number of results to return."},
        "filter": {"placeholder": 'category == "news"', "description": "Boolean pre-filter."},
        "output_fields": {"description": "Fields to return."},
        "vector_field": {"description": "Vector field for ANN search.", "group": "Vector"},
        "ef_search": {"description": "[hnsw] ef quality param.", "group": "Vector"},
        "fts_field": {"description": "Field for FTS search.", "group": "FTS"},
        "fts_mode": {
            "choices": ["match", "query"],
            "description": "FTS query mode.",
            "group": "FTS",
        },
        "reranker": {
            "choices": ["rrf", "weighted"],
            "description": "How to merge vector + FTS scores.",
            "group": "Reranking",
        },
        "vector_weight": {
            "description": "[weighted] Weight for vector score (0-1).",
            "group": "Reranking",
        },
        "fts_weight": {
            "description": "[weighted] Weight for FTS score (0-1).",
            "group": "Reranking",
        },
    },
)
def zvec_hybrid_search(
    input: Any = None,
    collection_path: str = "./zvec_collection",
    topk: int = 10,
    filter: str = "",
    output_fields: str = "",
    vector_field: str = "embedding",
    ef_search: int = 100,
    fts_field: str = "content",
    fts_mode: str = "match",
    reranker: str = "rrf",
    vector_weight: float = 0.7,
    fts_weight: float = 0.3,
) -> dict[str, Any]:
    """Hybrid vector + full-text search with score fusion."""
    try:
        import zvec  # noqa: PLC0415
    except ImportError:
        raise ImportError("zvec_hybrid_search requires zvec>=0.4. Install with: pip install zvec")

    _ensure_init()

    # Input: dict with "vector" (list[float]) and/or "text" (str)
    if isinstance(input, dict):
        query_vector = input.get("vector") or input.get("embedding")
        query_text = input.get("text") or input.get("query") or ""
    elif isinstance(input, list) and all(isinstance(x, (int, float)) for x in input):
        query_vector = input
        query_text = ""
    elif isinstance(input, str):
        query_vector = None
        query_text = input
    else:
        raise ValueError(
            "zvec_hybrid_search: input must be dict {vector, text}, float list, or string"
        )

    if not query_vector and not query_text:
        raise ValueError("zvec_hybrid_search: at least one of vector or text query is required")

    col = _open_collection(collection_path, zvec, read_only=True)
    fields_list = (
        [f.strip() for f in output_fields.split(",") if f.strip()] if output_fields else None
    )

    queries = []
    if query_vector:
        try:
            hnsw_param = zvec.HnswQueryParam(ef=int(ef_search or 100))
        except Exception:
            hnsw_param = None
        queries.append(zvec.Query(field_name=vector_field, vector=query_vector, param=hnsw_param))

    if query_text:
        fts = zvec.Fts(
            match_string=query_text if fts_mode == "match" else None,
            query_string=query_text if fts_mode == "query" else None,
        )
        queries.append(zvec.Query(field_name=fts_field, fts=fts))

    # Build reranker
    try:
        if reranker == "rrf":
            rr = zvec.RrfReRanker()
        else:
            rr = zvec.WeightedReRanker(
                weights=[float(vector_weight or 0.7), float(fts_weight or 0.3)]
            )
    except Exception:
        rr = None

    results = col.query(
        queries=queries,
        topk=int(topk or 10),
        filter=filter or None,
        include_vector=False,
        output_fields=fields_list,
        reranker=rr,
    )

    hits = [_doc_to_dict(doc, False) for doc in results]
    return {"results": hits, "count": len(hits), "collection_path": collection_path}


# ---------------------------------------------------------------------------
# Node 6 — Fetch by ID
# ---------------------------------------------------------------------------


@node(
    name="zvec Fetch",
    id="zvec_fetch",
    category=ZVEC_CATEGORY,
    role="executable",
    icon="download",
    requirements=["zvec>=0.4"],
    inputs=["main"],
    outputs=["main"],
    params={
        "collection_path": {
            "placeholder": "./my_collection",
            "description": "Path to the zvec collection.",
        },
        "include_vector": {"description": "Include raw vector data in results."},
        "output_fields": {"placeholder": "title, text", "description": "Scalar fields to return."},
    },
)
def zvec_fetch(
    input: Any = None,
    collection_path: str = "./zvec_collection",
    include_vector: bool = False,
    output_fields: str = "",
) -> dict[str, Any]:
    """Fetch documents by ID from a zvec collection."""
    try:
        import zvec  # noqa: PLC0415
    except ImportError:
        raise ImportError("zvec_fetch requires zvec>=0.4. Install with: pip install zvec")

    _ensure_init()

    if isinstance(input, str):
        ids = [input]
    elif isinstance(input, list):
        ids = [str(x) for x in input]
    elif isinstance(input, dict):
        ids = [str(input.get("id", ""))] if input.get("id") else []
    else:
        raise ValueError("zvec_fetch: input must be a string ID, list of IDs, or dict with 'id'")

    if not ids:
        return {"docs": {}, "found": 0, "collection_path": collection_path}

    fields_list = (
        [f.strip() for f in output_fields.split(",") if f.strip()] if output_fields else None
    )

    col = _open_collection(collection_path, zvec, read_only=True)
    docs = col.fetch(ids, output_fields=fields_list, include_vector=bool(include_vector))
    result = {doc_id: _doc_to_dict(doc, bool(include_vector)) for doc_id, doc in docs.items()}

    return {"docs": result, "found": len(result), "collection_path": collection_path}


# ---------------------------------------------------------------------------
# Node 7 — Delete Documents
# ---------------------------------------------------------------------------


@node(
    name="zvec Delete",
    id="zvec_delete",
    category=ZVEC_CATEGORY,
    role="executable",
    icon="trash-2",
    requirements=["zvec>=0.4"],
    inputs=["main"],
    outputs=["main"],
    params={
        "collection_path": {
            "placeholder": "./my_collection",
            "description": "Path to the zvec collection.",
        },
        "delete_by": {
            "choices": ["ids", "filter"],
            "description": "Delete by document IDs or a filter expression.",
        },
        "filter": {
            "placeholder": 'category == "stale" && date < 2024',
            "description": "[filter] Boolean expression to match documents to delete.",
        },
        "flush_after": {"description": "Flush to disk after delete."},
    },
)
def zvec_delete(
    input: Any = None,
    collection_path: str = "./zvec_collection",
    delete_by: str = "ids",
    filter: str = "",
    flush_after: bool = True,
) -> dict[str, Any]:
    """Delete documents from a zvec collection by ID or filter expression."""
    try:
        import zvec  # noqa: PLC0415
    except ImportError:
        raise ImportError("zvec_delete requires zvec>=0.4. Install with: pip install zvec")

    _ensure_init()
    col = _open_collection(collection_path, zvec)

    if delete_by == "filter":
        expr = filter or (str(input) if input is not None else "")
        if not expr:
            raise ValueError("zvec_delete: filter expression is required when delete_by='filter'")
        col.delete_by_filter(expr)
        if flush_after:
            col.flush()
        return {"deleted_by": "filter", "filter": expr, "collection_path": collection_path}

    # delete by IDs
    if isinstance(input, str):
        ids = [input]
    elif isinstance(input, list):
        ids = [str(x) for x in input]
    elif isinstance(input, dict) and "id" in input:
        ids = [str(input["id"])]
    else:
        raise ValueError("zvec_delete: input must be a string ID, list of IDs, or dict with 'id'")

    statuses = col.delete(ids)
    ok = sum(1 for s in statuses if s.ok())
    errors = [f"{doc_id}: {s.message()}" for doc_id, s in zip(ids, statuses, strict=False) if not s.ok()]

    if flush_after:
        col.flush()

    return {"deleted": ok, "errors": errors, "collection_path": collection_path}


# ---------------------------------------------------------------------------
# Node 8 — Collection Stats & Optimize
# ---------------------------------------------------------------------------


@node(
    name="zvec Collection Stats",
    id="zvec_collection_stats",
    category=ZVEC_CATEGORY,
    role="executable",
    icon="bar-chart",
    requirements=["zvec>=0.4"],
    inputs=["main"],
    outputs=["main"],
    params={
        "collection_path": {
            "placeholder": "./my_collection",
            "description": "Path to the zvec collection.",
        },
        "optimize": {
            "description": "Run optimization (merge segments, rebuild index) before reading stats."
        },
    },
)
def zvec_collection_stats(
    input: Any = None,
    collection_path: str = "./zvec_collection",
    optimize: bool = False,
) -> dict[str, Any]:
    """Get statistics for a zvec collection, optionally optimizing first."""
    try:
        import zvec  # noqa: PLC0415
    except ImportError:
        raise ImportError(
            "zvec_collection_stats requires zvec>=0.4. Install with: pip install zvec"
        )

    _ensure_init()
    col = _open_collection(collection_path, zvec)

    if optimize:
        col.optimize()

    s = col.stats
    schema = col.schema

    # Serialize schema fields
    scalar_fields = []
    if hasattr(schema, "fields") and schema.fields:
        for f in schema.fields:
            scalar_fields.append({"name": f.name, "data_type": str(f.data_type)})

    vector_fields = []
    if hasattr(schema, "vectors") and schema.vectors:
        for v in schema.vectors:
            vector_fields.append(
                {
                    "name": v.name,
                    "data_type": str(v.data_type),
                    "dimension": int(v.dimension) if hasattr(v, "dimension") else None,
                }
            )

    result: dict[str, Any] = {
        "collection_path": collection_path,
        "schema": {
            "name": schema.name if hasattr(schema, "name") else "",
            "scalar_fields": scalar_fields,
            "vector_fields": vector_fields,
        },
    }

    if s is not None:
        for attr in ("doc_count", "segment_count", "index_size", "total_size"):
            if hasattr(s, attr):
                result[attr] = int(getattr(s, attr))

    return result
