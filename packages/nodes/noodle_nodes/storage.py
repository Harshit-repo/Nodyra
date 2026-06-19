"""Storage / database nodes.

Database driver libraries (pymongo, redis, boto3, google-cloud-storage,
azure-storage-blob) are *workflow-env* dependencies — they install into a
user's chosen environment, not into the API process. Each node imports its
driver lazily and surfaces an actionable error if the driver isn't present.

ElasticSearch uses plain HTTP via ``requests`` so it works against any
Noodle env without a separate driver.

Credential metadata replaces inline secret fields with a single
"Credentials" picker per service.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import requests

from noodle.sdk import node
from noodle_nodes._creds import cred_multi, cred_single
from noodle_nodes.http_security import safe_request

_HTTP_TIMEOUT = 30


def _missing_driver(feature: str, package: str) -> RuntimeError:
    return RuntimeError(
        f"{feature} requires the Python package '{package}'. Install it in "
        "this workflow's environment and rebuild."
    )


def _expect_ok(response: requests.Response, service: str) -> dict:
    if response.status_code >= 400:
        body = response.text[:500]
        raise RuntimeError(f"{service}: HTTP {response.status_code} — {body}")
    try:
        return response.json()
    except ValueError:
        return {"text": response.text}


def _coerce_json(value: Any, *, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, dict | list):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return value


# ============================================================================
# MongoDB
# ============================================================================


@node(
    name="MongoDB Query",
    id="mongodb_query",
    category="Integrations",
    icon="brand:mongodb",
    params={
        "credentials": {
            **cred_single("mongodb", "uri", "MongoDB connection URI"),
            "description": "MongoDB connection string.",
        },
        "database": {"description": "Database name."},
        "collection": {"description": "Collection name."},
        "operation": {
            "choices": [
                "find",
                "find_one",
                "insert_one",
                "update_one",
                "delete_one",
                "count_documents",
            ],
            "description": "Operation to perform.",
        },
        "filter_json": {
            "group": "Options",
            "placeholder": '{"status": "open"}',
            "description": "Filter / query document as JSON.",
            "multiline": True,
        },
        "data_json": {
            "group": "Options",
            "placeholder": '{"$set": {"status": "closed"}}',
            "description": "Update or insert document as JSON.",
            "multiline": True,
        },
        "limit": {
            "group": "Options",
            "description": "Max results for 'find' (0 = no limit).",
        },
    },
)
def mongodb_query(
    input: Any = None,
    credentials: str = "",
    database: str = "",
    collection: str = "",
    operation: str = "find",
    filter_json: str = "",
    data_json: str = "",
    limit: int = 0,
) -> Any:
    """Run a single MongoDB operation."""
    _ = input
    uri = credentials
    if not all((uri, database, collection)):
        raise ValueError(
            "mongodb_query: credentials, database, and collection are required"
        )
    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise _missing_driver("MongoDB", "pymongo") from exc

    filt = _coerce_json(filter_json, default={}) or {}
    data = _coerce_json(data_json, default={}) or {}
    client = MongoClient(uri)
    try:
        col = client[database][collection]
        if operation == "find":
            cursor = col.find(filt)
            if limit and limit > 0:
                cursor = cursor.limit(int(limit))
            return [{**doc, "_id": str(doc.get("_id"))} for doc in cursor]
        if operation == "find_one":
            doc = col.find_one(filt)
            if doc is None:
                return None
            return {**doc, "_id": str(doc.get("_id"))}
        if operation == "insert_one":
            result = col.insert_one(data)
            return {"inserted_id": str(result.inserted_id)}
        if operation == "update_one":
            result = col.update_one(filt, data)
            return {
                "matched_count": result.matched_count,
                "modified_count": result.modified_count,
            }
        if operation == "delete_one":
            result = col.delete_one(filt)
            return {"deleted_count": result.deleted_count}
        if operation == "count_documents":
            return {"count": col.count_documents(filt)}
        raise ValueError(f"mongodb_query: unknown operation '{operation}'")
    finally:
        client.close()


# ============================================================================
# Redis
# ============================================================================


@node(
    name="Redis Command",
    id="redis_command",
    category="Integrations",
    icon="brand:redis",
    params={
        "credentials": {
            **cred_single("redis", "url", "Redis URL"),
            "description": "Redis connection URL.",
        },
        "operation": {
            "choices": [
                "get",
                "set",
                "delete",
                "incr",
                "expire",
                "publish",
                "lpush",
                "rpop",
            ],
            "description": "Redis command to run.",
        },
        "key": {"description": "Key (or channel for publish)."},
        "value": {
            "group": "Options",
            "description": (
                "Value for set/publish/lpush. Falls back to the wired input "
                "if blank."
            ),
            "multiline": True,
        },
        "ttl_seconds": {
            "group": "Options",
            "description": "TTL for 'set' or value for 'expire'.",
        },
    },
)
def redis_command(
    input: Any = None,
    credentials: str = "",
    operation: str = "get",
    key: str = "",
    value: str = "",
    ttl_seconds: int = 0,
) -> Any:
    """Run a single Redis command."""
    url = credentials
    if not url or not key:
        raise ValueError("redis_command: credentials and key are required")
    try:
        import redis as redis_lib
    except ImportError as exc:
        raise _missing_driver("Redis", "redis") from exc

    payload = value or (str(input) if input is not None else "")
    client = redis_lib.from_url(url, decode_responses=True)
    try:
        if operation == "get":
            return {"value": client.get(key)}
        if operation == "set":
            ex = int(ttl_seconds) if ttl_seconds and ttl_seconds > 0 else None
            return {"ok": bool(client.set(key, payload, ex=ex))}
        if operation == "delete":
            return {"deleted": client.delete(key)}
        if operation == "incr":
            return {"value": client.incr(key)}
        if operation == "expire":
            return {"set": bool(client.expire(key, int(ttl_seconds or 0)))}
        if operation == "publish":
            return {"receivers": client.publish(key, payload)}
        if operation == "lpush":
            return {"length": client.lpush(key, payload)}
        if operation == "rpop":
            return {"value": client.rpop(key)}
        raise ValueError(f"redis_command: unknown operation '{operation}'")
    finally:
        client.close()


# ============================================================================
# ElasticSearch (HTTP)
# ============================================================================


@node(
    name="ElasticSearch Search",
    id="elasticsearch_search",
    category="Integrations",
    icon="brand:elasticsearch",
    params={
        "base_url": {
            "placeholder": "https://localhost:9200",
            "description": "ElasticSearch base URL.",
        },
        "index": {"description": "Index name to search."},
        "query_json": {
            "placeholder": '{"query": {"match_all": {}}}',
            "description": "Full request body as JSON.",
            "multiline": True,
        },
        "credentials": {
            **cred_multi(
                "elasticsearch",
                "ElasticSearch credentials",
                ["api_key", "username", "password"],
            ),
            "description": (
                "Either api_key (preferred) or username + password. "
                "Leave fields blank to use anonymous access."
            ),
        },
    },
)
def elasticsearch_search(
    input: Any = None,
    base_url: str = "",
    index: str = "",
    query_json: str = "",
    credentials: dict | None = None,
) -> dict:
    """Run a search against ElasticSearch via its REST API."""
    _ = input
    if not base_url or not index:
        raise ValueError(
            "elasticsearch_search: base_url and index are required"
        )
    creds = credentials or {}
    api_key = str(creds.get("api_key") or "")
    username = str(creds.get("username") or "")
    password = str(creds.get("password") or "")
    body = _coerce_json(query_json, default={"query": {"match_all": {}}}) or {}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"ApiKey {api_key}"
    auth = (username, password) if username else None
    # SEC-2: base_url comes from user credentials (often a self-hosted
    # Elasticsearch host) — route through the SSRF guard.
    response = safe_request(
        "POST",
        f"{base_url.rstrip('/')}/{index}/_search",
        headers=headers,
        auth=auth,
        json=body,
        timeout=_HTTP_TIMEOUT,
        verify=True,
        context="elasticsearch search",
    )
    return _expect_ok(response, "elasticsearch")


# ============================================================================
# Google Cloud Storage
# ============================================================================


@node(
    name="GCS Upload",
    id="gcs_upload",
    category="Integrations",
    icon="brand:googlecloud",
    params={
        "bucket": {"description": "GCS bucket name."},
        "object_path": {
            "placeholder": "reports/2026-05-27.json",
            "description": "Object name (the key inside the bucket).",
        },
        "data_base64": {
            "description": (
                "File bytes as base64. Falls back to the wired input "
                "(string interpreted as UTF-8 text)."
            ),
            "multiline": True,
        },
        "content_type": {
            "group": "Options",
            "placeholder": "application/json",
            "description": "MIME type to set on the uploaded object.",
        },
    },
)
def gcs_upload(
    input: Any = None,
    bucket: str = "",
    object_path: str = "",
    data_base64: str = "",
    content_type: str = "application/octet-stream",
) -> dict:
    """Upload bytes to Google Cloud Storage.

    Authentication uses Application Default Credentials — set
    ``GOOGLE_APPLICATION_CREDENTIALS`` in the workflow env, or run on a GCP
    instance with a service account attached.
    """
    if not bucket or not object_path:
        raise ValueError("gcs_upload: bucket and object_path are required")
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise _missing_driver("GCS", "google-cloud-storage") from exc

    if data_base64:
        payload = base64.b64decode(data_base64)
    elif isinstance(input, bytes | bytearray):
        payload = bytes(input)
    elif isinstance(input, str):
        payload = input.encode("utf-8")
    else:
        raise ValueError("gcs_upload: payload (data_base64 or input) is required")

    client = storage.Client()
    blob = client.bucket(bucket).blob(object_path)
    blob.upload_from_string(payload, content_type=content_type or "application/octet-stream")
    return {
        "bucket": bucket,
        "object": object_path,
        "size_bytes": len(payload),
        "url": f"gs://{bucket}/{object_path}",
    }


@node(
    name="GCS List Objects",
    id="gcs_list_objects",
    category="Integrations",
    icon="brand:googlecloud",
    params={
        "bucket": {"description": "GCS bucket name."},
        "prefix": {
            "group": "Options",
            "placeholder": "reports/",
            "description": "Optional object name prefix.",
        },
        "max_results": {
            "group": "Options",
            "description": "Max objects to return (0 = default).",
        },
    },
)
def gcs_list_objects(
    input: Any = None,
    bucket: str = "",
    prefix: str = "",
    max_results: int = 0,
) -> list:
    """List objects in a GCS bucket."""
    _ = input
    if not bucket:
        raise ValueError("gcs_list_objects: bucket is required")
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise _missing_driver("GCS", "google-cloud-storage") from exc

    client = storage.Client()
    iterator = client.list_blobs(
        bucket,
        prefix=prefix or None,
        max_results=max_results or None,
    )
    return [
        {
            "name": blob.name,
            "size_bytes": blob.size,
            "content_type": blob.content_type,
            "updated": blob.updated.isoformat() if blob.updated else None,
        }
        for blob in iterator
    ]


# ============================================================================
# Azure Blob Storage
# ============================================================================


@node(
    name="Azure Blob Upload",
    id="azure_blob_upload",
    category="Integrations",
    icon="brand:microsoftazure",
    params={
        "credentials": {
            **cred_single(
                "azure_blob", "connection_string", "Azure Storage connection string"
            ),
            "description": "Azure Storage account connection string.",
        },
        "container": {"description": "Blob container name."},
        "blob_path": {
            "placeholder": "reports/2026-05-27.json",
            "description": "Blob name inside the container.",
        },
        "data_base64": {
            "description": (
                "File bytes as base64. Falls back to the wired input "
                "(string interpreted as UTF-8 text)."
            ),
            "multiline": True,
        },
        "content_type": {
            "group": "Options",
            "placeholder": "application/json",
            "description": "MIME type.",
        },
    },
)
def azure_blob_upload(
    input: Any = None,
    credentials: str = "",
    container: str = "",
    blob_path: str = "",
    data_base64: str = "",
    content_type: str = "application/octet-stream",
) -> dict:
    """Upload bytes to Azure Blob Storage."""
    connection_string = credentials
    if not all((connection_string, container, blob_path)):
        raise ValueError(
            "azure_blob_upload: credentials, container, and blob_path are required"
        )
    try:
        from azure.storage.blob import BlobServiceClient, ContentSettings
    except ImportError as exc:
        raise _missing_driver("Azure Blob", "azure-storage-blob") from exc

    if data_base64:
        payload = base64.b64decode(data_base64)
    elif isinstance(input, bytes | bytearray):
        payload = bytes(input)
    elif isinstance(input, str):
        payload = input.encode("utf-8")
    else:
        raise ValueError("azure_blob_upload: payload is required")

    client = BlobServiceClient.from_connection_string(connection_string)
    blob = client.get_blob_client(container=container, blob=blob_path)
    blob.upload_blob(
        payload,
        overwrite=True,
        content_settings=ContentSettings(
            content_type=content_type or "application/octet-stream"
        ),
    )
    return {
        "container": container,
        "blob": blob_path,
        "size_bytes": len(payload),
        "url": blob.url,
    }


# ============================================================================
# DynamoDB
# ============================================================================


@node(
    name="DynamoDB Get Item",
    id="dynamodb_get_item",
    category="Integrations",
    icon="brand:amazondynamodb",
    params={
        "region": {
            "placeholder": "us-east-1",
            "description": "AWS region.",
        },
        "table": {"description": "DynamoDB table name."},
        "key_json": {
            "placeholder": '{"id": "abc-123"}',
            "description": "Primary key (Python types — boto3 marshals).",
            "multiline": True,
        },
        "credentials": {
            **cred_multi(
                "aws",
                "AWS credentials",
                ["aws_access_key_id", "aws_secret_access_key"],
            ),
            "description": (
                "Optional AWS access key + secret. Leave blank to use the "
                "default boto3 credential chain (env vars, instance role)."
            ),
        },
    },
)
def dynamodb_get_item(
    input: Any = None,
    region: str = "us-east-1",
    table: str = "",
    key_json: str = "",
    credentials: dict | None = None,
) -> dict:
    """Fetch a single item from DynamoDB."""
    _ = input
    if not table or not key_json:
        raise ValueError("dynamodb_get_item: table and key_json are required")
    try:
        import boto3
    except ImportError as exc:
        raise _missing_driver("DynamoDB", "boto3") from exc

    creds = credentials or {}
    aws_access_key_id = str(creds.get("aws_access_key_id") or "")
    aws_secret_access_key = str(creds.get("aws_secret_access_key") or "")
    key = _coerce_json(key_json) or {}
    kwargs: dict[str, Any] = {"region_name": region or "us-east-1"}
    if aws_access_key_id and aws_secret_access_key:
        kwargs["aws_access_key_id"] = aws_access_key_id
        kwargs["aws_secret_access_key"] = aws_secret_access_key
    resource = boto3.resource("dynamodb", **kwargs)
    table_ref = resource.Table(table)
    response = table_ref.get_item(Key=key)
    return {"item": response.get("Item")}


@node(
    name="DynamoDB Put Item",
    id="dynamodb_put_item",
    category="Integrations",
    icon="brand:amazondynamodb",
    params={
        "region": {
            "placeholder": "us-east-1",
            "description": "AWS region.",
        },
        "table": {"description": "DynamoDB table name."},
        "item_json": {
            "placeholder": '{"id": "abc", "name": "Bob"}',
            "description": "Item to write as JSON.",
            "multiline": True,
        },
        "credentials": {
            **cred_multi(
                "aws",
                "AWS credentials",
                ["aws_access_key_id", "aws_secret_access_key"],
            ),
            "description": (
                "Optional AWS access key + secret. Leave blank to use the "
                "default boto3 credential chain (env vars, instance role)."
            ),
        },
    },
)
def dynamodb_put_item(
    input: Any = None,
    region: str = "us-east-1",
    table: str = "",
    item_json: str = "",
    credentials: dict | None = None,
) -> dict:
    """Write a single item to DynamoDB."""
    _ = input
    if not table:
        raise ValueError("dynamodb_put_item: table is required")
    try:
        import boto3
    except ImportError as exc:
        raise _missing_driver("DynamoDB", "boto3") from exc

    creds = credentials or {}
    aws_access_key_id = str(creds.get("aws_access_key_id") or "")
    aws_secret_access_key = str(creds.get("aws_secret_access_key") or "")
    item = _coerce_json(item_json, default=input if isinstance(input, dict) else None)
    if not isinstance(item, dict):
        raise ValueError(
            "dynamodb_put_item: provide item_json or wire a dict to input"
        )
    kwargs: dict[str, Any] = {"region_name": region or "us-east-1"}
    if aws_access_key_id and aws_secret_access_key:
        kwargs["aws_access_key_id"] = aws_access_key_id
        kwargs["aws_secret_access_key"] = aws_secret_access_key
    resource = boto3.resource("dynamodb", **kwargs)
    resource.Table(table).put_item(Item=item)
    return {"written": True, "item": item}
