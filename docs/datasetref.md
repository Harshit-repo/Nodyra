# DatasetRef guide

DatasetRef is Nodyra's table handle for data that should stay artifact-backed instead of being copied through every node as a giant JSON list.

Think of it like a library card for a table: the workflow passes the card downstream, while the real rows stay in artifact storage as Parquet. Nodes can preview, filter, query, export, or materialize the table when needed.

## When to use DatasetRef

Use DatasetRef when:

- You have tabular data from CSV, API records, a DataFrame, or SQL results.
- The row count can grow beyond what is comfortable to display inline.
- You want DuckDB-style SQL transforms.
- You want downstream nodes to keep schema/preview metadata without duplicating the full table.

Use inline records when:

- The data is small and a downstream integration expects a JSON list/dict.
- You are sending a few rows to a message, webhook, or custom Code node.

## The core pattern

Most DatasetRef workflows follow this shape:

```text
records / CSV / DataFrame
  -> Records To Dataset or CSV Parse
  -> Dataset Filter / Dataset Select Columns / DuckDB SQL / Dataset Limit
  -> Dataset Preview or CSV Write
  -> optional Dataset To Records at the edge
```

Keep the purple DatasetRef wire as long as possible. Convert back to records only at the point where a node truly needs inline JSON rows.

## Built-in DatasetRef nodes

- **CSV Parse**: CSV text or a CSV artifact becomes a DatasetRef.
- **Records To Dataset**: a list of dicts, or an object containing `records`, `rows`, `items`, `data`, or `results`, becomes a DatasetRef.
- **Dataset Preview**: shows row count, schema, and sample rows without materializing the full table.
- **Dataset Select Columns**: keeps named columns.
- **Dataset Filter**: applies a DuckDB `WHERE` expression.
- **Dataset Limit**: keeps a bounded slice.
- **DuckDB SQL**: runs one read-only `SELECT` or `WITH` query against the wired dataset, exposed as `input`.
- **Dataset To Records**: materializes rows as a JSON list for nodes that cannot consume DatasetRef directly.
- **CSV Write**: exports the final DatasetRef to a downloadable CSV artifact.

## SQL examples

DuckDB SQL nodes reference the wired dataset as `input`:

```sql
SELECT * FROM input LIMIT 100
```

```sql
SELECT status, COUNT(*) AS n
FROM input
GROUP BY status
ORDER BY n DESC
```

```sql
WITH recent AS (
  SELECT * FROM input WHERE created_at >= DATE '2026-01-01'
)
SELECT customer_id, SUM(amount) AS total_amount
FROM recent
GROUP BY customer_id
ORDER BY total_amount DESC
```

SQL safety rules:

- Only one statement is allowed.
- The statement must be `SELECT` or `WITH`.
- File-reading functions such as `read_csv`, `read_json`, and `read_parquet` are blocked. Query the wired dataset; do not open arbitrary files from SQL.

## Connection validation and quick fixes

The editor treats DatasetRef ports as a distinct kind of wire:

- If a node expects a DatasetRef and you connect inline records, Nodyra blocks the wire and offers **Add Records To Dataset**.
- If a node outputs a DatasetRef and you connect it to a normal inline-data input, Nodyra blocks the wire and offers **Add Dataset To Records**.
- If the target is an artifact/file-style step, Nodyra suggests a dataset helper such as **DuckDB SQL** before export.

This is intentional. An `any` port usually means inline JSON; DatasetRef is a table reference with artifact metadata. Requiring an explicit converter makes workflows easier to read and prevents runtime surprises.

## UI tips

- DatasetRef-producing and DatasetRef-consuming nodes have DatasetRef badges in the palette.
- Search terms like `dataset`, `datasetref`, `parquet`, `duckdb`, `sql`, `records`, and `table` find the relevant nodes.
- Empty canvases include a **DatasetRef + SQL** starter.
- The workflow template list includes:
  - **DatasetRef — API to SQL**
  - **DatasetRef — filter + CSV**
- Dataset output cards include preview metadata and an SQL explorer so users can try read-only queries before adding a DuckDB SQL node.

## Python / Code node examples

A Code node can produce records and then hand off to **Records To Dataset**:

```python
rows = [
    {"id": 1, "region": "west", "amount": 120},
    {"id": 2, "region": "east", "amount": 80},
]
output = rows
```

A Code node can also return a pandas DataFrame; serialization promotes it to a dataset-style artifact-backed value for UI/output handling:

```python
import pandas as pd

output = pd.DataFrame([
    {"id": 1, "region": "west", "amount": 120},
    {"id": 2, "region": "east", "amount": 80},
])
```

## Troubleshooting

- **"This input expects a DatasetRef"**: add **Records To Dataset** before the DatasetRef node.
- **"This output is a DatasetRef"**: add **Dataset To Records** before a JSON/integration node, or keep using DatasetRef-native nodes.
- **SQL file function rejected**: remove `read_csv`, `read_json`, or `read_parquet`; the wired dataset is already available as `input`.
- **Preview is truncated**: that only affects displayed sample rows. DatasetRef identity, artifact ID, schema, and row count stay intact downstream.
- **Large result needs a file**: use DatasetRef transforms first, then **CSV Write** at the end.
