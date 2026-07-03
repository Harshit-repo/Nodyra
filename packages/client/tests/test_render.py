from __future__ import annotations

import json

from pydantic import BaseModel


class _Row(BaseModel):
    id: str
    name: str
    status: str


def _rows() -> list[_Row]:
    return [
        _Row(id="wf_1", name="Daily ETL", status="published"),
        _Row(id="wf_2", name="Alerts", status="draft"),
    ]


def test_emit_table_mode(capsys) -> None:
    from nodyra_client.cli.render import emit

    emit(
        _rows(),
        json_mode=False,
        columns=[("ID", "id"), ("Name", "name"), ("Status", "status")],
    )
    out = capsys.readouterr().out
    assert "Daily ETL" in out and "wf_2" in out
    assert "ID" in out


def test_emit_json_mode_is_parseable(capsys) -> None:
    from nodyra_client.cli.render import emit

    emit(
        _rows(),
        json_mode=True,
        columns=[("ID", "id"), ("Name", "name"), ("Status", "status")],
    )
    data = json.loads(capsys.readouterr().out)
    assert data[0]["id"] == "wf_1"


def test_emit_scalar_none_prints_ok(capsys) -> None:
    from nodyra_client.cli.render import emit

    emit(None, json_mode=False)
    assert capsys.readouterr().out.strip() == "OK"
