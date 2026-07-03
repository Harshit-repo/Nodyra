"""Google Drive v2 operation specs and executors."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.providers.google import GoogleTransport
from nodyra_nodes.integrations_v2.registry import register_operation
from nodyra_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="google_drive_oauth2",
            key="*",
            label="Google Drive OAuth2",
            fields=["access_token", "refresh_token"],
            multi=True,
            test_service="google_drive",
        ),
        required_scopes=(DRIVE_SCOPE,),
    )


GOOGLE_DRIVE_LIST_FILES_SPEC = OperationSpec(
    node_id="google_drive_list_files",
    name="Google Drive List Files",
    provider="google_drive",
    resource="files",
    operation="list",
    description="List files and folders in Google Drive.",
    icon="brand:googledrive",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="query",
            placeholder="name contains 'report'",
            description="Drive query. Leave blank for all files.",
        ),
        OperationParamSpec(
            name="page_size",
            type="number",
            default=50,
            group="Options",
            description="Files per page (max 1000).",
        ),
        OperationParamSpec(
            name="order_by",
            group="Options",
            default="modifiedTime desc",
            placeholder="modifiedTime desc",
            description="Sort order (e.g. 'modifiedTime desc, name').",
        ),
        OperationParamSpec(
            name="include_items_from_all_drives",
            type="boolean",
            default=False,
            group="Options",
        ),
    ),
)

GOOGLE_DRIVE_CREATE_FOLDER_SPEC = OperationSpec(
    node_id="google_drive_create_folder",
    name="Google Drive Create Folder",
    provider="google_drive",
    resource="files",
    operation="create_folder",
    description="Create a folder in Google Drive.",
    icon="brand:googledrive",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="name",
            required=True,
            placeholder="New Folder",
            description="Folder name.",
        ),
        OperationParamSpec(
            name="parent_id",
            placeholder="Folder ID",
            description="Optional parent folder ID. Blank = root.",
        ),
    ),
)

GOOGLE_DRIVE_UPLOAD_FILE_SPEC = OperationSpec(
    node_id="google_drive_upload_file",
    name="Google Drive Upload File",
    provider="google_drive",
    resource="files",
    operation="upload",
    description="Upload a file to Google Drive.",
    icon="brand:googledrive",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="name",
            required=True,
            placeholder="filename.pdf",
            description="Filename in Drive.",
        ),
        OperationParamSpec(
            name="parent_id",
            placeholder="Folder ID",
            description="Optional parent folder ID. Blank = root.",
        ),
        OperationParamSpec(
            name="mime_type",
            placeholder="application/pdf",
            description="MIME type. Leave blank for automatic detection.",
        ),
        OperationParamSpec(
            name="body",
            multiline=True,
            description="File content. Blank uses the wired input.",
        ),
    ),
)

GOOGLE_DRIVE_DOWNLOAD_FILE_SPEC = OperationSpec(
    node_id="google_drive_download_file",
    name="Google Drive Download File",
    provider="google_drive",
    resource="files",
    operation="download",
    description="Download a file from Google Drive.",
    icon="brand:googledrive",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="file_id",
            required=True,
            placeholder="File ID",
            description="ID of the file to download.",
        ),
        OperationParamSpec(
            name="mime_type",
            group="Options",
            placeholder="application/pdf",
            description="Optional export MIME type (Google Docs/Sheets only).",
        ),
    ),
)

GOOGLE_DRIVE_DELETE_FILE_SPEC = OperationSpec(
    node_id="google_drive_delete_file",
    name="Google Drive Delete File",
    provider="google_drive",
    resource="files",
    operation="delete",
    description="Delete a file or folder from Google Drive.",
    icon="brand:googledrive",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="file_id",
            required=True,
            placeholder="File ID",
            description="ID of the file to delete.",
        ),
    ),
)

GOOGLE_DRIVE_GET_FILE_SPEC = OperationSpec(
    node_id="google_drive_get_file",
    name="Google Drive Get File Metadata",
    provider="google_drive",
    resource="files",
    operation="get",
    description="Get metadata for a Google Drive file.",
    icon="brand:googledrive",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="file_id",
            required=True,
            placeholder="File ID",
            description="ID of the file.",
        ),
    ),
)


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(val) for key, val in value.items()}
    if isinstance(value, str):
        return {"access_token": value}
    return {}


def _transport(credentials: Any) -> GoogleTransport:
    creds = _credentials_dict(credentials)
    return GoogleTransport(
        base_url=DRIVE_BASE,
        access_token=str(creds.get("access_token") or ""),
        api_key=str(creds.get("api_key") or ""),
    )


def _require(value: str, node_id: str, name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{node_id}: {name} is required")
    return clean


def list_files(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    query: str = "",
    page_size: int = 50,
    order_by: str = "modifiedTime desc",
    include_items_from_all_drives: bool = False,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "pageSize": min(1000, max(1, int(page_size or 50))),
    }
    if query:
        params["q"] = query
    if order_by:
        params["orderBy"] = order_by
    if include_items_from_all_drives:
        params["includeItemsFromAllDrives"] = "true"
        params["supportsAllDrives"] = "true"

    transport = _transport(credentials)
    result = transport.request("GET", "/files", operation="list_files", params=params)
    if isinstance(result, dict):
        files = result.get("files", [])
        return {"files": files, "count": len(files)}
    return {"files": [], "count": 0}


def create_folder(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    name: str = "",
    parent_id: str = "",
) -> dict[str, Any]:
    folder_name = _require(name, "google_drive_create_folder", "name")
    body: dict[str, Any] = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        body["parents"] = [parent_id]
    return _transport(credentials).request(
        "POST",
        "/files",
        operation="create_folder",
        json_body=body,
        params={"fields": "id,name,mimeType,parents,createdTime"},
    )


def upload_file(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    name: str = "",
    parent_id: str = "",
    mime_type: str = "",
    body: str = "",
) -> dict[str, Any]:
    file_name = _require(name, "google_drive_upload_file", "name")
    content = body if body else (str(input) if input is not None else "")

    metadata: dict[str, Any] = {"name": file_name}
    if parent_id:
        metadata["parents"] = [parent_id]
    if mime_type:
        metadata["mimeType"] = mime_type

    import json

    from nodyra_nodes.integrations_v2.providers.google.transport import (
        ProviderTransport,
    )

    # Upload uses multipart for metadata + content
    creds = _credentials_dict(credentials)
    transport = ProviderTransport(
        provider="google_drive",
        base_url="https://www.googleapis.com/upload/drive/v3",
        default_headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
    )
    boundary = "nodyra_drive_upload_boundary"
    body_bytes = content.encode("utf-8") if isinstance(content, str) else content
    metadata_bytes = json.dumps(metadata).encode("utf-8")

    multipart_body = (
        (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{metadata_bytes.decode('utf-8')}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {mime_type or 'application/octet-stream'}\r\n\r\n"
        ).encode()
        + body_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    return transport.request(
        "POST",
        "/files",
        operation="upload_file",
        headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        params={"uploadType": "multipart", "fields": "id,name,mimeType,size,parents,createdTime"},
        data=multipart_body,
    )


def download_file(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    file_id: str = "",
    mime_type: str = "",
) -> dict[str, Any]:
    fid = _require(file_id, "google_drive_download_file", "file_id")
    transport = _transport(credentials)

    if mime_type:
        result = transport.request(
            "GET",
            f"/files/{quote(fid)}/export",
            operation="download_file",
            params={"mimeType": mime_type},
        )
    else:
        result = transport.request(
            "GET",
            f"/files/{quote(fid)}",
            operation="download_file",
            params={"alt": "media"},
        )

    return {"file_id": fid, "content": result}


def delete_file(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    file_id: str = "",
) -> dict[str, Any]:
    fid = _require(file_id, "google_drive_delete_file", "file_id")
    return _transport(credentials).request(
        "DELETE", f"/files/{quote(fid)}", operation="delete_file"
    )


def get_file(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    file_id: str = "",
) -> dict[str, Any]:
    fid = _require(file_id, "google_drive_get_file", "file_id")
    return _transport(credentials).request(
        "GET",
        f"/files/{quote(fid)}",
        operation="get_file",
        params={
            "fields": "id,name,mimeType,size,parents,createdTime,modifiedTime,description,webViewLink"
        },
    )


register_operation(GOOGLE_DRIVE_LIST_FILES_SPEC, list_files)
register_operation(GOOGLE_DRIVE_CREATE_FOLDER_SPEC, create_folder)
register_operation(GOOGLE_DRIVE_UPLOAD_FILE_SPEC, upload_file)
register_operation(GOOGLE_DRIVE_DOWNLOAD_FILE_SPEC, download_file)
register_operation(GOOGLE_DRIVE_DELETE_FILE_SPEC, delete_file)
register_operation(GOOGLE_DRIVE_GET_FILE_SPEC, get_file)
