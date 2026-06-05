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
| Slack | Send message, reply in thread, update message, delete message, add reaction, open direct message, list channels, list users | Planned | Slack bot token | Beta | Mocked operation tests and AI-builder/template coverage | Legacy Slack send-message wrapper is hidden in favor of v2 nodes; signed event triggers, file upload, and richer dynamic options remain planned. |
| Stripe | Create/get/list/update customer, create checkout session, create/get/list payment intent, create refund, create/list invoices, create/list/cancel subscriptions, list products/prices | Planned | Stripe API key | Beta | Mocked operation tests | Legacy create-customer wrapper is hidden in favor of v2 nodes; idempotency keys and signed webhook triggers remain planned. |
| Airtable | List/get/create/update/delete records, batch create/update records, upsert records | Planned | Airtable token | Beta | Mocked operation tests | Legacy Airtable list/create wrappers are hidden in favor of v2 nodes; metadata loaders and webhook/polling triggers remain planned. |
| Notion | Search, query/get database, get/create/update/archive page, list/append block children | Planned | Notion integration token | Beta | Mocked operation tests | Legacy create-page wrapper is hidden in favor of v2 nodes; database/page/property option loaders and polling triggers remain planned. |
| Google Drive | None | Planned | Google OAuth credential | Planned | None | Candidate for file-created/updated triggers and file operations. |
| Generic HTTP/Webhook | Built-in nodes | Built-in webhook trigger | Optional auth params | Shipped | API and node tests | HTTP nodes now block private/loopback/link-local targets before dispatch. |

## Status Definitions

- **Shipped**: default production path with broad tests and UI coverage.
- **Beta**: implemented and tested, but provider coverage is still expanding.
- **Planned**: architecture and follow-up work identified, no v2 node yet.

## Next Provider Targets

1. Slack signed event trigger.
2. Stripe signed webhook trigger and additional Stripe operations.
3. Outlook/Microsoft Graph subscription trigger with renewal handling.
4. Google Drive/Sheets watch-channel triggers with renewal handling.
5. Airtable metadata loaders plus update/delete operations.
6. Notion database/page option loaders plus query/update operations.
7. Add more GitHub operations such as comments, pull requests, releases, and file contents.
