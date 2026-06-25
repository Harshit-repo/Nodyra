"""Trello v2 operation specs and executors."""

from __future__ import annotations

from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_integration, register_operation
from noodle_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from noodle_nodes.integrations_v2.transport import ProviderTransport

TRELLO_API_BASE = "https://api.trello.com/1"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="trello_api",
            key="*",
            label="Trello API Key & Token",
            fields=["api_key", "token"],
        ),
        description="Trello API key and token from https://trello.com/power-ups/admin.",
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    api_key = str(creds.get("api_key") or "")
    token = str(creds.get("token") or "")
    if not api_key or not token:
        raise ValueError("trello: api_key and token are required")
    return ProviderTransport(
        provider="trello",
        base_url=TRELLO_API_BASE,
        default_headers={"Content-Type": "application/json"},
    )


def _auth_params(credentials: Any) -> dict[str, str]:
    creds = _credentials_dict(credentials)
    return {
        "key": str(creds.get("api_key") or ""),
        "token": str(creds.get("token") or ""),
    }


TRELLO_LIST_BOARDS_SPEC = OperationSpec(
    node_id="trello_list_boards_v2",
    name="Trello List Boards",
    provider="trello",
    resource="board",
    operation="list",
    description="List all Trello boards for the authenticated user.",
    icon="brand:trello",
    tool_side_effecting=False,
    params=(_credentials_param(),),
)

TRELLO_LIST_LISTS_SPEC = OperationSpec(
    node_id="trello_list_lists_v2",
    name="Trello List Lists",
    provider="trello",
    resource="list",
    operation="list",
    description="List all lists on a Trello board.",
    icon="brand:trello",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="board_id", required=True),
    ),
)

TRELLO_CREATE_CARD_SPEC = OperationSpec(
    node_id="trello_create_card_v2",
    name="Trello Create Card",
    provider="trello",
    resource="card",
    operation="create",
    description="Create a new card on a Trello list.",
    icon="brand:trello",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="idList", required=True, description="The ID of the list to add the card to."
        ),
        OperationParamSpec(name="name", required=True),
        OperationParamSpec(name="desc", multiline=True, group="Options"),
        OperationParamSpec(
            name="due",
            group="Options",
            placeholder="2025-12-31T23:59:59.000Z",
        ),
        OperationParamSpec(
            name="pos",
            choices=("top", "bottom"),
            group="Options",
            description="Position on the list. Use 'top', 'bottom', or a numeric value.",
        ),
        OperationParamSpec(
            name="idLabels",
            group="Options",
            description="Comma-separated label IDs to add to the card.",
        ),
        OperationParamSpec(
            name="idMembers",
            group="Options",
            description="Comma-separated member IDs to assign to the card.",
        ),
    ),
)

TRELLO_UPDATE_CARD_SPEC = OperationSpec(
    node_id="trello_update_card_v2",
    name="Trello Update Card",
    provider="trello",
    resource="card",
    operation="update",
    description="Update an existing Trello card.",
    icon="brand:trello",
    params=(
        _credentials_param(),
        OperationParamSpec(name="card_id", required=True),
        OperationParamSpec(name="name", group="Options"),
        OperationParamSpec(name="desc", multiline=True, group="Options"),
        OperationParamSpec(
            name="due",
            group="Options",
            placeholder="2025-12-31T23:59:59.000Z",
        ),
        OperationParamSpec(
            name="dueComplete",
            type="boolean",
            group="Options",
        ),
        OperationParamSpec(
            name="idList",
            group="Options",
            description="Move the card to a different list.",
        ),
        OperationParamSpec(
            name="pos",
            group="Options",
            description="Position on the list. Use 'top', 'bottom', or a numeric value.",
        ),
    ),
)

TRELLO_GET_CARD_SPEC = OperationSpec(
    node_id="trello_get_card_v2",
    name="Trello Get Card",
    provider="trello",
    resource="card",
    operation="get",
    description="Get details of a specific Trello card.",
    icon="brand:trello",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="card_id", required=True),
    ),
)

TRELLO_LIST_LABELS_SPEC = OperationSpec(
    node_id="trello_list_labels_v2",
    name="Trello List Labels",
    provider="trello",
    resource="label",
    operation="list",
    description="List all labels on a Trello board.",
    icon="brand:trello",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="board_id", required=True),
        OperationParamSpec(
            name="limit",
            type="number",
            default=50,
            group="Options",
        ),
    ),
)


def list_boards(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
) -> Any:
    return _transport(credentials).request(
        "GET",
        "/members/me/boards",
        operation="list_boards",
        params=_auth_params(credentials),
    )


def list_lists(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    board_id: str = "",
) -> Any:
    if not board_id:
        raise ValueError("trello_list_lists_v2: board_id is required")
    return _transport(credentials).request(
        "GET",
        f"/boards/{board_id}/lists",
        operation="list_lists",
        params=_auth_params(credentials),
    )


def create_card(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    idList: str = "",
    name: str = "",
    desc: str = "",
    due: str = "",
    pos: str = "",
    idLabels: str = "",
    idMembers: str = "",
) -> Any:
    if not idList:
        raise ValueError("trello_create_card_v2: idList is required")
    if not name:
        raise ValueError("trello_create_card_v2: name is required")
    payload: dict[str, str] = {"idList": idList, "name": name}
    if desc:
        payload["desc"] = desc
    if due:
        payload["due"] = due
    if pos:
        payload["pos"] = pos
    if idLabels:
        payload["idLabels"] = idLabels
    if idMembers:
        payload["idMembers"] = idMembers
    params = dict(_auth_params(credentials))
    return _transport(credentials).request(
        "POST",
        "/cards",
        operation="create_card",
        params=params,
        json_body=payload,
    )


def update_card(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    card_id: str = "",
    name: str = "",
    desc: str = "",
    due: str = "",
    dueComplete: bool = False,
    idList: str = "",
    pos: str = "",
) -> Any:
    if not card_id:
        raise ValueError("trello_update_card_v2: card_id is required")
    payload: dict[str, Any] = {}
    if name:
        payload["name"] = name
    if desc:
        payload["desc"] = desc
    if due:
        payload["due"] = due
    if dueComplete:
        payload["dueComplete"] = True
    if idList:
        payload["idList"] = idList
    if pos:
        payload["pos"] = pos
    if not payload:
        raise ValueError("trello_update_card_v2: at least one field to update is required")
    params = dict(_auth_params(credentials))
    return _transport(credentials).request(
        "PUT",
        f"/cards/{card_id}",
        operation="update_card",
        params=params,
        json_body=payload,
    )


def get_card(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    card_id: str = "",
) -> Any:
    if not card_id:
        raise ValueError("trello_get_card_v2: card_id is required")
    return _transport(credentials).request(
        "GET",
        f"/cards/{card_id}",
        operation="get_card",
        params=_auth_params(credentials),
    )


def list_labels(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    board_id: str = "",
    limit: int = 50,
) -> Any:
    if not board_id:
        raise ValueError("trello_list_labels_v2: board_id is required")
    params = dict(_auth_params(credentials))
    params["limit"] = max(1, min(1000, int(limit or 50)))
    return _transport(credentials).request(
        "GET",
        f"/boards/{board_id}/labels",
        operation="list_labels",
        params=params,
    )


register_operation(TRELLO_LIST_BOARDS_SPEC, list_boards, node_registry=None)
register_operation(TRELLO_LIST_LISTS_SPEC, list_lists, node_registry=None)
register_operation(TRELLO_CREATE_CARD_SPEC, create_card, node_registry=None)
register_operation(TRELLO_UPDATE_CARD_SPEC, update_card, node_registry=None)
register_operation(TRELLO_GET_CARD_SPEC, get_card, node_registry=None)
register_operation(TRELLO_LIST_LABELS_SPEC, list_labels, node_registry=None)


TRELLO_INTEGRATION = IntegrationSpec(
    id="trello",
    name="Trello",
    description="Manage Trello boards, lists, cards, and labels.",
    icon="brand:trello",
    credential_types=("trello_api",),
    resources=(
        ResourceSpec(
            id="board",
            name="Board",
            operations=(TRELLO_LIST_BOARDS_SPEC,),
        ),
        ResourceSpec(
            id="list",
            name="List",
            operations=(TRELLO_LIST_LISTS_SPEC,),
        ),
        ResourceSpec(
            id="card",
            name="Card",
            operations=(
                TRELLO_CREATE_CARD_SPEC,
                TRELLO_UPDATE_CARD_SPEC,
                TRELLO_GET_CARD_SPEC,
            ),
        ),
        ResourceSpec(
            id="label",
            name="Label",
            operations=(TRELLO_LIST_LABELS_SPEC,),
        ),
    ),
)

register_integration(TRELLO_INTEGRATION)
