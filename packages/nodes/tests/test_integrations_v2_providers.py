"""Tests for the v2 integration provider nodes: Google Sheets and Microsoft Outlook."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import noodle_nodes.integrations_v2.providers.google_sheets  # noqa: F401
from noodle.sdk import registry
from noodle_nodes.integrations_v2.dynamic_options import call_loader
from noodle_nodes.integrations_v2.providers.google_sheets.operations import (
    append_values,
    clear_values,
    get_spreadsheet_metadata,
    read_values,
    update_values,
)
from noodle_nodes.integrations_v2.providers.microsoft_outlook.operations import (
    create_calendar_event,
    create_draft,
    delete_calendar_event,
    delete_message,
    forward_message,
    get_message,
    get_message_attachment,
    list_calendar_events,
    list_message_attachments,
    list_messages,
    reply_message,
    send_draft,
    send_mail,
    update_calendar_event,
    update_message,
)

# ---------------------------------------------------------------------------
# Google Sheets v2
# ---------------------------------------------------------------------------


@pytest.fixture()
def gs_creds():
    return {"access_token": "test-token", "api_key": ""}


def _mock_transport(return_value: Any):
    m = MagicMock()
    m.request.return_value = return_value
    return m


def test_google_sheets_v2_manifests_are_tool_compatible() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}

    read_only = {
        "google_sheets_read_v2",
        "google_sheets_get_metadata_v2",
    }
    side_effecting = {
        "google_sheets_append_v2",
        "google_sheets_update_v2",
        "google_sheets_clear_v2",
    }
    for node_id in read_only | side_effecting:
        assert manifests[node_id].usable_as_tool is True
    for node_id in read_only:
        assert manifests[node_id].tool_side_effecting is False
    for node_id in side_effecting:
        assert manifests[node_id].tool_side_effecting is True


class TestGoogleSheetsRead:
    def test_calls_correct_endpoint(self, gs_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.google_sheets.operations._transport"
        ) as mock_t:
            t = _mock_transport({"values": [["a", "b"], [1, 2]]})
            mock_t.return_value = t
            result = read_values(
                credentials=gs_creds,
                spreadsheet_id="sid1",
                range_name="Sheet1!A1:B2",
            )
        t.request.assert_called_once()
        method, path = t.request.call_args[0]
        assert method == "GET"
        # ! and : are in the safe set so they are NOT percent-encoded
        assert "/spreadsheets/sid1/values/Sheet1!A1:B2" == path
        assert result["values"][0] == ["a", "b"]

    def test_range_url_encoding(self, gs_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.google_sheets.operations._transport"
        ) as mock_t:
            t = _mock_transport({})
            mock_t.return_value = t
            read_values(credentials=gs_creds, spreadsheet_id="sid", range_name="My Sheet!A:D")
        called_path = t.request.call_args[0][1]
        # Space should be encoded; ! and : are safe
        assert "%20" in called_path


class TestGoogleSheetsAppend:
    def test_appends_list_of_lists(self, gs_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.google_sheets.operations._transport"
        ) as mock_t:
            t = _mock_transport({"updates": {}})
            mock_t.return_value = t
            append_values(
                credentials=gs_creds,
                spreadsheet_id="sid",
                range_name="Sheet1!A:B",
                values=[[1, 2], [3, 4]],
            )
        _kw = t.request.call_args[1]
        assert _kw["json_body"]["values"] == [[1, 2], [3, 4]]

    def test_input_coercion_dict(self, gs_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.google_sheets.operations._transport"
        ) as mock_t:
            t = _mock_transport({})
            mock_t.return_value = t
            append_values(
                input={"name": "Alice", "age": 30},
                credentials=gs_creds,
                spreadsheet_id="sid",
                range_name="Sheet1!A:B",
            )
        body = t.request.call_args[1]["json_body"]
        assert body["values"] == [["Alice", 30]]

    def test_input_coercion_scalar(self, gs_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.google_sheets.operations._transport"
        ) as mock_t:
            t = _mock_transport({})
            mock_t.return_value = t
            append_values(
                input=42,
                credentials=gs_creds,
                spreadsheet_id="sid",
                range_name="Sheet1!A:B",
            )
        body = t.request.call_args[1]["json_body"]
        assert body["values"] == [[42]]


class TestGoogleSheetsUpdate:
    def test_requires_range_name(self, gs_creds):
        with pytest.raises(ValueError, match="range_name"):
            update_values(credentials=gs_creds, spreadsheet_id="sid", range_name="")

    def test_calls_put(self, gs_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.google_sheets.operations._transport"
        ) as mock_t:
            t = _mock_transport({})
            mock_t.return_value = t
            update_values(
                credentials=gs_creds,
                spreadsheet_id="sid",
                range_name="Sheet1!A1:B2",
                values=[[1, 2]],
            )
        t.request.assert_called_once()
        method = t.request.call_args[0][0]
        assert method == "PUT"


class TestGoogleSheetsClear:
    def test_requires_range_name(self, gs_creds):
        with pytest.raises(ValueError, match="range_name"):
            clear_values(credentials=gs_creds, spreadsheet_id="sid", range_name="")

    def test_calls_post(self, gs_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.google_sheets.operations._transport"
        ) as mock_t:
            t = _mock_transport({})
            mock_t.return_value = t
            clear_values(credentials=gs_creds, spreadsheet_id="sid", range_name="Sheet1!A1:Z")
        method = t.request.call_args[0][0]
        assert method == "POST"


class TestGoogleSheetsMetadata:
    def test_flattens_sheet_names(self, gs_creds):
        api_response = {
            "spreadsheetId": "sid",
            "properties": {"title": "My Spreadsheet"},
            "sheets": [
                {"properties": {"title": "Alpha", "sheetId": 0}},
                {"properties": {"title": "Beta", "sheetId": 1}},
            ],
        }
        with patch(
            "noodle_nodes.integrations_v2.providers.google_sheets.operations._transport"
        ) as mock_t:
            t = _mock_transport(api_response)
            mock_t.return_value = t
            result = get_spreadsheet_metadata(credentials=gs_creds, spreadsheet_id="sid")
        assert result["sheet_names"] == ["Alpha", "Beta"]

    def test_empty_response_produces_empty_list(self, gs_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.google_sheets.operations._transport"
        ) as mock_t:
            t = _mock_transport({})
            mock_t.return_value = t
            result = get_spreadsheet_metadata(credentials=gs_creds, spreadsheet_id="sid")
        assert result["sheet_names"] == []


# ---------------------------------------------------------------------------
# Dynamic options loaders (Google Sheets)
# ---------------------------------------------------------------------------


class TestGoogleSheetsDynamicOptions:
    def test_list_sheet_names_happy_path(self):
        fake_response = {
            "sheets": [
                {"properties": {"title": "Sheet1", "sheetId": 0}},
                {"properties": {"title": "Sheet2", "sheetId": 1}},
            ]
        }
        # options.py imported _transport directly, so patch there
        with patch(
            "noodle_nodes.integrations_v2.providers.google_sheets.options._transport"
        ) as mock_t:
            t = _mock_transport(fake_response)
            mock_t.return_value = t
            options = call_loader(
                "google_sheets.list_sheet_names",
                spreadsheet_id="sid",
                credentials={"access_token": "tok"},
            )
        assert len(options) == 2
        assert options[0]["value"] == "Sheet1"
        assert options[1]["label"] == "Sheet2"

    def test_list_sheet_names_empty_spreadsheet_id(self):
        options = call_loader("google_sheets.list_sheet_names", spreadsheet_id="")
        assert options == []

    def test_list_header_columns_happy_path(self):
        fake_response = {"values": [["Name", "Email", "Score"]]}
        # options.py imported _transport directly, so patch there
        with patch(
            "noodle_nodes.integrations_v2.providers.google_sheets.options._transport"
        ) as mock_t:
            t = _mock_transport(fake_response)
            mock_t.return_value = t
            options = call_loader(
                "google_sheets.list_header_columns",
                spreadsheet_id="sid",
                sheet_name="Data",
                credentials={"access_token": "tok"},
            )
        assert [o["value"] for o in options] == ["Name", "Email", "Score"]

    def test_list_header_columns_empty_spreadsheet_id(self):
        options = call_loader("google_sheets.list_header_columns", spreadsheet_id="")
        assert options == []


# ---------------------------------------------------------------------------
# Microsoft Outlook v2
# ---------------------------------------------------------------------------


@pytest.fixture()
def ms_creds():
    return {"access_token": "ms-test-token"}


def test_outlook_v2_manifests_use_clean_names_and_brand_icons() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}

    expected = {
        "outlook_send_mail_v2": ("Outlook Send Email", True),
        "outlook_list_messages_v2": ("Outlook List Messages", False),
        "outlook_get_message_v2": ("Outlook Get Message", False),
        "outlook_list_message_attachments_v2": (
            "Outlook List Message Attachments",
            False,
        ),
        "outlook_get_message_attachment_v2": (
            "Outlook Get Message Attachment",
            False,
        ),
        "outlook_list_calendar_events_v2": ("Outlook List Calendar Events", False),
        "outlook_create_draft_v2": ("Outlook Create Draft", True),
        "outlook_reply_message_v2": ("Outlook Reply To Message", True),
        "outlook_forward_message_v2": ("Outlook Forward Message", True),
        "outlook_send_draft_v2": ("Outlook Send Draft", True),
        "outlook_update_message_v2": ("Outlook Update Message", True),
        "outlook_delete_message_v2": ("Outlook Delete Message", True),
        "outlook_create_calendar_event_v2": ("Outlook Create Calendar Event", True),
        "outlook_update_calendar_event_v2": ("Outlook Update Calendar Event", True),
        "outlook_delete_calendar_event_v2": ("Outlook Delete Calendar Event", True),
    }
    for node_id, (name, side_effecting) in expected.items():
        manifest = manifests[node_id]
        assert manifest.name == name
        assert manifest.icon == "brand:microsoftoutlook"
        assert manifest.usable_as_tool is True
        assert manifest.tool_side_effecting is side_effecting


def _ms_mock_transport(return_value: Any):
    m = MagicMock()
    m.request.return_value = return_value
    return m


class TestOutlookSendMail:
    def test_requires_to(self, ms_creds):
        with pytest.raises(ValueError, match="'to'"):
            send_mail(credentials=ms_creds, to="", subject="Hi", body="Hello")

    def test_requires_subject(self, ms_creds):
        with pytest.raises(ValueError, match="'subject'"):
            send_mail(credentials=ms_creds, to="a@b.com", subject="", body="Hello")

    def test_posts_to_sendmail(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport(None)
            mock_t.return_value = t
            send_mail(
                credentials=ms_creds,
                to="a@b.com, c@d.com",
                subject="Test",
                body="<p>Hi</p>",
            )
        t.request.assert_called_once()
        method, path = t.request.call_args[0]
        assert method == "POST"
        assert path == "/me/sendMail"

    def test_multiple_recipients(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport(None)
            mock_t.return_value = t
            send_mail(credentials=ms_creds, to="a@b.com, c@d.com", subject="T", body="B")
        body = t.request.call_args[1]["json_body"]
        assert len(body["message"]["toRecipients"]) == 2

    def test_cc_and_bcc(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport(None)
            mock_t.return_value = t
            send_mail(
                credentials=ms_creds,
                to="a@b.com",
                subject="T",
                body="B",
                cc="cc@b.com",
                bcc="bcc@b.com",
            )
        msg = t.request.call_args[1]["json_body"]["message"]
        assert "ccRecipients" in msg
        assert "bccRecipients" in msg


class TestOutlookListMessages:
    def test_calls_inbox_by_default(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport({"value": []})
            mock_t.return_value = t
            list_messages(credentials=ms_creds)
        path = t.request.call_args[0][1]
        assert "Inbox" in path

    def test_limit_clamped(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport({"value": []})
            mock_t.return_value = t
            list_messages(credentials=ms_creds, limit=200)
        params = t.request.call_args[1]["params"]
        assert params["$top"] == 100

    def test_search_sets_search_param(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport({"value": []})
            mock_t.return_value = t
            list_messages(credentials=ms_creds, search="invoice")
        params = t.request.call_args[1]["params"]
        assert "$search" in params
        assert "invoice" in params["$search"]
        # $orderby should not be present when $search is used
        assert "$orderby" not in params


class TestOutlookGetMessage:
    def test_requires_message_id(self, ms_creds):
        with pytest.raises(ValueError, match="message_id"):
            get_message(credentials=ms_creds, message_id="")

    def test_calls_correct_path(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport({"id": "abc"})
            mock_t.return_value = t
            result = get_message(credentials=ms_creds, message_id="abc123")
        path = t.request.call_args[0][1]
        assert path == "/me/messages/abc123"
        assert result["id"] == "abc"

    def test_attachment_nodes(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport({"value": []})
            mock_t.return_value = t
            list_message_attachments(credentials=ms_creds, message_id="abc123", limit=200)
            get_message_attachment(
                credentials=ms_creds,
                message_id="abc123",
                attachment_id="att123",
            )
        assert t.request.call_args_list[0].args == (
            "GET",
            "/me/messages/abc123/attachments",
        )
        assert t.request.call_args_list[0].kwargs == {
            "operation": "list_message_attachments",
            "params": {"$top": 100},
        }
        assert t.request.call_args_list[1].args == (
            "GET",
            "/me/messages/abc123/attachments/att123",
        )
        assert t.request.call_args_list[1].kwargs == {
            "operation": "get_message_attachment"
        }


class TestOutlookCalendar:
    def test_calls_events_endpoint(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport({"value": []})
            mock_t.return_value = t
            list_calendar_events(credentials=ms_creds)
        path = t.request.call_args[0][1]
        assert path == "/me/events"

    def test_date_filter_applied(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport({"value": []})
            mock_t.return_value = t
            list_calendar_events(
                credentials=ms_creds,
                start_datetime="2024-01-01T00:00:00Z",
                end_datetime="2024-01-31T23:59:59Z",
            )
        params = t.request.call_args[1]["params"]
        assert "$filter" in params
        assert "2024-01-01" in params["$filter"]


class TestOutlookMessageActions:
    def test_create_draft_posts_message(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport({"id": "draft1"})
            mock_t.return_value = t
            create_draft(
                credentials=ms_creds,
                to="a@b.com",
                subject="Draft",
                body="Body",
            )
        t.request.assert_called_once_with(
            "POST",
            "/me/messages",
            operation="create_draft",
            json_body={
                "subject": "Draft",
                "body": {"contentType": "HTML", "content": "Body"},
                "toRecipients": [{"emailAddress": {"address": "a@b.com"}}],
            },
        )

    def test_reply_forward_send_and_delete_message(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport({})
            mock_t.return_value = t
            reply_message(credentials=ms_creds, message_id="msg1", comment="Thanks")
            forward_message(
                credentials=ms_creds,
                message_id="msg1",
                to="lead@example.com",
                comment="FYI",
            )
            send_draft(credentials=ms_creds, message_id="draft1")
            delete_message(credentials=ms_creds, message_id="msg2")

        assert t.request.call_args_list[0].args == ("POST", "/me/messages/msg1/reply")
        assert t.request.call_args_list[0].kwargs == {
            "operation": "reply_message",
            "json_body": {"comment": "Thanks"},
        }
        assert t.request.call_args_list[1].args == ("POST", "/me/messages/msg1/forward")
        assert t.request.call_args_list[1].kwargs["json_body"]["toRecipients"] == [
            {"emailAddress": {"address": "lead@example.com"}}
        ]
        assert t.request.call_args_list[2].args == ("POST", "/me/messages/draft1/send")
        assert t.request.call_args_list[3].args == ("DELETE", "/me/messages/msg2")

    def test_update_message_payload(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport({"id": "msg1"})
            mock_t.return_value = t
            update_message(
                credentials=ms_creds,
                message_id="msg1",
                is_read=True,
                categories="Important, Customer",
            )
        t.request.assert_called_once_with(
            "PATCH",
            "/me/messages/msg1",
            operation="update_message",
            json_body={"isRead": True, "categories": ["Important", "Customer"]},
        )


class TestOutlookCalendarActions:
    def test_create_update_and_delete_calendar_event(self, ms_creds):
        with patch(
            "noodle_nodes.integrations_v2.providers.microsoft_outlook.operations._transport"
        ) as mock_t:
            t = _ms_mock_transport({"id": "evt1"})
            mock_t.return_value = t
            create_calendar_event(
                credentials=ms_creds,
                subject="Call",
                start_datetime="2024-01-01T10:00:00",
                end_datetime="2024-01-01T10:30:00",
                time_zone="Australia/Sydney",
                location="Teams",
                attendees=["a@b.com"],
            )
            update_calendar_event(
                credentials=ms_creds,
                event_id="evt1",
                subject="Updated call",
                is_all_day=False,
            )
            delete_calendar_event(credentials=ms_creds, event_id="evt1")

        assert t.request.call_args_list[0].args == ("POST", "/me/events")
        assert t.request.call_args_list[0].kwargs["json_body"] == {
            "subject": "Call",
            "start": {
                "dateTime": "2024-01-01T10:00:00",
                "timeZone": "Australia/Sydney",
            },
            "end": {
                "dateTime": "2024-01-01T10:30:00",
                "timeZone": "Australia/Sydney",
            },
            "location": {"displayName": "Teams"},
            "attendees": [
                {"emailAddress": {"address": "a@b.com"}, "type": "required"}
            ],
            "isAllDay": False,
        }
        assert t.request.call_args_list[1].args == ("PATCH", "/me/events/evt1")
        assert t.request.call_args_list[1].kwargs == {
            "operation": "update_calendar_event",
            "json_body": {"subject": "Updated call", "isAllDay": False},
        }
        assert t.request.call_args_list[2].args == ("DELETE", "/me/events/evt1")
