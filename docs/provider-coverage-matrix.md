# Provider Coverage Matrix

This matrix tracks production-readiness for official provider nodes. V2
providers are spec-driven, Python-native nodes generated from
`OperationSpec`/`ProviderTriggerSpec`; their source viewer still shows ordinary
Python function signatures.

| Provider | V2 operations | V2 triggers | Credentials | Status | Test coverage | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Google Sheets | Read values, append values, update values, clear values, spreadsheet metadata | Planned | `google_sheets_oauth2` | Beta | Mocked operation and dynamic-option tests | Uses `GoogleTransport`, dynamic sheet/header option loaders, and shared provider retry/debug events. |
| Microsoft Outlook | Send mail, list messages, get message, list calendar events | Planned | Microsoft Graph OAuth credential | Beta | Mocked operation tests | Uses Microsoft Graph transport and v2 operation specs. |
| GitHub | Get repository, create issue | Repository webhook trigger | GitHub token + generic webhook secret | Beta | Mocked operation tests plus lifecycle, signature, ping, dedupe, status, audit, and dispatch trigger tests | Provider subscriptions are lifecycle-managed on workflow activation/publish and expose status in the workflow UI. Legacy GitHub operation wrappers are hidden in favor of v2 nodes. |
| OpenAI-compatible chat | AI model supplier | N/A | `llm_provider` | Beta | Mocked adapter tests | Includes OpenAI, OpenRouter, Ollama, and compatible endpoints. Optional cost estimates use configured per-1M token rates. |
| Azure OpenAI chat | AI model supplier | N/A | `azure_openai_api_key` | Beta | Mocked adapter tests | Uses Azure deployment endpoint and optional cost estimates. |
| Anthropic chat | AI model supplier | N/A | `anthropic_api_key` | Beta | Mocked adapter tests | Supports Claude text/tool-use responses and optional cost estimates. |
| OpenAI-compatible embeddings | Embedding model supplier | N/A | `embedding_provider` | Beta | Mocked adapter tests | Optional embedding cost estimate when provider returns prompt token usage. |
| Cohere embeddings | Embedding model supplier | N/A | `embedding_provider` | Beta | Supplier tests | Usage/cost estimate waits on provider usage normalization. |
| Qdrant | Vector-store adapter | N/A | URL/API key params | Beta | Mocked REST tests | Supports create collection, upsert, query, and delete through RAG nodes. |
| Slack | Send message | Planned | Slack bot token | Beta | Mocked operation tests and AI-builder/template coverage | Legacy Slack send-message wrapper is hidden in favor of `slack_send_message_v2`; signed event triggers remain planned. |
| Stripe | Create customer | Planned | Stripe API key | Beta | Mocked operation tests | Legacy create-customer wrapper is hidden in favor of `stripe_create_customer_v2`; signed webhook trigger tests are still needed before production triggers. |
| Airtable | List records, create record | Planned | Airtable token | Beta | Mocked operation tests | Legacy Airtable list/create wrappers are hidden in favor of v2 nodes; metadata loaders and update/delete operations remain planned. |
| Notion | Create page | Planned | Notion integration token | Beta | Mocked operation tests | Legacy create-page wrapper is hidden in favor of `notion_create_page_v2`; database/page option loaders and additional operations remain planned. |
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
