# Provider Coverage Matrix

This matrix tracks production-readiness for official provider nodes. V2
providers are spec-driven, Python-native nodes generated from
`OperationSpec`/`ProviderTriggerSpec`; their source viewer still shows ordinary
Python function signatures.

| Provider | V2 operations | V2 triggers | Credentials | Status | Test coverage | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Google Sheets | Create spreadsheet, metadata, add/rename/delete sheet, read/append/update/clear values, batch update values, lookup rows, upsert row | Planned | `google_sheets_oauth2` | Beta | Mocked operation and dynamic-option tests | Uses `GoogleTransport`, dynamic sheet/header option loaders, and shared provider retry/debug events; watch-channel triggers remain planned. |
| Microsoft Outlook | Send mail, list/get/update/delete messages, create/reply/forward/send draft, list/get message attachments, list/create/update/delete calendar events | Planned | Microsoft Graph OAuth credential | Beta | Mocked operation tests | Uses Microsoft Graph transport and v2 operation specs; folder/calendar dynamic options, artifact-backed attachment downloads, and Graph subscription renewal remain planned. |
| GitHub | Get repository, list/get/create/update issues, create/list issue comments, list/get/create/merge pull requests, get/create/update file contents, dispatch workflow, create release | Repository webhook trigger | GitHub token + generic webhook secret | Beta | Mocked operation tests plus lifecycle, signature, ping, dedupe, status, audit, and dispatch trigger tests | Provider subscriptions are lifecycle-managed on workflow activation/publish and expose status in the workflow UI. Legacy GitHub operation wrappers are hidden in favor of v2 nodes; branch/label/workflow dynamic options remain planned. |
| OpenAI-compatible chat | AI model supplier | N/A | `llm_provider` | Beta | Mocked adapter tests | Includes OpenAI, OpenRouter, Ollama, and compatible endpoints. Optional cost estimates use configured per-1M token rates. |
| Azure OpenAI chat | AI model supplier | N/A | `azure_openai_api_key` | Beta | Mocked adapter tests | Uses Azure deployment endpoint and optional cost estimates. |
| Anthropic chat | AI model supplier | N/A | `anthropic_api_key` | Beta | Mocked adapter tests | Supports Claude text/tool-use responses and optional cost estimates. |
| OpenAI-compatible embeddings | Embedding model supplier | N/A | `embedding_provider` | Beta | Mocked adapter tests | Optional embedding cost estimate when provider returns prompt token usage. |
| Cohere embeddings | Embedding model supplier | N/A | `embedding_provider` | Beta | Supplier tests | Usage/cost estimate waits on provider usage normalization. |
| Qdrant | Vector-store adapter | N/A | URL/API key params | Beta | Mocked REST tests | Supports create collection, upsert, query, and delete through RAG nodes. |
| Slack | Send message, reply in thread, update message, delete message, add reaction, open direct message, list channels, list users | Signed event trigger (`slack_event_trigger_v2`) | Slack bot token + signing secret | Beta | Mocked operation tests, HMAC signature tests, event dispatch tests | Legacy Slack send-message wrapper is hidden in favor of v2 nodes. |
| Stripe | Create/get/list/update customer, create checkout session, create/get/list payment intent, create refund, create/list invoices, create/list/cancel subscriptions, list products/prices | Signed event trigger (`stripe_event_trigger_v2`) | Stripe API key + webhook secret | Beta | Mocked operation tests, HMAC signature tests, event dispatch tests | Legacy create-customer wrapper is hidden in favor of v2 nodes; idempotency keys remain planned. |
| Airtable | List/get/create/update/delete records, batch create/update records, upsert records | Planned | Airtable token | Beta | Mocked operation tests | Legacy Airtable list/create wrappers are hidden in favor of v2 nodes; metadata loaders and webhook/polling triggers remain planned. |
| Notion | Search, query/get database, get/create/update/archive page, list/append block children | New-page polling trigger (`notion_new_database_page_trigger_v2`, 5 min) | Notion integration token | Beta | Mocked operation tests, polling cursor tests | Legacy create-page wrapper is hidden in favor of v2 nodes; database/page/property option loaders remain planned. |
| Google Sheets (polling) | — | New-row polling trigger (`google_sheets_new_row_trigger_v2`, 5 min) | `google_sheets_oauth2` | Beta | Mocked polling tests with cursor assertions | Supplements the existing v2 operation nodes; watch-channel triggers remain planned. |
| RSS / Atom | — | New-item polling trigger (`rss_feed_trigger`, 15 min) | None (public feeds) | Beta | RSS + Atom parsing tests, cursor ring-buffer tests | Stdlib XML parsing, no external HTTP library beyond `requests`; Atom namespace support included. |
| Google Drive | None | Planned | Google OAuth credential | Planned | None | Candidate for file-created/updated triggers and file operations. |
| Generic HTTP/Webhook | Built-in nodes | Built-in webhook trigger | Optional auth params | Shipped | API and node tests | HTTP nodes now block private/loopback/link-local targets before dispatch. |

## File Reading Nodes

| Node | Sources | Dataset mode | Requirements | Status | Tests |
| --- | --- | --- | --- | --- | --- |
| `read_text_file` | Server path, browser upload (artifact_id) | — | stdlib | Shipped | Path + upload + encoding tests |
| `read_csv_file` | Server path, browser upload | `output_as_dataset` toggle → DatasetRef | stdlib | Shipped | Raw mode, no-header, empty, dataset mode, missing-source tests |
| `read_json_file` | Server path, browser upload | `output_as_dataset` requires array of objects | stdlib | Shipped | Raw array, raw object, dataset mode, non-array error tests |
| `read_xml_file` | Server path, browser upload | `output_as_dataset` + `row_xpath` extracts elements | `xmltodict` | Shipped | Raw mode, dataset mode with row extraction, missing-xpath error tests |

Browser uploads use the `file_upload` widget in the node UI and are stored via `POST /artifacts/upload` as pre-run artifacts. The node reads bytes from `{artifacts_dir}/uploads/{artifact_id}/{filename}` at execution time.

## Status Definitions

- **Shipped**: default production path with broad tests and UI coverage.
- **Beta**: implemented and tested, but provider coverage is still expanding.
- **Planned**: architecture and follow-up work identified, no v2 node yet.

## Next Provider Targets

1. Outlook/Microsoft Graph subscription trigger with renewal handling.
2. Google Drive/Sheets watch-channel triggers with renewal handling.
3. Airtable metadata loaders, webhook/polling triggers.
4. Notion database/page option loaders and additional query/update operations.
5. Add more GitHub operations such as comments, pull requests, releases, and file contents.
6. File nodes: S3 / GCS / Azure Blob read support, streaming for large files.
