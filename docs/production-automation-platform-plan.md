# Production Python Automation Platform Plan

This plan describes how to evolve Noodle into a production-grade Python automation
platform with n8n-class integrations and AI workflow capabilities. n8n is used as
a design and catalog reference only. Noodle should remain Python-native and should
not copy n8n implementation code.

Because Noodle is still in development and has no existing users, this plan
assumes we can replace the current AI nodes instead of preserving every legacy
behavior. Existing node IDs can be migrated or removed if a cleaner production
architecture is better.

## Goals

- Build a production-ready Python automation engine, not just a larger list of
  HTTP wrapper nodes.
- Support n8n-style provider integrations such as Google Sheets, Outlook, Slack,
  GitHub, Notion, Salesforce, Airtable, Jira, Stripe, Shopify, and long-tail SaaS.
- Support first-class AI workflows with typed AI ports, model suppliers, memory,
  tools, output parsers, guardrails, RAG, vector stores, and agent tracing.
- Make integrations declarative, testable, observable, credential-aware, and
  secure by default.
- Keep Noodle's Python SDK ergonomic for custom nodes and internal provider
  implementations.
- Preserve the "node as Python" model: every executable built-in, integration,
  AI node, trigger handler, and generated custom node should resolve to Python
  source that can be inspected and forked from the editor.

## Non-Goals

- Do not port n8n TypeScript files line-for-line.
- Do not preserve current AI node behavior if it blocks a cleaner architecture.
- Do not add hundreds of provider nodes before the integration framework,
  credential system, and runtime semantics are ready.
- Do not make AI agents opaque black boxes. Agent steps, tool calls, approvals,
  and costs must be visible in run history.

## Current State Summary

Noodle already has useful foundations:

- Python node SDK in `packages/core/noodle/sdk.py`.
- DAG execution engine in `packages/core/noodle/engine.py`.
- Basic manifest models in `packages/core/noodle/models.py`.
- React editor and inspector in `apps/web/src/editor/`.
- Encrypted credential storage and reference resolution in
  `apps/api/app/services/credentials.py`.
- Credential test handlers in `apps/api/app/services/credential_tests.py`.
- Existing integrations in `packages/nodes/noodle_nodes/integrations.py` and
  `packages/nodes/noodle_nodes/saas.py`.
- Prototype AI nodes in `packages/nodes/noodle_nodes/llm.py` and
  `packages/nodes/noodle_nodes/ai_extra.py`.

The current AI nodes are useful prototypes, but they pass dictionaries through
normal ports and run tool calls inline. For production, the workflow engine needs
to understand AI-specific connections and agent tool execution.

## Architecture Invariants

These constraints should stay true while Noodle grows toward n8n-class coverage:

- Nodes remain Python-native. Integration specs may generate nodes, but the
  generated runtime target must still be Python functions registered through the
  Noodle SDK.
- The editor's Python view remains first-class. Built-in nodes expose their
  Python source through `/nodes/{node_type}/source`; custom nodes expose
  editable source from code modules. The older side-inspector `Show code` button
  should be removed once the NDV `Inspector | Python` toggle is the standard
  path.
- n8n is a design and catalog reference only. Noodle should not introduce a
  TypeScript node runtime or require provider operations to be implemented in
  JavaScript.
- Declarative specs are allowed only as authoring metadata. They should produce
  manifests, UI fields, credentials, tests, and Python execution functions.
- Generated nodes should include source provenance so the Python panel can show
  useful code instead of opaque generated wrappers.

## LLM Handoff Guide

This document is intended to be sufficient for another LLM or engineer to resume
the work after context loss. When continuing:

1. Read this file first.
2. Run `git status --short --branch` in `D:\noodle` before editing.
3. Do not revert uncommitted changes unless the user explicitly asks.
4. Treat `feat/ndv-param-grouping` as planned baseline work if it has not yet
   been merged to `main`.
5. Keep the implementation Python-native. Do not introduce a JavaScript node
   runtime.
6. Use `D:\n8n-master` only for design, catalog, and behavior reference. Do not
   copy n8n source code.
7. Update this plan whenever a phase is completed, changed, or blocked.

Important local reference files:

- Core manifest models: `packages/core/noodle/models.py`
- Python node SDK: `packages/core/noodle/sdk.py`
- Execution engine: `packages/core/noodle/engine.py`
- Built-in HTTP and webhook nodes: `packages/nodes/noodle_nodes/builtin.py`
- Current simple integration wrappers:
  `packages/nodes/noodle_nodes/integrations.py`
- Current SaaS nodes: `packages/nodes/noodle_nodes/saas.py`
- Current AI nodes: `packages/nodes/noodle_nodes/llm.py`
- Current extra AI/provider nodes: `packages/nodes/noodle_nodes/ai_extra.py`
- Credential service: `apps/api/app/services/credentials.py`
- Credential tests: `apps/api/app/services/credential_tests.py`
- Credential routes: `apps/api/app/routers/credentials.py`
- Node manifest/source routes: `apps/api/app/routers/nodes.py`
- Editor manifest types: `apps/web/src/types.ts`
- NDV modal panels: `apps/web/src/editor/NDVPanels.tsx`
- Source viewer/forker: `apps/web/src/editor/NodeDetails.tsx::NodeCodePanel`
- Connection validation: `apps/web/src/editor/connectionValidation.ts`
- Credentials UI: `apps/web/src/CredentialsPage.tsx`
- AI workflow draft builder: `apps/api/app/services/ai_builder.py`

n8n reference entry points already inspected:

- Outlook operation descriptions:
  `D:\n8n-master\packages\nodes-base\nodes\Microsoft\Outlook\v2\actions`
- Outlook transport:
  `D:\n8n-master\packages\nodes-base\nodes\Microsoft\Outlook\v2\transport`
- Outlook credential:
  `D:\n8n-master\packages\nodes-base\credentials\MicrosoftOutlookOAuth2Api.credentials.ts`
- Google Sheets operation descriptions:
  `D:\n8n-master\packages\nodes-base\nodes\Google\Sheet\v2\actions`
- Google Sheets credential:
  `D:\n8n-master\packages\nodes-base\credentials\GoogleSheetsOAuth2Api.credentials.ts`
- Common Google OAuth credential:
  `D:\n8n-master\packages\nodes-base\credentials\GoogleOAuth2Api.credentials.ts`
- AI Agent V3:
  `D:\n8n-master\packages\@n8n\nodes-langchain\nodes\agents\Agent\V3`
- Agent execution helpers:
  `D:\n8n-master\packages\@n8n\nodes-langchain\utils\agent-execution`

After each meaningful implementation chunk, record:

- files changed
- tests run
- known risks
- remaining work package IDs

Use the work package IDs below in commit messages and handoff summaries.

## n8n Reference Patterns To Adopt

n8n's catalog is valuable as a design reference because it separates:

- credential type definitions
- node description metadata
- resource and operation routing
- provider transport helpers
- dynamic option loaders
- operation executors
- AI subnodes such as language models, tools, memory, output parsers, retrievers,
  and vector stores
- engine-mediated agent tool execution

Noodle should adopt these patterns in Python without copying code.

## Target Architecture

The production architecture has four major layers:

1. Core workflow engine
2. Integration SDK and provider runtime
3. AI runtime and typed AI nodes
4. Editor, credentials, observability, and deployment tooling

```text
packages/core/noodle/
  models.py              # manifests, ports, params, execution models
  sdk.py                 # @node and new @integration_node helpers
  engine.py              # DAG execution plus agent action resume loop
  ai_runtime.py          # model/tool/memory/output-parser protocols

packages/nodes/noodle_nodes/
  integrations_v2/
    specs.py
    registry.py
    transport.py
    oauth.py
    dynamic_options.py
    providers/
  ai_v2/
    models.py
    agents.py
    tools.py
    memory.py
    parsers.py
    retrievers.py
    vectorstores.py
    guardrails.py
```

## Core Runtime Changes

### 1. Rich Port Types

Extend `PortDataKind` beyond the current generic data kinds.

Required connection types:

- `main`
- `control`
- `dataset`
- `artifact`
- `file`
- `ai_language_model`
- `ai_embedding_model`
- `ai_memory`
- `ai_tool`
- `ai_output_parser`
- `ai_retriever`
- `ai_vector_store`
- `ai_document_loader`
- `ai_guardrail`

Editor validation should reject incompatible connections before a workflow runs.
The engine should also validate them server-side.

### 2. Supplier Nodes

Add a node role concept:

- `executable`: normal node, produces run output
- `supplier`: supplies a runtime object or config to another node
- `trigger`: starts runs
- `tool`: executable by an AI agent
- `output_parser`: validates or transforms AI output

Supplier nodes are critical for AI. A chat model node should supply a model
adapter/config to the agent, not behave like a normal data node unless explicitly
run in standalone mode.

### 3. Rich Param Metadata

Add fields to `ParamSpec`:

- `group`
- `display_name`
- `display_when`
- `hide_when`
- `widget`
- `depends_on`
- `load_options`
- `resource_mapper`
- `fixed_collection`
- `credential_type`
- `required_scopes`
- `advanced`
- `documentation_url`
- `validation`

This unlocks n8n-style UI behavior for both integrations and AI.

The `feat/ndv-param-grouping` branch should be treated as the first completed
slice of this work. It adds `ParamSpec.group` and the inspector "Add option"
chip behavior. Future metadata should extend that model rather than replace it.
For simple nodes, use `@node(param_groups={"Options": [...]})`; for generated
integration nodes, have the operation spec populate `group` on optional fields.

### 4. Engine-Mediated Tool Execution

Replace inline AI tool execution with an engine request/resume model.

Flow:

1. Agent runs and asks for one or more tool calls.
2. Agent returns `AgentActionRequest`.
3. Engine records the tool calls as run events.
4. Engine executes selected tool nodes or subworkflows.
5. Engine returns `AgentActionResponse`.
6. Agent resumes with observations.
7. Loop continues until final answer, max steps, cancellation, or error.

This gives production-level observability and enables human approval gates.

### 5. Durable Agent State

Add agent run state so long agent runs can pause and resume:

- current iteration
- prior model messages
- prior tool requests
- observations
- memory load/save metadata
- pending approval state
- cancellation state

Store this in the run/event tables instead of process memory.

## Integration SDK

Create `packages/nodes/noodle_nodes/integrations_v2/`.

Core objects:

- `CredentialTypeSpec`
- `IntegrationSpec`
- `ResourceSpec`
- `OperationSpec`
- `ParamSpecBuilder`
- `DynamicOptionLoader`
- `Transport`
- `TriggerSpec`

Example:

```python
IntegrationSpec(
    id="google_sheets",
    name="Google Sheets",
    credential_types=["google_sheets_oauth2", "google_service_account"],
    resources=[
        ResourceSpec(
            id="sheet",
            operations=["read", "append", "update", "clear", "create", "delete"],
        ),
        ResourceSpec(
            id="spreadsheet",
            operations=["create", "delete", "get"],
        ),
    ],
)
```

The SDK should generate manifests and register executable node functions from
these specs.

The generated function should be real Python and should remain compatible with
the existing source/fork workflow. For example, a generated Google Sheets append
node can delegate to a shared provider operation, but the editor should still be
able to show a Python function with clear imports, parameters, credential usage,
and the provider call it performs.

## Credential and OAuth Architecture

Move credential presets from React into backend credential type specs.

Add endpoints:

- `GET /credentials/types`
- `GET /credentials/test-handlers`
- `POST /credentials`
- `PUT /credentials/{id}`
- `POST /credentials/{id}/test`
- `POST /credentials/oauth/start`
- `GET /credentials/oauth/callback`
- `POST /credentials/{id}/refresh`

Credential records should support:

- encrypted access token
- encrypted refresh token
- expiry timestamp
- OAuth scopes
- provider metadata
- service account JSON
- auth method
- test handler
- environment/workflow/runner-pool scope

Provider OAuth support should include:

- Google OAuth2
- Microsoft OAuth2
- Slack OAuth2
- GitHub OAuth2
- HubSpot OAuth2
- Salesforce OAuth2
- generic OAuth2

The runner should refresh credentials before execution when possible. If refresh
fails, the node should fail with a clear credential error, not a generic HTTP
error.

## Provider Transport Layer

Create shared transports:

- `GoogleTransport`
- `MicrosoftGraphTransport`
- `SlackTransport`
- `GitHubTransport`
- `GenericRestTransport`
- `AwsTransport`
- `DatabaseTransport`
- `VectorStoreTransport`

Each transport should handle:

- auth headers
- OAuth refresh
- pagination
- retries
- rate-limit backoff
- idempotency keys
- file upload/download
- structured errors
- secret redaction
- request/response debug metadata

Integration operation code should not manually repeat this logic.

## Replacing HTTP Wrapper Nodes

The current integration nodes are mostly direct HTTP wrappers. They manually
read credentials, build URLs, call `requests`, parse the response, and raise
their own error strings. That is fine for prototypes, but it will not scale to
production coverage.

The replacement model is:

```text
CredentialTypeSpec
  -> Provider Transport
  -> IntegrationSpec
  -> ResourceSpec
  -> OperationSpec
  -> generated Python node function
  -> registered NodeManifest
```

The user still sees normal nodes on the canvas. The difference is that the node
implementation delegates common behavior to a provider transport and an
operation object instead of reimplementing HTTP behavior inline.

Example target shape:

```python
@integration_node(
    provider="google_sheets",
    resource="sheet",
    operation="append",
)
async def google_sheets_append(
    input: Any = None,
    credentials: CredentialRef | None = None,
    spreadsheet_id: str = "",
    range_name: str = "Sheet1!A1",
    values: list[list[Any]] | None = None,
    value_input_option: str = "USER_ENTERED",
) -> dict[str, Any]:
    transport = await GoogleSheetsTransport.from_credentials(credentials)
    return await transport.append_values(
        spreadsheet_id=spreadsheet_id,
        range_name=range_name,
        values=values or values_from_input(input),
        value_input_option=value_input_option,
    )
```

This function should be real Python source and should show in the NDV Python
panel. The implementation may be generated from specs, but the source panel must
not show only an opaque generic executor call with no useful context.

### Current Wrapper To v2 Migration Rules

- Keep `HTTP Request` as a generic custom API node.
- Replace provider-specific wrappers such as Google Sheets, Outlook, Slack,
  GitHub, Notion, Airtable, Stripe, and database wrappers with v2 provider
  operations.
- A v2 operation is considered ready when it has credential spec, parameter
  spec, operation executor, mocked HTTP tests, structured errors, pagination
  behavior if applicable, and editor metadata.
- Mark old nodes as legacy only after the v2 equivalent exists and starter
  workflows/templates are updated.
- Remove old provider-specific wrappers after the replacement provider passes
  mocked operation tests and at least one end-to-end workflow test.

### Wrapper Replacement Acceptance Criteria

For each replaced provider node:

- Node remains visible in the palette with clear provider grouping.
- Node parameters are generated from `OperationSpec`.
- Optional params use `ParamSpec.group` or `param_groups`.
- Credential selection uses backend credential type specs.
- Provider calls use a transport class, not direct ad hoc HTTP in the node body.
- Errors have stable shape: provider, operation, status, message, retryable,
  request id when available.
- Secrets are redacted in errors, run metadata, and logs.
- Tests cover success, auth failure, provider error, retry/rate-limit behavior,
  and pagination where applicable.
- The NDV Python panel can show and fork meaningful Python source.

## Editor and UI Changes

Update:

- `apps/web/src/types.ts`
- `apps/web/src/editor/NodeDetails.tsx`
- `apps/web/src/editor/connectionValidation.ts`
- `apps/web/src/editor/NodePalette.tsx`
- `apps/web/src/CredentialsPage.tsx`

Required UI capabilities:

- typed handles for main/data/AI connections
- replace the older side inspector `Show code` button with the NDV
  `Inspector | Python` toggle, using the same `NodeCodePanel` implementation
  for source viewing, editing, and forking
- provider operation layout: Resource -> Operation -> Parameters
- optional parameter groups using `ParamSpec.group` and "Add option" chips
- dynamic dropdowns and search lists
- resource locator fields
- table/column mappers
- fixed collections
- OAuth connect button
- credential type browser
- missing scopes warnings
- AI agent step viewer
- tool call viewer
- model usage and cost display
- memory load/save display

### Compatibility With `feat/ndv-param-grouping`

This branch is compatible with the production plan and should be merged before
the larger integration work if it is already green.

Observed branch changes:

- `ParamSpec.group` is added in backend and frontend manifest types.
- `sdk.py` threads per-param `group` metadata into manifests.
- The NDV parameters panel changes from a Show code toggle to an
  `Inspector | Python` segmented view.
- `NodeCodePanel` is still the source viewer/editor/forker used by both the NDV
  and the side inspector path.
- Current working-tree edits also add `@node(param_groups=...)`, a useful
  shorthand for tagging optional params in Python.

Plan impact:

- No architectural conflict. The branch supports the richer metadata direction.
- The branch should be followed by a cleanup that removes the older side
  inspector `Show code` button and makes the NDV `Inspector | Python` toggle the
  only first-class code view.
- The integration SDK should output grouped params from day one.
- AI v2 nodes should put advanced model settings, guardrail options, parser
  options, memory tuning, and retry controls into param groups so the default
  inspector remains focused.
- Move UI coverage to the NDV toggle and keep backend/source-panel coverage for
  `/nodes/{node_type}/source`, built-in source viewing, and custom-node forking.

## Replace Current AI Nodes With AI v2

Because there are no production users yet, replace the current AI nodes rather
than keeping them as the foundation.

Current prototype nodes can be removed or converted into thin compatibility
wrappers after v2 is stable:

- `ai_chat_model`
- `ai_chat`
- `ai_structured_output`
- `ai_text_chunk`
- `ai_batch_embeddings`
- `ai_dataset_map`
- `ai_vector_retriever`
- `ai_rag_answer`
- `ai_memory_buffer`
- `ai_tool`
- `ai_tool_box`
- `ai_agent`
- AI nodes in `ai_extra.py`

New AI v2 modules:

```text
packages/nodes/noodle_nodes/ai_v2/
  providers/
    openai.py
    anthropic.py
    azure_openai.py
    google_gemini.py
    openrouter.py
    ollama.py
    mistral.py
    cohere.py
  models.py
  embeddings.py
  agents.py
  tools.py
  memory.py
  output_parsers.py
  document_loaders.py
  text_splitters.py
  retrievers.py
  vectorstores.py
  guardrails.py
```

## AI v2 Node Catalog

### Model Nodes

- AI Chat Model - OpenAI
- AI Chat Model - Anthropic
- AI Chat Model - Azure OpenAI
- AI Chat Model - Google Gemini
- AI Chat Model - OpenRouter
- AI Chat Model - Ollama
- AI Chat Model - Mistral
- AI Chat Model - Groq
- AI Embeddings - OpenAI
- AI Embeddings - Azure OpenAI
- AI Embeddings - Cohere
- AI Embeddings - Gemini
- AI Embeddings - Ollama

These are supplier nodes with `ai_language_model` or `ai_embedding_model`
outputs.

### Agent Nodes

- AI Agent
- AI Agent Tool
- AI SQL Agent
- AI Retrieval Agent
- AI Plan and Execute Agent

Agent inputs:

- `main`
- `ai_language_model`
- optional fallback `ai_language_model`
- `ai_memory`
- `ai_tool`
- `ai_output_parser`
- `ai_guardrail`

Agent options:

- prompt source: upstream, manual, guardrail output
- system message
- max iterations
- max tokens from memory
- return intermediate steps
- streaming
- fallback model
- side-effect approval mode
- binary/image passthrough
- tracing metadata

### Tool Nodes

- AI HTTP Tool
- AI Workflow Tool
- AI Code Tool
- AI Calculator Tool
- AI Vector Store Tool
- AI Retriever Tool
- AI Think Tool
- AI MCP Client Tool
- AI Integration Tool

The AI Integration Tool should allow any supported integration operation to be
exposed as a tool using the operation's JSON schema.

### Memory Nodes

- AI Buffer Memory
- AI Window Buffer Memory
- AI Postgres Memory
- AI Redis Memory
- AI MongoDB Memory
- AI Zep Memory
- AI Memory Manager

Memory operations:

- load messages
- insert messages
- override messages
- delete last N messages
- clear session
- group or ungroup output

### Output Parser Nodes

- AI Structured Output Parser
- AI List Output Parser
- AI Item List Output Parser
- AI Auto-Fixing Output Parser
- AI JSON Schema Parser

Agents should use output parsers as connected `ai_output_parser` suppliers.

### RAG Nodes

- AI Document Loader - File
- AI Document Loader - PDF
- AI Document Loader - JSON
- AI Document Loader - URL
- AI Document Loader - GitHub
- AI Document Loader - Google Drive
- AI Text Splitter - Character
- AI Text Splitter - Recursive
- AI Text Splitter - Token
- AI Retriever - Vector Store
- AI Retriever - Multi Query
- AI Retriever - Contextual Compression
- AI RAG Chain

### Vector Store Nodes

- Pinecone
- Qdrant
- Chroma
- Supabase
- PGVector
- Redis
- MongoDB Atlas
- Azure AI Search
- Weaviate
- In-memory vector store for local development

Each vector store should support insert, load/query, delete, and tool mode where
practical.

### Guardrail Nodes

- AI Guardrails
- PII check
- secret key check
- keyword policy
- URL safety
- topical alignment
- jailbreak detection
- moderation provider check

Guardrails can run before model calls, after model calls, or as an agent input.

## AI Provider Runtime

Define provider adapters with common protocols:

```python
class ChatModelAdapter:
    capabilities: ModelCapabilities
    async def invoke(messages, tools=None, response_format=None, stream=False): ...

class EmbeddingModelAdapter:
    async def embed(texts): ...
```

Capabilities:

- `supports_tools`
- `supports_json_schema`
- `supports_json_object`
- `supports_streaming`
- `supports_vision`
- `supports_audio`
- `supports_reasoning`
- `supports_batching`

The agent should require a model that supports tool calling unless it is using a
fallback JSON-planning mode.

## Integration Rollout Plan

Build providers in waves after the integration SDK is ready.

### Wave 1 - High Value Foundation

- Google Sheets
- Google Drive
- Gmail
- Google Calendar
- Microsoft Outlook
- Microsoft Excel
- Microsoft Teams
- Microsoft OneDrive
- Slack
- GitHub
- Notion
- Airtable

### Wave 2 - Work Management and CRM

- Jira
- Linear
- Trello
- Asana
- HubSpot
- Salesforce
- Pipedrive
- Zendesk
- Freshdesk

### Wave 3 - Commerce, Data, and Messaging

- Stripe
- Shopify
- WooCommerce
- PayPal
- Xero
- QuickBooks
- Telegram
- Discord
- Twilio
- SendGrid
- Mailchimp

### Wave 4 - Data and DevOps

- Postgres
- MySQL
- MongoDB
- Redis
- Snowflake
- S3
- Cloudflare
- GitLab
- Bitbucket
- Jenkins
- Docker/Kubernetes utility nodes

### Wave 5 - Long-Tail SaaS

Use the n8n node catalog as the inventory reference. Add providers with the
integration generator and coverage matrix.

## Trigger Architecture

Action nodes should ship before provider triggers. Triggers require lifecycle
management and production deployment semantics.

Trigger types:

- manual
- schedule
- polling
- webhook
- provider webhook subscription
- provider event stream

Provider trigger lifecycle:

- create subscription on workflow activation
- verify webhook challenge
- store subscription id and expiry
- renew subscription
- delete subscription on deactivation
- recover failed subscriptions
- dedupe events
- replay/backfill events

Priority trigger providers:

- GitHub webhooks
- Slack events
- Outlook mail/calendar subscriptions
- Google Drive/Sheets watch channels
- Stripe webhooks
- Shopify webhooks

## Production Reliability

Add platform-level reliability features:

- per-node retry policy
- provider-aware retry policy
- exponential backoff and jitter
- rate-limit handling
- idempotency keys
- dead-letter queue
- run cancellation
- run resume
- node-level timeouts
- workflow-level timeouts
- circuit breakers for providers
- checkpointed long-running executions
- queue-backed production runs
- durable scheduler state

## Observability

Extend run events and debug snapshots:

- HTTP request metadata
- provider latency
- response status
- retry count
- credential id used, without secret values
- model provider/model
- prompt/completion token usage
- estimated cost
- agent iteration count
- tool calls requested/completed
- memory loads/saves
- guardrail checks
- dynamic option calls

Expose this in the run UI and API.

## Security

Required controls:

- encrypted credentials with per-credential data encryption keys
- OAuth scope display and validation
- least-privilege credential presets
- no secrets in workflow JSON
- redaction in logs, prompts, outputs, and debug metadata
- private IP protection for HTTP/tool nodes
- SSRF protection
- approval gates for side-effect tools
- audit logs for credential use and destructive actions
- scoped credentials by workflow, environment, and runner pool
- sandboxed code tools
- unsafe node policy for production activation

## Testing Strategy

Add tests for:

- manifest generation
- typed connection validation
- dynamic option loaders
- credential type specs
- OAuth start/callback/refresh
- provider transport retries and pagination
- integration operation payloads
- AI model adapter normalization
- agent tool request/resume loop
- output parsers
- memory load/save/delete
- guardrails
- RAG/vector store behavior
- UI conditional fields
- end-to-end workflow execution

Use mocked provider HTTP fixtures. Live integration tests should be optional and
guarded by environment variables.

## Integration Generator

Add scripts:

```text
scripts/new_integration.py
scripts/new_operation.py
scripts/new_ai_provider.py
```

Generated output should include:

- provider folder
- credential spec
- transport stub
- operation spec
- dynamic option stub
- mocked HTTP tests
- docs stub
- coverage matrix entry

This is necessary to scale toward hundreds of integrations.

## Migration Strategy

Since Noodle has no users yet:

- replace current AI nodes with AI v2
- keep old IDs only if it saves development time
- otherwise delete and regenerate starter workflows/templates
- rewrite `ai_builder` known node specs to target v2 nodes
- update tests to expect v2 node IDs and typed ports

For integrations:

- keep old simple nodes until v2 equivalents exist
- mark old nodes as legacy in manifests
- remove old nodes after v2 provider coverage is stable

## Implementation Phases

### Phase 0 - Planning and Inventory

- Create catalog coverage matrix from n8n providers.
- Mark provider priority and credential type.
- Identify existing Noodle nodes to replace.

### Phase 1 - Core Schema and Editor Support

- Add rich port types.
- Add supplier node role.
- Add conditional/dynamic param metadata.
- Adopt `ParamSpec.group` and `@node(param_groups=...)` from
  `feat/ndv-param-grouping` as the baseline optional-parameter mechanism.
- Add typed connection validation in backend and editor.
- Add dynamic options endpoint.

### Phase 2 - Credentials and OAuth

- Move credential types to backend specs.
- Build OAuth start/callback/refresh.
- Add provider credential specs for Google, Microsoft, Slack, GitHub, OpenAI,
  Anthropic, OpenRouter, Pinecone, Qdrant.

### Phase 3 - Integration SDK

- Build `integrations_v2`.
- Build transports.
- Build operation registration.
- Build integration generator.

### Phase 4 - AI v2 Runtime

- Add model adapter protocols.
- Add supplier nodes.
- Add memory protocol.
- Add tool protocol.
- Add output parser protocol.
- Replace current AI nodes.

### Phase 5 - Agent Engine

- Implement agent action request/resume.
- Add tool execution through engine.
- Add agent tracing.
- Add approval gates.
- Add streaming support where feasible.

### Phase 6 - First Provider Nodes

- Google Sheets v2.
- Microsoft Outlook v2.
- Slack v2.
- GitHub v2.
- Notion v2.
- Airtable v2.

### Phase 7 - RAG Stack

- Document loaders.
- Text splitters.
- Embedding providers.
- Vector stores.
- Retrievers.
- Vector store tools.

### Phase 8 - Triggers

- Webhook subscription lifecycle.
- Polling trigger framework.
- Provider triggers for GitHub, Slack, Stripe, Outlook, Google Drive/Sheets.

### Phase 9 - Production Hardening

- Retry policies.
- Rate-limit handling.
- Durable agent state.
- Run resume.
- Observability UI.
- Security review.
- Load testing.
- Deployment docs.

## Detailed Work Packages

These work packages are the execution backlog. Complete them in order unless a
later package is explicitly isolated and does not depend on earlier schema or
runtime changes.

Status legend: `planned`, `in_progress`, `done`, `blocked`.

| ID | Status | Package |
| --- | --- | --- |
| WP0 | done | Baseline branch and NDV Python toggle cleanup |
| WP1 | done | Core manifest schema v2 |
| WP2 | done | Typed connection validation |
| WP3 | done | Backend credential type registry and OAuth |
| WP4 | done | Provider transport layer |
| WP5 | done | Integration SDK and operation registry |
| WP6 | done | Google Sheets v2 provider |
| WP7 | done | Microsoft Outlook v2 provider |
| WP8 | in_progress | Legacy wrapper replacement |
| WP9 | done | AI runtime contracts |
| WP10 | done | AI v2 supplier nodes |
| WP11 | done | Agent engine request/resume loop |
| WP12 | done | RAG and vector store stack |
| WP13 | done | Provider trigger framework |
| WP14 | done | Observability, security, and production policy |
| WP15 | done | Release cleanup and documentation |

### WP0 - Baseline Branch And NDV Python Toggle Cleanup

Goal: make `feat/ndv-param-grouping` the UI/config baseline and remove the old
side-inspector source button.

Primary files:

- `packages/core/noodle/models.py`
- `packages/core/noodle/sdk.py`
- `packages/core/tests/test_sdk.py`
- `apps/web/src/types.ts`
- `apps/web/src/editor/NDVPanels.tsx`
- `apps/web/src/editor/NodeDetails.tsx`
- `apps/web/src/editor/webhookFields.test.ts`

Steps:

- Ensure `ParamSpec.group` exists in backend and frontend types.
- Keep or merge the `@node(param_groups=...)` SDK shortcut.
- Confirm AST discovery of decorated user modules also preserves `group`.
- Remove the old side-inspector `</> Show code` button from `NodeDetails`.
- Keep `NodeCodePanel` as shared implementation for source view, edit, fork,
  and custom-node creation.
- Make the NDV `Inspector | Python` toggle the standard code view.
- Update tests that referenced the old side button.

Tests:

- `pytest packages/core/tests/test_sdk.py`
- frontend typecheck/build
- existing NDV/webhook field tests
- manual check: built-in node Python panel loads source
- manual check: custom node Python panel can save/fork source

Done when:

- There is one first-class code UI path: NDV `Inspector | Python`.
- Existing source endpoint behavior remains unchanged.
- Optional parameter groups work for webhook and at least two non-webhook nodes.

Status on `main`:

- Done after merge commit `b485645` plus the side-inspector cleanup commit.
- `ParamSpec.group` exists in backend/frontend manifests.
- `@node(param_groups=...)` exists in the Python SDK.
- The old side-inspector `Show code` button was removed.
- The side inspector and NDV use the `Inspector | Python` segmented control.
- Verified with `npm run build` in `apps/web`.

### WP1 - Core Manifest Schema V2

Goal: expand manifests so integrations and AI nodes can describe production UI
and runtime behavior without ad hoc frontend rules.

Primary files:

- `packages/core/noodle/models.py`
- `packages/core/noodle/sdk.py`
- `packages/core/tests/test_sdk.py`
- `apps/web/src/types.ts`
- `apps/api/app/routers/nodes.py`

Add or extend:

- `NodeRole`: `executable`, `supplier`, `trigger`, `tool`, `output_parser`.
- Rich `PortDataKind`: `main`, `control`, `dataset`, `artifact`, `file`,
  `ai_language_model`, `ai_embedding_model`, `ai_memory`, `ai_tool`,
  `ai_output_parser`, `ai_retriever`, `ai_vector_store`,
  `ai_document_loader`, `ai_guardrail`.
- `ParamSpec` metadata: `display_name`, `display_when`, `hide_when`, `widget`,
  `depends_on`, `load_options`, `resource_mapper`, `fixed_collection`,
  `credential_type`, `required_scopes`, `advanced`, `documentation_url`,
  `validation`.
- Backward-compatible defaults for all new fields.

Steps:

- Add Pydantic model fields with defaults so old workflows still load.
- Thread new metadata through `_build_manifest`.
- Thread new metadata through `_decorated_node_from_ast`.
- Extend the `@node(...)` decorator signature conservatively.
- Add tests for decorated built-ins and statically discovered user modules.
- Keep manifest JSON stable for existing fields.

Done when:

- Old manifests still serialize.
- New manifest metadata appears in `/nodes`.
- User-uploaded decorated Python can use the same metadata.

Status in current implementation:

- Done in the working tree after the WP1 schema update.
- `NodeRole` now exists with `executable`, `supplier`, `trigger`, `tool`, and
  `output_parser`.
- `NodeManifest.role` defaults to `executable`.
- `PortDataKind` now includes `main`, `control`, `dataset`, `artifact`, `file`,
  and the AI-specific kinds required by the plan.
- `ParamSpec` now carries rich integration/AI UI metadata:
  `display_name`, `display_when`, `hide_when`, `widget`, `depends_on`,
  `load_options`, `resource_mapper`, `fixed_collection`, `credential_type`,
  `required_scopes`, `advanced`, `documentation_url`, and `validation`.
- The Python SDK threads this metadata through built-in decorators and static
  AST discovery of uploaded decorated modules.
- `@node(role=...)` is supported.
- `@node(param_groups=...)` is applied in both runtime decorators and static AST
  discovery.
- Frontend manifest types were updated for the richer metadata.
- Verification run:
  `uv run pytest packages/core/tests`,
  `npm run build` from `apps/web`.

### WP2 - Typed Connection Validation

Goal: prevent invalid main/data/AI wiring in the editor and on the backend.

Primary files:

- `packages/core/noodle/models.py`
- `packages/core/noodle/engine.py`
- `apps/web/src/editor/connectionValidation.ts`
- `apps/web/src/editor/store.ts`
- `apps/web/src/editor/NodeCard.tsx`
- `apps/web/src/editor/PortDataViewer.tsx`

Steps:

- Define compatibility rules for each `PortDataKind`.
- Allow `any` only where deliberately specified.
- Reject invalid AI connections such as `main -> ai_language_model`.
- Reject invalid data connections server-side before execution.
- Keep quick-fix behavior for dataset mismatches where it exists today.
- Add visual handle styling for AI supplier ports.

Tests:

- frontend connection validation tests
- backend graph validation tests
- workflow execution test for an invalid graph

Done when:

- Invalid wiring is blocked before a run.
- Backend validation matches frontend validation.
- AI supplier nodes can connect only to compatible AI inputs.

Status in current implementation:

- Done in the working tree after the WP2 validation update.
- Core engine now validates connection kinds before execution and raises
  `GraphError` for structural port mismatches.
- Targeted partial runs validate only the nodes/edges that participate in that
  run, so an invalid unrelated edge does not block running a separate target.
- AI port kinds are strict: `any`/`main` data cannot connect into AI supplier
  inputs, and mismatched AI kinds cannot connect to each other.
- DatasetRef behavior remains strict as before, with one corrected case:
  `_connection_kind_error` now allows `dataset <-> any` connections so that
  `_auto_expand_dataset_inputs` can expand a DatasetRef into loop/code nodes
  whose inputs are typed `any`. This fixed a regression that broke
  `test_engine_expands_dataset_ref_into_loop_items`; all 18 dataset tests pass.
- The editor mirrors backend compatibility rules in
  `connectionValidation.ts`.
- AI port kinds have labels and colors in `NodeCard.tsx`.
- Follow-up UI polish still needed: highlight existing invalid saved edges/nodes
  on the canvas, show persistent node/edge warning badges, add a workflow-level
  "invalid connections" panel, and offer quick fixes for AI mismatches where a
  safe converter or replacement exists.
- Verification run:
  `uv run pytest packages/core/tests`,
  `npm run test -- connectionValidation.test.ts` from `apps/web`,
  `npm run build` from `apps/web`.

### WP3 - Backend Credential Type Registry And OAuth

Goal: move credential definitions out of hard-coded frontend presets and support
OAuth providers with refresh.

Primary files:

- `apps/api/app/models.py`
- `apps/api/app/schemas.py`
- `apps/api/app/routers/credentials.py`
- `apps/api/app/services/credentials.py`
- `apps/api/app/services/credential_tests.py`
- `apps/api/app/services/crypto.py`
- `apps/web/src/CredentialsPage.tsx`
- `apps/web/src/editor/NodeDetails.tsx`

New files to add:

- `apps/api/app/services/credential_types.py`
- `apps/api/app/services/oauth.py`
- Alembic migration for OAuth token metadata if needed.

Credential type spec should include:

- id, display name, provider
- auth method: API key, basic, OAuth2, service account, connection string
- fields and secret fields
- OAuth auth URL, token URL, scopes, redirect behavior
- test handler id
- documentation URL
- default environment/workflow scope behavior

Endpoints:

- `GET /credentials/types`
- `POST /credentials/oauth/start`
- `GET /credentials/oauth/callback`
- `POST /credentials/{id}/refresh`

Steps:

- Add registry with Google, Microsoft, Slack, GitHub, OpenAI, Anthropic,
  OpenRouter, Pinecone, Qdrant.
- Store encrypted access tokens and refresh tokens.
- Store expiry and scopes.
- Add refresh-before-run helper.
- Remove hard-coded credential presets from React after the endpoint is ready.
- Add clear missing-scope and expired-credential messages.

Tests:

- credential type endpoint test
- credential create/update/read redaction test
- OAuth start state generation test
- OAuth callback token exchange with mocked provider
- refresh path with mocked provider
- node credential picker renders backend-provided types

Done when:

- Frontend credential creation is driven by backend specs.
- OAuth credentials can be created, tested, refreshed, and redacted.
- Existing credential references still resolve during runs.

Implementation notes (done):

- Risk-to-watch resolved: `GET /credentials/oauth/callback` no longer returns
  raw JSON. It now returns an `HTMLResponse` popup-close page built by
  `_oauth_popup_html(*, success, message, credential_id)` in
  `apps/api/app/routers/credentials.py`, which posts a `postMessage` to
  `window.opener` and closes the popup.
- `apps/web/src/CredentialsPage.tsx` opens the OAuth popup without
  `noopener`/`noreferrer` and listens for the `postMessage` result to finish
  the flow inline.
- Verified by 13 credential tests.

Status in current implementation:

- In progress after the credential registry + OAuth backend slice.
- Added backend-owned credential type registry in
  `apps/api/app/services/credential_types.py`.
- Added API schemas for credential type fields and OAuth metadata.
- Added `GET /credentials/types`.
- Added frontend `CredentialTypeInfo` types and `api.listCredentialTypes()`.
- Credentials page now loads backend credential type specs and merges them over
  the legacy local preset list, so backend-owned types drive new credential
  creation where available without breaking existing legacy credential presets.
- Registry currently includes OpenAI, Anthropic, OpenRouter, Pinecone, Qdrant,
  Slack Bot Token, Slack OAuth2, GitHub Token, GitHub OAuth2, Google Sheets
  OAuth2, Google service account, and Microsoft Outlook OAuth2.
- Added `apps/api/app/services/oauth.py` for signed OAuth state, provider client
  config lookup, authorization URL creation, token exchange, refresh exchange,
  expiry parsing, and token payload normalization.
- Added `POST /credentials/oauth/start`, `GET /credentials/oauth/callback`, and
  `POST /credentials/{id}/refresh`.
- OAuth callback creates normal encrypted `Credential` rows. Tokens are stored
  in the existing encrypted credential payload, so no database migration was
  needed for this slice.
- Added config and Docker environment plumbing for provider OAuth app settings:
  `OAUTH_REDIRECT_BASE_URL`, Google, Microsoft, Slack, and GitHub client
  IDs/secrets.
- Added refresh-before-run behavior at credential-reference resolution time.
  Expired OAuth credentials refresh before Python node execution when a refresh
  token and provider app config exist. Missing refresh/config fails with a clear
  credential error instead of passing a stale token to provider code.
- Added frontend API methods/types for starting OAuth and refreshing a
  credential.
- Added credentials-page OAuth actions. Backend OAuth credential types now show
  a Connect OAuth action in the creation modal, while manual token entry remains
  available. Existing OAuth credentials show a Refresh action that calls
  `POST /credentials/{id}/refresh`.
- Credential list responses now include non-secret OAuth metadata:
  `auth_method`, `oauth_scopes`, and `oauth_expires_at`.
- Node credential pickers now compare `ParamSpec.required_scopes` with selected
  credential `oauth_scopes` and show a missing-scope warning when an OAuth
  credential does not satisfy the node's declared scope requirements.
- Verification run:
  `uv run pytest apps/api/tests/test_credentials_v2.py`,
  `npm run build` from `apps/web`.
- Additional verification run after OAuth backend slice:
  `uv run pytest apps/api/tests/test_credentials_v2.py`,
  `uv run pytest packages/core/tests apps/api/tests/test_credentials_v2.py`,
  `uv run ruff check apps/api/app/services/oauth.py apps/api/app/routers/credentials.py apps/api/app/services/credentials.py apps/api/tests/test_credentials_v2.py`,
  `npm run test -- connectionValidation.test.ts` from `apps/web`,
  `npm run build` from `apps/web`;
  after the metadata/scope-warning slice:
  `uv run pytest apps/api/tests/test_credentials_v2.py`,
  `npm run build` from `apps/web`.
- Files changed in the OAuth backend slice:
  `apps/api/app/config.py`,
  `apps/api/app/routers/credentials.py`,
  `apps/api/app/schemas.py`,
  `apps/api/app/services/credentials.py`,
  `apps/api/app/services/credential_types.py`,
  `apps/api/app/services/oauth.py`,
  `apps/api/pyproject.toml`,
  `apps/api/tests/test_credentials_v2.py`,
  `apps/web/src/api.ts`,
  `apps/web/src/CredentialsPage.tsx`,
  `apps/web/src/editor/NodeDetails.tsx`,
  `apps/web/src/index.css`,
  `apps/web/src/types.ts`,
  `deploy/docker-compose.yml`.
- Known risks: OAuth callback currently returns JSON in the provider popup/tab
  rather than a dedicated frontend success page; provider-specific token quirks
  beyond Google/Microsoft/Slack/GitHub may need special handling once more OAuth
  types are added; refresh-before-run currently covers stored credential refs,
  while future provider transports should also call a shared refresh hook
  directly.
- Remaining WP3 work: add a polished OAuth callback success page/popup-close
  flow, add required-scope metadata to provider operation specs once v2
  providers exist, add OAuth-aware provider test handlers for Microsoft Outlook
  and other OAuth providers, and finish moving hard-coded frontend credential
  presets out after backend coverage is complete.

### WP4 - Provider Transport Layer

Goal: centralize provider HTTP behavior so operation nodes do not repeat auth,
pagination, retries, and error parsing.

New files:

- `packages/nodes/noodle_nodes/integrations_v2/transport.py`
- `packages/nodes/noodle_nodes/integrations_v2/errors.py`
- `packages/nodes/noodle_nodes/integrations_v2/oauth.py`
- `packages/nodes/noodle_nodes/integrations_v2/pagination.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/google/transport.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/microsoft/transport.py`

Transport contract:

- accept resolved credentials
- add auth headers
- refresh tokens through backend/runtime hook where available
- apply retry policy
- parse rate-limit headers
- support cursor/page-token pagination
- support binary upload/download
- redact secrets from debug metadata
- raise structured `ProviderError`

ProviderError fields:

- provider
- operation
- status_code
- code
- message
- retryable
- request_id
- response_body_summary

Tests:

- retry on 429/5xx
- no retry on 400/401 unless refresh is possible
- pagination helper
- error redaction
- request metadata does not include secrets

Done when:

- Google and Microsoft transports can execute mocked requests.
- Operation code no longer needs provider-specific retry/error boilerplate.

Status in current implementation:

- In progress after the first v2 transport slice.
- Added `packages/nodes/noodle_nodes/integrations_v2/`.
- Added structured `ProviderError` with provider, operation, status code, code,
  message, retryable flag, request id, and response summary.
- Added sync `ProviderTransport` and `RetryPolicy` using `requests`, matching
  current Python node execution style while centralizing retries and error
  normalization.
- Added `GoogleTransport` and `MicrosoftGraphTransport` shells with base URLs
  and bearer/API-key auth helpers.
- Added mocked transport tests in
  `packages/nodes/tests/test_integrations_v2_transport.py`.
- Verification run:
  `uv run pytest packages/nodes/tests/test_integrations_v2_transport.py`,
  `uv run ruff check packages/nodes/noodle_nodes/integrations_v2 packages/nodes/tests/test_integrations_v2_transport.py`.
- Remaining WP4 work: pagination helpers, richer provider-specific error
  parsing, rate-limit header handling, request/response debug metadata, OAuth
  refresh hook integration with provider transports, and replacing direct HTTP
  calls inside v2 operation nodes.

**Completed additions (this session):**

- Added `ProviderTransport.paginate()` method supporting Google-style
  `nextPageToken` and MS Graph-style `@odata.nextLink` pagination, with a
  `DEFAULT_MAX_PAGES = 50` safety cap.
- `_sleep_before_retry()` now parses `Retry-After`, `x-ratelimit-reset-after`,
  and `x-ratelimit-reset` response headers on HTTP 429 before falling back to
  exponential backoff.
- `request()` passes the live response object into `_sleep_before_retry` on
  retryable failures so header-driven wait is honoured.
- WP4 is **done**.

### WP5 - Integration SDK And Operation Registry

Goal: define integrations declaratively while registering real Python node
functions.

New files:

- `packages/nodes/noodle_nodes/integrations_v2/specs.py`
- `packages/nodes/noodle_nodes/integrations_v2/registry.py`
- `packages/nodes/noodle_nodes/integrations_v2/node_factory.py`
- `packages/nodes/noodle_nodes/integrations_v2/dynamic_options.py`
- `packages/nodes/noodle_nodes/integrations_v2/__init__.py`

Core classes:

- `IntegrationSpec`
- `CredentialTypeSpec`
- `ResourceSpec`
- `OperationSpec`
- `OperationParamSpec`
- `DynamicOptionLoader`
- `OperationExecutor`
- `GeneratedNodeSource`

Steps:

- Build an internal registry for integration specs.
- Generate `NodeManifest` objects from specs.
- Register executable Python callables in the existing `NodeRegistry`.
- Preserve meaningful source for `NodeCodePanel`.
- Add `@integration_node` only if it simplifies registration without hiding
  Python source.
- Add dynamic options endpoint for operation-backed dropdowns.

Tests:

- spec to manifest test
- spec to registered callable test
- source endpoint test for generated operation node
- dynamic options loader test

Done when:

- A simple mock provider can define one operation from specs and run as a real
  Noodle node.
- The Python panel shows useful operation source.

Status in current implementation:

- In progress after the first operation-spec registry slice.
- Added `OperationParamSpec`, `OperationSpec`, `ResourceSpec`, and
  `IntegrationSpec` in `packages/nodes/noodle_nodes/integrations_v2/specs.py`.
- Added an operation node factory that builds real Python functions with
  explicit parameters and stores generated source on `__noodle_source__`.
- Added an operation registry that stores executors, registers generated
  `NodeDef` objects into a `NodeRegistry`, and executes operations by node id.
- Updated `GET /nodes/{node_type}/source` to use `__noodle_source__` when a
  generated node provides it, falling back to `inspect.getsource` for ordinary
  built-ins.
- Added mocked registry/factory tests in
  `packages/nodes/tests/test_integrations_v2_registry.py`.
- Added API coverage in `apps/api/tests/test_nodes.py` for generated v2
  manifests and source responses.
- Verification run:
  `uv run pytest packages/nodes/tests/test_integrations_v2_transport.py packages/nodes/tests/test_integrations_v2_registry.py`,
  `uv run ruff check packages/nodes/noodle_nodes/integrations_v2 packages/nodes/tests/test_integrations_v2_transport.py packages/nodes/tests/test_integrations_v2_registry.py apps/api/app/routers/nodes.py`,
  `uv run pytest apps/api/tests/test_nodes.py`,
  `uv run ruff check apps/api/tests/test_nodes.py apps/api/app/routers/nodes.py`.
- Remaining WP5 work: integration-level registration helpers, dynamic option
  loader registry/API endpoint, richer source templates for real provider
  operations, and expanded real-provider operation coverage.

**Completed additions (this session):**

- Added `packages/nodes/noodle_nodes/integrations_v2/dynamic_options.py`:
  `DynamicOption`, `register_loader()`, `call_loader()`, `list_loader_ids()`.
- Added `GET /nodes/dynamic-options/{loader_id}` endpoint in
  `apps/api/app/routers/nodes.py` — accepts `credentials`, `spreadsheet_id`,
  `sheet_name` query params and returns `{loader_id, options}` JSON.
- Exposed `call_loader` and `register_loader` from `integrations_v2/__init__.py`.
- WP5 is **done**.

### WP6 - Google Sheets V2 Provider

Goal: build the first production integration provider.

Provider files:

- `packages/nodes/noodle_nodes/integrations_v2/providers/google_sheets/spec.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/google_sheets/operations.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/google_sheets/options.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/google_sheets/tests/`

Operations:

- Spreadsheet: create, get metadata, delete if supported by Drive credential
- Sheet: list sheets
- Values: read, append, update, clear
- Rows: lookup by column, append mapped row

Credential types:

- `google_sheets_oauth2`
- `google_service_account`

Dynamic options:

- spreadsheets
- sheets/tabs
- header row columns

Tests:

- append values payload
- read values payload
- update values payload
- OAuth credential scope check
- pagination or next-page behavior where relevant
- dynamic option loaders with mocked Google responses

Done when:

- `google_sheets_append_v2` can replace the old Google Sheets append wrapper.
- One end-to-end workflow can read and append rows using mocked provider calls.

Status in current implementation:

- In progress after the first Google Sheets v2 operation slice.
- Added provider module
  `packages/nodes/noodle_nodes/integrations_v2/providers/google_sheets/`.
- Added generated Python-native nodes:
  `google_sheets_read_v2` and `google_sheets_append_v2`.
- Both nodes use `OperationSpec`, `register_operation`, and `GoogleTransport`
  instead of ad hoc HTTP wrapper code.
- Both nodes declare `google_sheets_oauth2` multi-field credentials and required
  OAuth scope `https://www.googleapis.com/auth/spreadsheets`, enabling the
  editor missing-scope warning added in WP3.
- Imported the Google Sheets v2 provider from `noodle_nodes.__init__` so the
  generated manifests register with the default node registry.
- Added mocked provider tests in
  `packages/nodes/tests/test_google_sheets_v2.py`.
- Verification run:
  `uv run pytest packages/nodes/tests/test_integrations_v2_transport.py packages/nodes/tests/test_integrations_v2_registry.py packages/nodes/tests/test_google_sheets_v2.py`,
  `uv run ruff check packages/nodes/noodle_nodes/integrations_v2 packages/nodes/noodle_nodes/__init__.py packages/nodes/tests/test_integrations_v2_transport.py packages/nodes/tests/test_integrations_v2_registry.py packages/nodes/tests/test_google_sheets_v2.py apps/api/app/routers/nodes.py`.
- Broader verification after WP6 slice:
  `uv run pytest packages/core/tests apps/api/tests/test_credentials_v2.py packages/nodes/tests/test_integrations_v2_transport.py packages/nodes/tests/test_integrations_v2_registry.py packages/nodes/tests/test_google_sheets_v2.py`,
  `npm run test -- connectionValidation.test.ts` from `apps/web`,
  `npm run build` from `apps/web`.
- Final verification in this implementation session:
  `uv run pytest packages/core/tests apps/api/tests/test_credentials_v2.py apps/api/tests/test_nodes.py packages/nodes/tests/test_integrations_v2_transport.py packages/nodes/tests/test_integrations_v2_registry.py packages/nodes/tests/test_google_sheets_v2.py`,
  `uv run ruff check apps/api/app/services/oauth.py apps/api/app/routers/credentials.py apps/api/app/services/credentials.py apps/api/app/routers/nodes.py apps/api/tests/test_credentials_v2.py apps/api/tests/test_nodes.py packages/nodes/noodle_nodes/integrations_v2 packages/nodes/noodle_nodes/__init__.py packages/nodes/tests/test_integrations_v2_transport.py packages/nodes/tests/test_integrations_v2_registry.py packages/nodes/tests/test_google_sheets_v2.py`,
  `npm run test -- connectionValidation.test.ts` from `apps/web`.
- Remaining WP6 work: spreadsheet metadata operations, clear/update operations,
  row lookup/mapping operations, dynamic spreadsheet/sheet/header option
  loaders, OAuth credential scope test integration, and an end-to-end workflow
  execution test through the generated v2 nodes.

**Completed additions (this session):**

- Added `update_values` (PUT), `clear_values` (POST :clear), and
  `get_spreadsheet_metadata` (GET w/ field mask) executors and specs.
- Added `packages/nodes/noodle_nodes/integrations_v2/providers/google_sheets/options.py`
  registering two dynamic option loaders:
  `google_sheets.list_sheet_names` and `google_sheets.list_header_columns`.
- `google_sheets/__init__.py` imports `options` to trigger loader registration.
- Tests in `packages/nodes/tests/test_integrations_v2_providers.py` cover all 5
  operations and both dynamic option loaders (27 tests, all passing).
- WP6 is **done** (end-to-end with real credentials remains a manual step).

### WP7 - Microsoft Outlook V2 Provider

Goal: build the second production provider and prove Microsoft Graph transport.

Provider files:

- `packages/nodes/noodle_nodes/integrations_v2/providers/microsoft_outlook/spec.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/microsoft_outlook/operations.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/microsoft_outlook/options.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/microsoft_outlook/tests/`

Operations:

- Email: send, list, get, search, create draft, reply, forward, move, delete
- Attachment: list, download, upload to draft if feasible
- Calendar: create event, update event, delete event, list events

Credential type:

- `microsoft_outlook_oauth2`

Scopes:

- mail read/write/send scopes as least-privilege choices
- calendar read/write scopes for event operations

Tests:

- Graph `sendMail` payload
- list/search query params
- attachment download artifact behavior
- Graph error normalization
- token refresh path

Done when:

- Outlook send email and list messages work through Microsoft transport.
- Scope warnings appear for operations requiring scopes the credential lacks.

**Completed (this session):**

- Created `packages/nodes/noodle_nodes/integrations_v2/providers/microsoft_outlook/`
  with `operations.py` (4 operations) and `__init__.py`.
- `send_mail`: POST `/me/sendMail` — to/cc/bcc/reply-to, HTML/Text body, saveToSentItems.
- `list_messages`: GET `/me/mailFolders/{folder}/messages` — `$top`, `$search`,
  `$filter`, `$select`, `$orderby` (search and orderby are mutually exclusive as
  per Graph API constraint).
- `get_message`: GET `/me/messages/{id}` with `$select`.
- `list_calendar_events`: GET `/me/events` — date-range `$filter`, `$select`, `$orderby`.
- All operations use `MicrosoftGraphTransport` with Bearer auth.
- `noodle_nodes/__init__.py` imports the provider to trigger registration.
- Tests in `packages/nodes/tests/test_integrations_v2_providers.py` cover all 4
  Outlook operations (12 tests, all passing alongside 15 Sheets tests).
- WP7 is **done** (attachment/draft/reply operations remain for a future slice).

### WP8 - Legacy Wrapper Replacement

Goal: retire provider-specific HTTP wrappers only after v2 equivalents exist.

Primary files:

- `packages/nodes/noodle_nodes/integrations.py`
- `packages/nodes/noodle_nodes/saas.py`
- `packages/nodes/noodle_nodes/communication.py`
- `apps/web/src/workflowTemplates.ts`
- `apps/api/app/services/ai_builder.py`
- affected tests under `packages/nodes/tests/` and `apps/api/tests/`

Steps:

- Add `legacy=True` or equivalent manifest metadata if needed.
- Update palette labels to prefer v2 providers.
- Update starter workflows/templates to use v2 IDs.
- Keep `http_request` as the generic custom API escape hatch.
- Remove old wrappers provider by provider.
- Do not remove a wrapper until v2 has equivalent tests.

Done when:

- Old provider wrappers are gone or hidden.
- Templates and AI draft generation target v2 nodes.
- Generic HTTP remains available.

Implementation notes (in progress):

- Hid and deprecated the legacy Google Sheets wrappers
  `google_sheets_read` and `google_sheets_append` now that v2 equivalents exist.
  Existing workflows can still run them by id, but the palette hides them and
  their manifests carry replacement ids `google_sheets_read_v2` and
  `google_sheets_append_v2`.
- Updated palette follow-up recommendations in
  `apps/web/src/editor/NodePalette.tsx` to use `google_sheets_append_v2`
  instead of the hidden legacy append wrapper.
- Confirmed templates and `ai_builder` do not currently target the old Google
  Sheets wrapper ids.
- Added manifest assertions in `packages/nodes/tests/test_builtin_nodes.py` and
  `apps/api/tests/test_nodes.py`.
- Added GitHub v2 operation nodes:
  - `github_get_repo_v2`
  - `github_create_issue_v2`
- GitHub v2 operations use `CredentialSpec(type="github", key="*", multi=True)`
  and `ProviderTransport` with GitHub API version headers.
- Hid and deprecated legacy GitHub operation wrappers `github_get_repo` and
  `github_create_issue`, with replacement ids pointing to their v2 equivalents.
- Updated `apps/api/app/services/ai_builder.py` to allow and generate
  `github_create_issue_v2` with a whole GitHub credential reference instead of
  the old raw `token` field.
- Updated `docs/provider-coverage-matrix.md` to list GitHub v2 operations.
- Added Slack v2 operation node `slack_send_message_v2`.
- Slack v2 uses `CredentialSpec(type="slack_bot", key="*", multi=True)` and the
  shared `ProviderTransport`; Slack `ok: false` HTTP 200 responses are converted
  into structured `ProviderError`s.
- Hid and deprecated legacy `slack_send_message`, with replacement id
  `slack_send_message_v2`.
- Updated the Webhook + Slack workflow template and palette recommendations to
  use `slack_send_message_v2`.
- Updated `apps/api/app/services/ai_builder.py` to allow and generate
  `slack_send_message_v2` with a whole Slack credential reference instead of the
  old raw `bot_token` field.
- Updated `docs/provider-coverage-matrix.md` to list Slack v2 send-message
  coverage.
- Added Stripe v2 operation node `stripe_create_customer_v2`.
- Stripe v2 uses `CredentialSpec(type="stripe", key="*", multi=True)` and the
  shared `ProviderTransport`, preserving metadata form flattening for Stripe's
  API.
- Hid and deprecated legacy `stripe_create_customer`, with replacement id
  `stripe_create_customer_v2`.
- Updated `docs/provider-coverage-matrix.md` to list Stripe v2 create-customer
  coverage.
- Added Airtable v2 operation nodes:
  - `airtable_list_records_v2`
  - `airtable_create_record_v2`
- Airtable v2 uses `CredentialSpec(type="airtable", key="*", multi=True)` and
  the shared `ProviderTransport`, preserving list filters and create-record
  `typecast` behavior.
- Hid and deprecated legacy Airtable wrappers `airtable_list_records` and
  `airtable_create_record`, with replacement ids pointing to their v2
  equivalents.
- Updated `apps/api/app/services/ai_builder.py` to allow Airtable v2 nodes with
  a whole Airtable credential reference instead of old raw token-style params.
- Updated `docs/provider-coverage-matrix.md` to list Airtable v2 list/create
  coverage.
- Added Notion v2 operation node `notion_create_page_v2`.
- Notion v2 uses `CredentialSpec(type="notion", key="*", multi=True)` and the
  shared `ProviderTransport`, preserving database/page-parent page creation and
  making the parent requirement explicit.
- Hid and deprecated legacy `notion_create_page`, with replacement id
  `notion_create_page_v2`.
- Updated `apps/api/app/services/ai_builder.py` to allow Notion v2 with a whole
  Notion credential reference.
- Updated palette recommendations and `docs/provider-coverage-matrix.md` for
  Notion v2 create-page coverage.

Verification:

- `uv run pytest packages/nodes/tests/test_builtin_nodes.py::test_expected_integration_nodes_are_registered packages/nodes/tests/test_builtin_nodes.py::test_google_sheets_append_derives_rows_from_input apps/api/tests/test_nodes.py packages/nodes/tests/test_google_sheets_v2.py`
  (8 passed, 1 existing Pydantic warning)
- `uv run ruff check packages/nodes/noodle_nodes/integrations.py packages/nodes/tests/test_builtin_nodes.py apps/api/tests/test_nodes.py`
- `npm run build` from `apps/web` passed with existing chunk-size warnings.
- `uv run pytest packages/nodes/tests/test_github_v2.py packages/nodes/tests/test_builtin_nodes.py::test_expected_integration_nodes_are_registered apps/api/tests/test_nodes.py apps/api/tests/test_releases_errors_ai.py::test_ai_builder_uses_github_v2_operations_and_credentials`
  (9 passed, 1 existing Pydantic warning)
- `uv run ruff check packages/nodes/noodle_nodes/integrations_v2/providers/github packages/nodes/noodle_nodes/integrations.py packages/nodes/tests/test_github_v2.py packages/nodes/tests/test_builtin_nodes.py apps/api/app/services/ai_builder.py apps/api/tests/test_nodes.py apps/api/tests/test_releases_errors_ai.py`
- `uv run pytest packages/nodes/tests/test_slack_v2.py packages/nodes/tests/test_builtin_nodes.py::test_expected_integration_nodes_are_registered packages/nodes/tests/test_builtin_nodes.py::test_slack_node_builds_chat_post_message_payload apps/api/tests/test_nodes.py apps/api/tests/test_releases_errors_ai.py::test_ai_builder_returns_and_applies_editable_graph apps/api/tests/test_releases_errors_ai.py::test_ai_builder_attaches_existing_credentials`
  (11 passed, 1 existing Pydantic warning)
- `uv run ruff check packages/nodes/noodle_nodes/integrations_v2/providers/slack packages/nodes/noodle_nodes/__init__.py packages/nodes/noodle_nodes/integrations.py packages/nodes/tests/test_slack_v2.py packages/nodes/tests/test_builtin_nodes.py apps/api/app/services/ai_builder.py apps/api/tests/test_nodes.py apps/api/tests/test_releases_errors_ai.py`
- `npm run build` from `apps/web` passed with existing chunk-size warnings after
  the Slack template/palette update.
- `uv run pytest packages/nodes/tests/test_stripe_v2.py packages/nodes/tests/test_builtin_nodes.py::test_expected_integration_nodes_are_registered packages/nodes/tests/test_builtin_nodes.py::test_stripe_create_customer_uses_input_and_metadata apps/api/tests/test_nodes.py`
  (7 passed, 1 existing Pydantic warning)
- `uv run ruff check packages/nodes/noodle_nodes/integrations_v2/providers/stripe packages/nodes/noodle_nodes/__init__.py packages/nodes/noodle_nodes/integrations.py packages/nodes/tests/test_stripe_v2.py packages/nodes/tests/test_builtin_nodes.py apps/api/tests/test_nodes.py`
- `uv run pytest packages/nodes/tests/test_airtable_v2.py packages/nodes/tests/test_builtin_nodes.py::test_expected_integration_nodes_are_registered apps/api/tests/test_nodes.py`
  (8 passed, 1 existing Pydantic warning)
- `uv run ruff check packages/nodes/noodle_nodes/integrations_v2/providers/airtable packages/nodes/noodle_nodes/__init__.py packages/nodes/noodle_nodes/integrations.py packages/nodes/tests/test_airtable_v2.py packages/nodes/tests/test_builtin_nodes.py apps/api/app/services/ai_builder.py apps/api/tests/test_nodes.py`
- `uv run pytest packages/nodes/tests/test_notion_v2.py packages/nodes/tests/test_builtin_nodes.py::test_expected_integration_nodes_are_registered apps/api/tests/test_nodes.py`
  (8 passed, 1 existing Pydantic warning)
- `uv run ruff check packages/nodes/noodle_nodes/integrations_v2/providers/notion packages/nodes/noodle_nodes/__init__.py packages/nodes/noodle_nodes/integrations.py packages/nodes/tests/test_notion_v2.py packages/nodes/tests/test_builtin_nodes.py apps/api/app/services/ai_builder.py apps/api/tests/test_nodes.py`
- `npm run build` from `apps/web` passed with existing chunk-size warnings after
  the Notion palette update.
- Broader WP8 regression:
  `uv run pytest packages/nodes/tests/test_google_sheets_v2.py packages/nodes/tests/test_github_v2.py packages/nodes/tests/test_slack_v2.py packages/nodes/tests/test_stripe_v2.py packages/nodes/tests/test_airtable_v2.py packages/nodes/tests/test_notion_v2.py packages/nodes/tests/test_integrations_v2_providers.py packages/nodes/tests/test_builtin_nodes.py apps/api/tests/test_nodes.py apps/api/tests/test_releases_errors_ai.py`
  (98 passed, 1 existing Pydantic warning)
- Broad WP8 lint sweep passed:
  `uv run ruff check packages/nodes/noodle_nodes/integrations_v2/providers packages/nodes/noodle_nodes/__init__.py packages/nodes/noodle_nodes/integrations.py packages/nodes/tests/test_google_sheets_v2.py packages/nodes/tests/test_github_v2.py packages/nodes/tests/test_slack_v2.py packages/nodes/tests/test_stripe_v2.py packages/nodes/tests/test_airtable_v2.py packages/nodes/tests/test_notion_v2.py packages/nodes/tests/test_integrations_v2_providers.py packages/nodes/tests/test_builtin_nodes.py apps/api/app/services/ai_builder.py apps/api/tests/test_nodes.py apps/api/tests/test_releases_errors_ai.py`
- Final combined regression after WP8/WP15 updates:
  `uv run pytest apps/api/tests/test_credentials_v2.py apps/api/tests/test_triggers.py apps/api/tests/test_workflows.py apps/api/tests/test_nodes.py apps/api/tests/test_releases_errors_ai.py apps/api/tests/test_runs.py::test_run_timeline_includes_persisted_agent_events apps/api/tests/test_runs.py::test_run_timeline_includes_persisted_guardrail_events apps/api/tests/test_enterprise.py::test_audit_log_records_actions packages/core/tests/test_ai_runtime.py packages/nodes/tests/test_ai_v2_nodes.py packages/nodes/tests/test_integrations_v2_transport.py packages/nodes/tests/test_integrations_v2_registry.py packages/nodes/tests/test_github_provider_triggers.py packages/nodes/tests/test_google_sheets_v2.py packages/nodes/tests/test_github_v2.py packages/nodes/tests/test_slack_v2.py packages/nodes/tests/test_stripe_v2.py packages/nodes/tests/test_airtable_v2.py packages/nodes/tests/test_notion_v2.py packages/nodes/tests/test_integrations_v2_providers.py packages/nodes/tests/test_builtin_nodes.py`
  (256 passed, 1 existing Pydantic warning)
- Final combined lint after WP8/WP15 updates:
  `uv run ruff check apps/api/app/services/credential_tests.py apps/api/app/routers/credentials.py apps/api/app/services/provider_triggers.py apps/api/app/services/runner.py apps/api/app/routers/runs.py apps/api/app/routers/workflows.py apps/api/app/routers/provider_webhooks.py apps/api/app/schemas.py apps/api/app/services/ai_builder.py apps/api/tests/test_credentials_v2.py apps/api/tests/test_triggers.py apps/api/tests/test_runs.py apps/api/tests/test_nodes.py apps/api/tests/test_releases_errors_ai.py packages/core/noodle/ai_runtime.py packages/core/tests/test_ai_runtime.py packages/nodes/noodle_nodes/http_security.py packages/nodes/noodle_nodes/builtin.py packages/nodes/noodle_nodes/__init__.py packages/nodes/noodle_nodes/integrations.py packages/nodes/noodle_nodes/ai_v2 packages/nodes/noodle_nodes/integrations_v2 packages/nodes/tests/test_ai_v2_nodes.py packages/nodes/tests/test_builtin_nodes.py packages/nodes/tests/test_integrations_v2_transport.py packages/nodes/tests/test_integrations_v2_registry.py packages/nodes/tests/test_github_provider_triggers.py packages/nodes/tests/test_google_sheets_v2.py packages/nodes/tests/test_github_v2.py packages/nodes/tests/test_slack_v2.py packages/nodes/tests/test_stripe_v2.py packages/nodes/tests/test_airtable_v2.py packages/nodes/tests/test_notion_v2.py packages/nodes/tests/test_integrations_v2_providers.py`

Remaining WP8 work:

- Do not hide remaining legacy wrappers until v2 operation equivalents are
  implemented and tested.
- Remaining visible legacy wrapper modules include providers in
  `communication.py`, `saas.py`, and unreplaced integrations such as Discord,
  SMTP, Postgres, MySQL, and S3. Replace them provider-by-provider only after
  matching v2 specs/tests exist.
- Update templates and `ai_builder` when additional v2 operation equivalents
  land.
- Keep `http_request` and `graphql_request` visible as generic API escape
  hatches.

### WP9 - AI Runtime Contracts

Goal: create provider-neutral AI protocols before replacing nodes.

New files:

- `packages/core/noodle/ai_runtime.py`
- `packages/nodes/noodle_nodes/ai_v2/providers/`
- `packages/nodes/noodle_nodes/ai_v2/types.py`

Contracts:

- `ChatModelAdapter`
- `EmbeddingModelAdapter`
- `ToolAdapter`
- `MemoryAdapter`
- `OutputParserAdapter`
- `RetrieverAdapter`
- `GuardrailAdapter`
- `ModelCapabilities`
- `ModelUsage`
- `AIMessage`
- `ToolCall`
- `ToolResult`

Steps:

- Normalize messages, tool calls, usage, and provider errors.
- Add provider capability checks.
- Add minimal OpenAI and Anthropic adapters first.
- Avoid LangChain lock-in unless deliberately chosen later.

Tests:

- model adapter normalization
- tool schema serialization
- usage extraction
- capability mismatch error

Done when:

- AI node implementations can use common protocols instead of provider-specific
  dictionaries.

Implementation notes (done):

- `packages/core/noodle/ai_runtime.py` defines the provider-neutral contracts:
  `MessageRole`, `AIMessage`, `ToolParameterSchema`, `ToolSchema`, `ToolCall`,
  `ToolResult`, `ModelUsage`, `ModelCapabilities`, `ChatRequest`,
  `ChatResponse`, `EmbeddingRequest`, `EmbeddingResponse`,
  `AgentActionRequest`, `AgentResumeInput`, and the ABCs `ChatModelAdapter`,
  `EmbeddingModelAdapter`, `MemoryAdapter`, `OutputParserAdapter`,
  `ToolAdapter`, `GuardrailAdapter`. Pydantic models throughout; no LangChain.
- Provider adapters (requests-only, no vendor SDKs):
  - `packages/nodes/noodle_nodes/ai_v2/providers/openai.py` -
    `OpenAIChatAdapter` (covers `openai`, `openai_compatible`, `ollama`,
    `openrouter`) and `AzureOpenAIChatAdapter`.
  - `packages/nodes/noodle_nodes/ai_v2/providers/anthropic.py` -
    `AnthropicChatAdapter` (system-message extraction, tool_result as a
    user + content block).
- `packages/nodes/noodle_nodes/ai_v2/factory.py` exposes
  `adapter_from_credentials(credentials, *, provider, model, temperature,
  max_tokens, response_format, timeout_seconds)` to bridge legacy flat
  credential dicts to the WP9 adapters.
- All adapter constructors carry runtime defaults
  (`temperature`/`max_tokens`/`timeout_seconds`) that round-trip through
  `as_config`/`from_config`.
- Verified by 42 tests in `packages/core/tests/test_ai_runtime.py`.

### WP10 - AI V2 Supplier Nodes

Goal: replace prototype AI nodes with typed supplier/executable nodes.

New files:

- `packages/nodes/noodle_nodes/ai_v2/models.py`
- `packages/nodes/noodle_nodes/ai_v2/embeddings.py`
- `packages/nodes/noodle_nodes/ai_v2/memory.py`
- `packages/nodes/noodle_nodes/ai_v2/tools.py`
- `packages/nodes/noodle_nodes/ai_v2/output_parsers.py`
- `packages/nodes/noodle_nodes/ai_v2/guardrails.py`

Initial nodes:

- AI Chat Model - OpenAI
- AI Chat Model - Anthropic
- AI Embeddings - OpenAI
- AI Buffer Memory
- AI Structured Output Parser
- AI HTTP Tool
- AI Workflow Tool
- AI Integration Tool
- AI Guardrails

Steps:

- Register supplier nodes with typed AI output ports.
- Put advanced provider options behind param groups.
- Keep standalone model invocation nodes only where useful.
- Update `ai_builder` to use v2 node IDs.
- Remove or mark old `llm.py` and `ai_extra.py` nodes after v2 coverage exists.

Tests:

- manifests have correct roles and AI ports
- model node supplies adapter
- parser node validates output
- tool node schema is stable

Done when:

- A workflow can wire model supplier -> agent with typed validation.
- Old AI node usage is no longer needed for new templates.

Implementation notes (done):

- New supplier/tool nodes registered with typed AI output ports:
  - `packages/nodes/noodle_nodes/ai_v2/models.py` -
    `ai_chat_model_openai`, `ai_chat_model_anthropic`, `ai_chat_model_azure`
    (`role="supplier"`, output port kind `ai_language_model`). Advanced
    options (`temperature`, `max_tokens`, `response_format`, `api_version`,
    `timeout_seconds`) are behind an `Options` param group.
  - `packages/nodes/noodle_nodes/ai_v2/embeddings.py` - `ai_embedding_model`
    (`ai_embedding_model` port) backed by
    `providers/embeddings.py` (`OpenAIEmbeddingAdapter`,
    `CohereEmbeddingAdapter`, `embedding_adapter_from_credentials`).
  - `packages/nodes/noodle_nodes/ai_v2/memory.py` - `ai_buffer_memory`
    (`ai_memory` port) + concrete `BufferMemoryAdapter` (bounded in-process
    window, optional seeded system prompt).
  - `packages/nodes/noodle_nodes/ai_v2/tools.py` - `ai_http_tool` and
    `ai_workflow_tool` (`role="tool"`, `ai_tool` port) + `HttpToolAdapter`
    and `WorkflowToolAdapter`.
  - `packages/nodes/noodle_nodes/ai_v2/output_parsers.py` -
    `ai_structured_output_parser` (`role="output_parser"`,
    `ai_output_parser` port) + `StructuredOutputParser` (JSON extraction with
    fenced-block tolerance, required-key validation, format instructions).
  - `packages/nodes/noodle_nodes/ai_v2/guardrails.py` - `ai_guardrail`
    (`ai_guardrail` port) + `KeywordGuardrail` (blocked terms, regex
    redaction, max-length enforcement).
- Registration: `packages/nodes/noodle_nodes/ai_v2/__init__.py` imports all
  node modules; `packages/nodes/noodle_nodes/__init__.py` imports `ai_v2`.
- Deferred from this package: `AI Integration Tool` node, `ai_builder`
  migration to v2 IDs, and removal/deprecation of legacy `llm.py` /
  `ai_extra.py` nodes (kept in place pending the WP11 agent engine work).
- Verified by 33 tests in `packages/nodes/tests/test_ai_v2_nodes.py`; rerun the
  broader AI suite before closing WP11.

### WP11 - Agent Engine Request/Resume Loop

Goal: make agent tool execution observable, resumable, and approval-aware.

Primary files:

- `packages/core/noodle/ai_runtime.py`
- `packages/core/noodle/engine.py`
- `packages/core/noodle/models.py`
- `packages/core/noodle/sdk.py`
- `packages/core/noodle/serialization.py`
- `packages/nodes/noodle_nodes/llm.py`
- `packages/nodes/noodle_nodes/ai_v2/agents.py`
- `packages/nodes/noodle_nodes/ai_v2/tools.py`
- `packages/nodes/noodle_nodes/ai_v2/providers/openai.py`
- `packages/nodes/noodle_nodes/ai_v2/providers/anthropic.py`
- `apps/api/app/models.py`
- `apps/api/app/schemas.py`
- `apps/api/app/services/queue.py`
- `apps/api/app/services/runner.py`
- `apps/api/app/services/runtime_pool.py`
- `apps/api/app/services/remote_dispatch.py`
- `apps/api/app/routers/runs.py`
- `apps/api/app/routers/ops.py`
- `apps/api/app/services/retention.py`
- `apps/api/alembic/versions/0032_run_events.py`
- `apps/api/alembic/versions/0033_run_approvals.py`
- `packages/runtime/noodle_runtime/server.py`
- `packages/runner/noodle_runner_agent/process_pool.py`
- `packages/runner/noodle_runner_agent/agent.py`
- `packages/runner/noodle_runner_agent/k8s_entrypoint.py`
- `apps/web/src/types.ts`
- `apps/web/src/ExecutionsPage.tsx`
- `apps/web/src/EditorPage.tsx`
- `apps/web/src/editor.css`
- `apps/web/src/index.css`
- `apps/web/src/editor/store.ts`
- `apps/web/src/editor/NodeCard.tsx`
- `apps/web/src/editor/NodePalette.tsx`
- `apps/web/src/editor/CommandPalette.tsx`
- `apps/web/src/editor/NDVPanels.tsx`
- run/event UI components

New concepts:

- `AgentActionRequest`
- `AgentActionResponse`
- `AgentStepEvent`
- pending approval state
- durable approval records
- side-effect auto-approve mode
- waiting run status for approval pauses
- hidden/deprecated manifest metadata for legacy node retirement
- resume-preparation audit events for approval resumes
- tool side-effect metadata
- max iteration enforcement
- cancellation checks between steps

Steps:

- Agent asks engine to execute tool calls.
- Engine records tool calls as run events.
- Engine executes tool node or subworkflow.
- Engine resumes agent with observations.
- Add side-effect approval mode.
- Persist state between iterations.

Tests:

- single tool call loop
- multiple tool calls
- failed tool call
- max iterations
- cancellation
- approval required
- auto-approve side-effect tool
- approval decision API
- pause on approval and resume approved tool call
- resume after process restart if persistence is implemented

Done when:

- Agent steps are visible in run history.
- Tool execution is not hidden inside one opaque node invocation.

Implementation notes (in progress - current slice):

- Added `AgentActionResponse` and `AgentStepEvent` to
  `packages/core/noodle/ai_runtime.py`.
- Extended `AIMessage` with assistant `tool_calls` so resumed model calls can
  preserve provider-native tool-call history.
- Updated OpenAI-compatible and Anthropic adapters to serialize assistant
  tool-call messages before tool-result messages on resume.
- Added engine handling for nodes that return `AgentActionRequest`:
  - emits `agent_action_requested`, `agent_tool_started`,
    `agent_tool_finished`, and `agent_action_completed` events;
  - dispatches matching connected `ToolAdapter.invoke(...)` calls;
  - turns unknown/failed tools into `ToolResult(is_error=True)` observations;
  - enforces `max_steps` before dispatch;
  - blocks side-effecting tools unless `AgentActionRequest.allow_side_effects`
    is true;
  - emits `agent_tool_auto_approved` before approved side-effecting tools run;
  - resumes nodes that accept hidden runtime `agent_resume` via `**kwargs`;
  - returns `AgentActionResponse` for non-resumable nodes.
- Added `ToolAdapter.invoke_async(...)` as the engine-facing invocation hook.
  Normal sync tools use the default thread wrapper; workflow tools can override
  it to call async host services.
- Added `ToolAdapter.side_effecting` and `AgentActionRequest.allow_side_effects`
  as the first approval-safety hook. HTTP tools are treated as side-effecting
  for non-`GET`/`HEAD`/`OPTIONS` methods, and workflow tools are treated as
  side-effecting by default.
- Added `AgentActionRequest.approved_tool_call_ids` so approving one pending
  side-effecting tool does not approve every later tool call in the same agent
  step.
- Added `AgentApprovalRequired`, `RunStatus.waiting`, and `NodeStatus.waiting`.
  API in-process runs now call the engine with `pause_on_approval=True`; direct
  engine callers still get the older error-observation behavior unless they
  opt into pause mode.
- Side-effecting tools now emit `agent_tool_approval_required` and return an
  error observation when approval has not been granted. The event is forwarded
  by remote dispatch, persisted in `run_events`, merged into run timelines, and
  rendered in the execution timeline.
- The AI Agent v2 node now exposes a core `Tool approval` parameter backed by
  `side_effect_approval`, with `require_approval` and `auto_approve` choices.
  `auto_approve` sets `AgentActionRequest.allow_side_effects=True`; a legacy
  direct-call `allow_side_effects` kwarg is still accepted through
  `**runtime`. This remains auditable: auto-approved side-effecting tool calls
  emit `agent_tool_auto_approved` and are persisted as approved
  `run_approvals` rows with `resolved_by="auto"`.
- Added durable `run_approvals` records:
  - new `RunApproval` ORM model and `0033_run_approvals` Alembic migration;
  - runner upserts pending approvals from `agent_tool_approval_required`;
  - runner records auto-approved side-effecting tools as approved approvals
    with `resolved_by="auto"`;
  - pending approvals store private `resume_state` so the run can resume the
    exact agent request after approval;
  - retention deletes approval rows alongside old runs.
- Added approval APIs:
  - `GET /runs/{run_id}/approvals`;
  - `POST /runs/{run_id}/approvals/{approval_id}/decision`;
  - decision writes a durable `agent_tool_approval_decided` event and publishes
    it on the run event stream.
- Added a paused approval lifecycle:
  - the same pause/resume options are now passed through the local
    `noodle_runtime` subprocess protocol used by `runtime_pool.dispatch`;
  - approval-required runs finish the active worker task with `Run.status`
    set to `waiting` and no `finished_at`;
  - `RunQueueEntry.status="waiting"` parks the run outside the lease loop;
  - approval decisions call `resume_waiting_run_from_approval(...)`, which
    requeues the same run with a replay seed containing prior restorable
    successful outputs, target descendants of the agent node, and the serialized
    `AgentActionRequest`;
  - queued resume injects `agent_action_resume` into the engine so the approved
    tool call runs without asking the model to re-plan the same step.
- Approval resume now emits and persists `agent_resume_prepared` before the run
  is requeued. The event records the node outputs reused from cache, the nodes
  skipped because their prior outputs were non-restorable object previews, and
  the target set used for resume.
- Resume cache policy is explicit: plain JSON/restorable serialized outputs are
  reused, while adapter/supplier outputs such as chat models, memory adapters,
  and tool adapters are intentionally skipped and recomputed because the engine
  walks upstream ancestors for the resumed agent target.
- Tool-call persistence decision: do not create synthetic child `NodeRun` rows
  for agent tool calls in WP11. `NodeRun` remains one row per graph node, while
  `run_events` is the canonical timeline for `agent_tool_started`,
  `agent_tool_finished`, `agent_tool_approval_required`,
  `agent_tool_auto_approved`, `agent_tool_approval_decided`, and
  `agent_resume_prepared`. Add a dedicated `RunToolInvocation` table later only
  if aggregate querying/cost analytics need indexes beyond the event stream.
- Queue stats, ops metrics, run list/status UI, editor store, and node cards
  now understand `waiting`.
- Extended approval pause/resume protocol fields through remote execution:
  - `remote_dispatch.assign_run(...)` accepts `pause_on_approval` and
    `agent_action_resume`;
  - agent, Docker, and Kubernetes runner payloads include those fields;
  - `noodle_runner_agent.process_pool.run_workflow_subprocess(...)` forwards
    them into `noodle_runtime`.
- Added `ai_agent_v2` in `packages/nodes/noodle_nodes/ai_v2/agents.py`.
  This is intentionally registered as a new id so saved legacy graphs can still
  execute while new workflows use the typed v2 agent path.
- Added `ai_tool_bundle` in `packages/nodes/noodle_nodes/ai_v2/tools.py`
  because the current graph model supports one edge per target input name.
  The bundle lets multiple `ai_tool` supplier outputs feed the agent's single
  typed tool port.
- Added manifest-level `hidden`, `deprecated`, and `replacement_id` metadata in
  `NodeManifest` and the `@node(...)` decorator/static discovery path. The
  editor keeps hidden manifests in the store for saved workflow hydration and
  source lookup, but filters them out of the side palette and command palette.
- Marked the legacy `llm.py` agent stack as hidden/deprecated:
  `ai_chat_model -> ai_chat_model_openai`,
  `ai_memory_buffer -> ai_buffer_memory`, `ai_tool -> ai_http_tool`,
  `ai_tool_box -> ai_tool_bundle`, and `ai_agent -> ai_agent_v2`.
- `WorkflowToolAdapter.invoke_async(...)` now calls
  `noodle.context.workflow_caller` when available, so AI workflow tools can run
  through the same host sub-workflow path as the `Execute Workflow` node.
- Updated shared serialization so Pydantic runtime models serialize as JSON
  instead of opaque object previews.
- Updated remote Docker dispatch event forwarding so `agent_*` events are not
  dropped outside local execution.
- Added durable `run_events` persistence for `agent_*` events:
  - new `RunEvent` ORM model and `0032_run_events` Alembic migration;
  - runner stores serialized/redacted agent events with a stable sequence;
  - `/runs/{run_id}/timeline` merges persisted agent events with node
    start/finish events;
  - retention deletes `RunEvent` rows alongside old runs on SQLite/Postgres.
- Added dedicated execution timeline rendering for `agent_*` events in the web
  app: readable labels, agent/tool/step/status summaries, and visual grouping.
- Added execution-page approval controls in the web app. Pending approvals show
  approve/reject actions; auto-approved/rejected/approved records show as run
  history.
- Follow-up added from product discussion: auto-approve is intentionally a
  first-class, visible AI Agent v2 option rather than a hidden advanced-only
  setting. The editor now honors manifest `display_name`, so this field renders
  as `Tool approval`.
- Added tests:
  - `packages/core/tests/test_engine_agent_actions.py` (9 tests covering
    single tool call, resumable loop, failed tool call, multiple tool calls,
    max-step enforcement, side-effect blocking, and side-effect execution when
    explicitly allowed, pause/resume after approval, cancellation propagation,
    and the `agent_tool_auto_approved` event)
  - new runtime-contract assertions in `packages/core/tests/test_ai_runtime.py`
  - new AI v2 agent/tool-bundle assertions in
    `packages/nodes/tests/test_ai_v2_nodes.py`
    including tool side-effect metadata and the agent `side_effect_approval`
    auto-approve option
  - `packages/core/tests/test_sdk.py` assertions that hidden/deprecated
    manifest metadata survives runtime registration and static discovery
  - `packages/nodes/tests/test_llm_nodes.py::test_legacy_agent_stack_is_hidden_with_v2_replacements`
  - `apps/api/tests/test_nodes.py` assertions that `/nodes` returns the
    legacy hidden/deprecated metadata for editor hydration
  - `apps/api/tests/test_runs.py::test_run_timeline_includes_persisted_agent_events`
  - `apps/api/tests/test_runs.py::test_run_approvals_are_recorded_and_decidable`
  - `apps/api/tests/test_runs.py::test_approval_decision_requeues_waiting_run`
    now covers `agent_resume_prepared` and skipped non-restorable supplier
    output audit data
- Verification run for this slice:
  - `uv run pytest packages/core/tests`
  - `uv run pytest packages/core/tests/test_ai_runtime.py packages/core/tests/test_engine_agent_actions.py packages/core/tests/test_engine.py`
  - `uv run pytest packages/nodes/tests/test_ai_v2_nodes.py`
  - `uv run pytest packages/core/tests packages/nodes/tests/test_ai_v2_nodes.py`
    (145 passed, 1 skipped)
  - `uv run pytest apps/api/tests/test_runs.py`
  - `uv run pytest apps/api/tests/test_retention.py`
  - `npm run build` from `apps/web`
  - `uv run ruff check apps/api/app/services/runtime_pool.py packages/runtime/noodle_runtime/server.py apps/api/app/services/runner.py`
  - `uv run pytest packages/runtime/tests/test_server.py`
    (3 passed)
  - `uv run pytest packages/core/tests packages/nodes/tests/test_ai_v2_nodes.py packages/runtime/tests/test_server.py apps/api/tests/test_runs.py apps/api/tests/test_retention.py`
    (184 passed, 1 skipped)
  - `uv run ruff check` on the full WP11 Python touched-file set including
    runtime protocol files
  - `npm run build` from `apps/web`
  - `uv run ruff check apps/api/app/services/remote_dispatch.py apps/api/app/services/runner.py packages/runner/noodle_runner_agent/process_pool.py packages/runner/noodle_runner_agent/agent.py packages/runner/noodle_runner_agent/k8s_entrypoint.py`
  - Re-ran full WP11 validation after remote protocol wiring:
    `uv run pytest packages/core/tests packages/nodes/tests/test_ai_v2_nodes.py packages/runtime/tests/test_server.py apps/api/tests/test_runs.py apps/api/tests/test_retention.py`
    (184 passed, 1 skipped), `uv run ruff check` on the full WP11 Python
    touched-file set, and `npm run build` from `apps/web`.
  - `uv run pytest packages/core/tests/test_engine_agent_actions.py`
    (9 passed)
  - Final WP11 validation after cancellation coverage:
    `uv run pytest packages/core/tests packages/nodes/tests/test_ai_v2_nodes.py packages/runtime/tests/test_server.py apps/api/tests/test_runs.py apps/api/tests/test_retention.py`
    (185 passed, 1 skipped), `uv run ruff check` on the full WP11 Python
    touched-file set, and `npm run build` from `apps/web`.
  - `uv run ruff check` on all touched Python files
  - `uv run pytest packages/core/tests/test_engine_agent_actions.py packages/nodes/tests/test_ai_v2_nodes.py apps/api/tests/test_runs.py::test_run_timeline_includes_persisted_agent_events`
    (42 passed)
  - `uv run ruff check packages/core/noodle/ai_runtime.py packages/core/noodle/engine.py packages/core/tests/test_engine_agent_actions.py packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/noodle_nodes/ai_v2/tools.py packages/nodes/tests/test_ai_v2_nodes.py apps/api/app/services/remote_dispatch.py apps/api/app/services/runner.py apps/api/app/routers/runs.py apps/api/tests/test_runs.py`
  - `npm run build` from `apps/web`
  - `uv run pytest packages/core/tests/test_engine_agent_actions.py packages/nodes/tests/test_ai_v2_nodes.py apps/api/tests/test_runs.py::test_run_timeline_includes_persisted_agent_events apps/api/tests/test_runs.py::test_run_approvals_are_recorded_and_decidable`
    (43 passed)
  - `uv run ruff check packages/core/noodle/engine.py packages/core/tests/test_engine_agent_actions.py packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_v2_nodes.py apps/api/app/models.py apps/api/app/schemas.py apps/api/app/services/runner.py apps/api/app/services/remote_dispatch.py apps/api/app/services/retention.py apps/api/app/routers/runs.py apps/api/tests/test_runs.py apps/api/alembic/versions/0033_run_approvals.py`
  - `uv run pytest apps/api/tests/test_runs.py::test_run_timeline_includes_persisted_agent_events apps/api/tests/test_runs.py::test_run_approvals_are_recorded_and_decidable apps/api/tests/test_retention.py`
    (6 passed)
  - `uv run pytest packages/core/tests/test_engine_agent_actions.py packages/nodes/tests/test_ai_v2_nodes.py`
    (41 passed)
  - `npm run build` from `apps/web`
  - `uv run pytest packages/core/tests packages/nodes/tests/test_ai_v2_nodes.py apps/api/tests/test_runs.py apps/api/tests/test_retention.py`
    (179 passed, 1 skipped)
  - `uv run ruff check` on the full WP11 Python touched-file set
  - `npm run build` from `apps/web`
  - `uv run pytest packages/core/tests/test_engine_agent_actions.py apps/api/tests/test_runs.py::test_approval_decision_requeues_waiting_run apps/api/tests/test_runs.py::test_run_approvals_are_recorded_and_decidable`
    (10 passed)
  - `uv run ruff check` on the waiting/resume Python touched-file set
  - `uv run pytest packages/core/tests packages/nodes/tests/test_ai_v2_nodes.py apps/api/tests/test_runs.py apps/api/tests/test_retention.py`
    (181 passed, 1 skipped)
  - `npm run build` from `apps/web`
  - `uv run pytest packages/core/tests/test_sdk.py packages/nodes/tests/test_llm_nodes.py apps/api/tests/test_nodes.py`
    (35 passed)
  - `uv run ruff check packages/core/noodle/models.py packages/core/noodle/sdk.py packages/core/tests/test_sdk.py packages/nodes/noodle_nodes/llm.py packages/nodes/tests/test_llm_nodes.py apps/api/tests/test_nodes.py`
  - `npm run build` from `apps/web`
  - `uv run pytest apps/api/tests/test_runs.py::test_approval_decision_requeues_waiting_run`
    (1 passed)
  - `uv run ruff check apps/api/app/services/runner.py apps/api/app/routers/runs.py apps/api/tests/test_runs.py`
  - `npm run build` from `apps/web`
  - Auto-approve visibility follow-up:
    `uv run pytest packages/nodes/tests/test_ai_v2_nodes.py::test_agent_v2_registered_with_typed_ports packages/core/tests/test_ai_runtime.py`
    (43 passed), `uv run ruff check packages/core/noodle/ai_runtime.py packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_v2_nodes.py`,
    and `npm run build` from `apps/web`.
  - Broader WP11 validation after legacy-node hiding and resume audit:
    `uv run pytest packages/core/tests packages/nodes/tests/test_ai_v2_nodes.py packages/nodes/tests/test_llm_nodes.py packages/runtime/tests/test_server.py apps/api/tests/test_runs.py apps/api/tests/test_nodes.py apps/api/tests/test_retention.py`
    (199 passed, 1 skipped), plus `uv run ruff check` on the touched Python
    files.

Deferred WP11 follow-up:

- Add integration tests with a real websocket runner and/or container runner
  once the test harness can start those services locally.

### WP12 - RAG And Vector Store Stack

Goal: support production document ingestion and retrieval workflows.

New files:

- `packages/core/noodle/ai_runtime.py`
- `packages/nodes/noodle_nodes/ai_v2/document_loaders.py`
- `packages/nodes/noodle_nodes/ai_v2/text_splitters.py`
- `packages/nodes/noodle_nodes/ai_v2/retrievers.py`
- `packages/nodes/noodle_nodes/ai_v2/vectorstores.py`
- `packages/nodes/tests/test_ai_v2_nodes.py`

Initial scope:

- file loader
- URL loader
- recursive text splitter
- OpenAI embeddings
- in-memory vector store
- PGVector or Qdrant
- vector retriever
- RAG chain

Implementation notes (current slice):

- Added provider-neutral RAG contracts in `ai_runtime.py`:
  `Document`, `RetrievedDocument`, `DocumentLoaderAdapter`,
  `VectorStoreAdapter`, and `RetrieverAdapter`.
- Added document loader suppliers:
  - `ai_text_document_loader`;
  - `ai_file_document_loader`;
  - `ai_url_document_loader`.
- Added `ai_recursive_text_splitter`, which loads documents from an
  `ai_document_loader` port and returns inspectable plain document chunks.
- Added an in-run `ai_in_memory_vector_store` supplier and
  `ai_vector_store_upsert`, which embeds chunks through an
  `ai_embedding_model` port and returns the mutated `ai_vector_store`.
- Added `ai_qdrant_vector_store`, a requests-only Qdrant vector-store adapter
  that can create a collection, upsert embedded documents, and query nearest
  points through Qdrant REST APIs.
- Added `VectorStoreAdapter.delete(...)` plus `ai_vector_store_delete`; in-memory
  and Qdrant stores can now delete document ids.
- Added `ai_vector_retriever_v2`, `ai_retrieve_documents`, and `ai_rag_chain`.
  The RAG chain retrieves context, calls the connected chat model, and returns
  answer, context documents, usage, provider, and model.
- `packages/nodes/noodle_nodes/ai_v2/__init__.py` now imports the WP12 modules
  so the nodes register with the default registry.

Tests:

- chunking behavior
- embedding adapter batching
- vector upsert/query/delete
- retriever returns normalized documents
- RAG chain cites retrieved chunks in debug metadata

Current verification:

- `uv run pytest packages/nodes/tests/test_ai_v2_nodes.py` (42 passed)
- `uv run ruff check packages/core/noodle/ai_runtime.py packages/nodes/noodle_nodes/ai_v2/__init__.py packages/nodes/noodle_nodes/ai_v2/document_loaders.py packages/nodes/noodle_nodes/ai_v2/text_splitters.py packages/nodes/noodle_nodes/ai_v2/vectorstores.py packages/nodes/noodle_nodes/ai_v2/retrievers.py packages/nodes/tests/test_ai_v2_nodes.py`
- Added `test_rag_workflow_executes_through_engine`, covering loader ->
  splitter -> embedding -> in-memory vector store -> retriever -> RAG chain via
  `execute(...)`.
- `uv run pytest packages/core/tests/test_ai_runtime.py packages/nodes/tests/test_ai_v2_nodes.py`
  (83 passed)
- `uv run pytest packages/core/tests/test_ai_runtime.py packages/nodes/tests/test_ai_v2_nodes.py apps/api/tests/test_nodes.py`
  (86 passed)
- `npm run build` from `apps/web` passed with existing chunk-size warnings.
- New WP12 node manifests use icon keys already supported by
  `apps/web/src/NodeIcon.tsx`.

Deferred WP12 follow-up:

- Add PGVector if a native Postgres vector path is still wanted after Qdrant.
- Add more persistent vector-store operations only if needed: Qdrant now covers
  collection create, upsert, query, and document-id delete.
- Add a URL loader SSRF/private-IP guard under WP14 security policy before
  production exposure.
- Add DatasetRef/document ingestion helpers if large document batches need
  artifact-backed storage instead of plain run outputs.

Done when:

- A workflow can load documents, embed them, store them, retrieve them, and feed
  an agent or RAG chain.

### WP13 - Provider Trigger Framework

Goal: add lifecycle-managed provider triggers after action nodes are stable.

Primary files:

- `apps/api/app/services/triggers.py`
- `apps/api/app/routers/triggers.py` if needed
- `packages/nodes/noodle_nodes/integrations_v2/triggers.py`
- provider trigger specs

Trigger lifecycle:

- create subscription on workflow activation
- verify webhook challenge
- persist provider subscription id and expiry
- renew subscription
- delete subscription on deactivation
- recover failed subscriptions
- dedupe events
- replay/backfill events where provider supports it

Priority triggers:

- GitHub webhooks
- Slack events
- Stripe webhooks
- Outlook mail/calendar subscriptions
- Google Drive/Sheets watch channels

Done when:

- At least one provider trigger can activate, receive events, and deactivate
  cleanly with tests.

Implementation notes:

- Added provider-trigger contracts to
  `packages/nodes/noodle_nodes/integrations_v2/specs.py`:
  `ProviderTriggerSpec`, activation/deactivation/webhook contexts,
  `ProviderTriggerSubscription`, and `ProviderTriggerEvent`.
- Extended the v2 integration node factory and registry with generated
  Python-native provider trigger nodes:
  - trigger nodes keep explicit Python function signatures;
  - manifests use `role="trigger"` and no data input ports;
  - the source viewer still shows generated Python source.
- Added `github_repository_trigger_v2`:
  - creates a GitHub repository webhook using the repo webhook REST endpoint;
  - sends a durable `/provider-webhook/{subscription_id}` callback URL;
  - requires a webhook secret credential;
  - verifies inbound `X-Hub-Signature-256` HMAC signatures;
  - acknowledges GitHub `ping` deliveries without starting a workflow;
  - dedupes real deliveries with `X-GitHub-Delivery`.
- Added durable provider subscription storage:
  - `ProviderTriggerSubscription` ORM model;
  - `0034_provider_trigger_subscriptions` Alembic migration;
  - stores workflow/version/node/provider linkage, external hook id, callback
    URL, status, expiry, last event timestamp, and redacted config metadata.
- Added API lifecycle service
  `apps/api/app/services/provider_triggers.py`:
  - syncs subscriptions on workflow activation/deactivation and active publish;
  - deletes provider subscriptions on deactivation;
  - fails activation when provider hook creation fails, so workflows are not
    silently active without provider subscriptions;
  - dispatches verified inbound provider events through `start_run(...)` with
    `trigger_type="provider"` and the provider trigger node pre-seeded in
    cache.
- Added public provider webhook ingress:
  `apps/api/app/routers/provider_webhooks.py`, mounted only when
  `webhook_role != "disabled"`.
- Added `PUBLIC_API_URL`/`settings.public_api_url` for externally reachable
  provider callback URLs. Blank falls back to `http://localhost:8000` for local
  dev/tests.
- Updated `graph_utils.is_trigger_type(...)` so registered provider trigger
  nodes participate in trigger-target gating.
- Official GitHub references checked during implementation:
  [Create repository webhook](https://docs.github.com/en/rest/webhooks/repos#create-a-repository-webhook),
  [Creating webhooks](https://docs.github.com/en/webhooks/using-webhooks/creating-webhooks),
  and
  [Validating webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).

Verification:

- `uv run pytest packages/nodes/tests/test_integrations_v2_registry.py packages/nodes/tests/test_github_provider_triggers.py`
  (9 passed)
- `uv run pytest apps/api/tests/test_triggers.py::test_github_provider_trigger_lifecycle_and_dispatch`
  (1 passed)
- `uv run pytest apps/api/tests/test_triggers.py apps/api/tests/test_trigger_gated_runs.py packages/nodes/tests/test_integrations_v2_registry.py packages/nodes/tests/test_github_provider_triggers.py`
  (52 passed)
- `uv run pytest apps/api/tests/test_nodes.py packages/nodes/tests/test_builtin_nodes.py`
  (32 passed)
- `uv run ruff check` on the WP13 touched Python files
- `npm run build` from `apps/web` passed with existing chunk-size warnings.

Deferred WP13 follow-up:

- Add renewal/recovery loop for expiring providers such as Microsoft Graph and
  Google watch channels.
- Add Slack, Stripe, Outlook, Google Drive/Sheets provider trigger specs.
- Add provider-trigger list/status UI so users can inspect active/error/deleted
  subscription rows from the workflow/deployment screens.
- Add replay/backfill where providers expose delivery history.
- Add provider-specific signature tests for Slack/Stripe once those specs land.

### WP14 - Observability, Security, And Production Policy

Goal: make production runs auditable and safe.

Primary files:

- `apps/api/app/services/runner.py`
- `apps/api/app/services/redaction.py`
- `apps/api/app/models.py`
- `apps/web/src/editor/NDVPanels.tsx`
- run history UI

Add:

- provider request metadata without secrets
- retry and latency events
- credential id used, never secret values
- model usage and estimated cost
- agent tool call timeline
- guardrail events
- approval audit events
- unsafe-node activation policy
- SSRF/private IP protection for HTTP and AI HTTP tools

Tests:

- redaction tests
- event shape tests
- unsafe-node policy tests
- SSRF guard tests

Done when:

- Runs can be debugged without exposing secrets.
- Destructive or side-effecting AI tools can require approval.

Implementation notes:

- Added runtime SSRF/private-network protection in
  `packages/nodes/noodle_nodes/http_security.py`.
  - Blocks non-HTTP(S) schemes.
  - Blocks literal localhost/private/loopback/link-local/unspecified/reserved
    targets.
  - Performs best-effort DNS resolution and blocks hostnames that resolve to
    private/loopback/link-local addresses when resolution succeeds.
- Applied the guard to:
  - `http_request`;
  - `graphql_request`;
  - `ai_http_tool` / `HttpToolAdapter.invoke(...)`;
  - `ai_url_document_loader` / `UrlDocumentLoaderAdapter.load(...)`.
- Added tests proving private targets are blocked before `requests` is called.
- Updated `packages/nodes/tests/test_builtin_nodes.py` so the trigger/input
  assertion now accounts for typed supplier/tool nodes with no main input.
- Added provider-trigger receipt events:
  - provider webhook dispatch now writes a redacted
    `provider_trigger_received` `RunEvent` with subscription id, provider,
    trigger key, dedupe key, event name, delivery id, and repository name when
    available;
  - `/runs/{run_id}/timeline` merges provider trigger events with node and AI
    agent events in a stable order;
  - the executions UI renders provider-trigger timeline rows with readable
    summaries;
  - the GitHub provider-trigger lifecycle test now asserts the persisted
    timeline event contains provider metadata and does not leak the webhook
    secret.
- Added provider-trigger status visibility:
  - workflow summaries/details now include provider trigger status counts for
    active, activating, error, and deleted subscriptions;
  - added `GET /workflows/{workflow_id}/provider-triggers` for read-only
    inspection of subscription rows, callback URLs, last event timestamps, and
    redacted config metadata;
  - the workflow list shows provider-trigger active/activating/error chips and
    a Trigger errors dashboard filter;
  - clicking a provider-trigger chip opens a status modal with subscription
    rows.
- Added provider subscription audit events:
  - successful provider-trigger activation writes an `activate`
    `provider_trigger_subscription` audit row;
  - provider-trigger deactivation writes a `deactivate`
    `provider_trigger_subscription` audit row;
  - workflow update/publish paths pass actor id/email into provider-trigger
    sync so audit rows are attributable when auth is enabled;
  - audit details include provider, trigger key, node id, and workflow id, but
    never node parameters or credential values.
- Added redacted provider delivery metadata:
  - provider webhook dispatch measures provider handler latency and response
    status;
  - subscription config stores a compact `last_delivery` object with status,
    latency, duplicate flag, event name, delivery id, and repository name when
    available;
  - raw webhook bodies, raw headers, signatures, credentials, and node params
    are not stored in delivery metadata;
  - `provider_trigger_received` timeline events now include response status and
    latency;
  - the workflow provider-trigger status modal and executions timeline render
    the new status/latency metadata.
- Added outbound provider request retry/latency debug events:
  - `ProviderTransport` records one redacted `provider_requests` debug event per
    HTTP attempt while a node is executing;
  - events include provider, operation, method, URL without query string,
    attempt number, max attempts, latency, outcome, status code, retryability,
    retry-scheduled flag, request id, and provider error code when available;
  - headers, query strings, request bodies, credentials, and response bodies are
    not stored in debug events;
  - all v2 provider operation nodes inherit this through the shared transport.
- Added configurable AI usage cost estimation:
  - `ModelUsage` now carries optional `estimated_cost_usd`,
    `prompt_price_per_1m_tokens`, and `completion_price_per_1m_tokens` fields;
  - `ModelUsage.with_estimated_cost(...)` estimates USD cost from
    caller-provided per-million-token rates without hard-coding provider
    pricing;
  - AI chat model suppliers expose optional input/output token price fields and
    pass them through OpenAI-compatible, Azure OpenAI, and Anthropic adapters;
  - embedding model suppliers expose optional embedding-token price and pass it
    through OpenAI-compatible embedding adapters when provider usage includes
    prompt token counts;
  - agent and RAG outputs keep using the normalized usage object, so cost
    estimates appear wherever usage already appears.
- Added guardrail event persistence and UI:
  - keyword guardrails record sanitized `guardrail_blocked` and
    `guardrail_redacted` events in node debug without storing response text,
    blocked terms, regex patterns, or matched values;
  - the API runner promotes guardrail debug events into durable `RunEvent`
    timeline rows;
  - timeline ordering places guardrail events near the node that produced them;
  - the executions UI renders guardrail blocked/redacted labels and summaries.

Verification:

- `uv run pytest packages/nodes/tests/test_builtin_nodes.py::test_http_request_blocks_private_targets packages/nodes/tests/test_builtin_nodes.py::test_graphql_request_blocks_private_targets packages/nodes/tests/test_ai_v2_nodes.py::test_http_tool_blocks_private_targets packages/nodes/tests/test_ai_v2_nodes.py::test_url_document_loader_blocks_private_targets`
  (4 passed)
- `uv run pytest packages/nodes/tests/test_builtin_nodes.py packages/nodes/tests/test_ai_v2_nodes.py`
  (76 passed)
- `uv run ruff check packages/nodes/noodle_nodes/http_security.py packages/nodes/noodle_nodes/builtin.py packages/nodes/noodle_nodes/ai_v2/tools.py packages/nodes/noodle_nodes/ai_v2/document_loaders.py packages/nodes/tests/test_builtin_nodes.py packages/nodes/tests/test_ai_v2_nodes.py`
- `uv run pytest apps/api/tests/test_triggers.py::test_github_provider_trigger_lifecycle_and_dispatch`
  (1 passed)
- `uv run ruff check apps/api/app/services/provider_triggers.py apps/api/app/routers/runs.py apps/api/tests/test_triggers.py`
- `npm run build` from `apps/web` passed with existing chunk-size warnings.
- `uv run pytest apps/api/tests/test_triggers.py::test_github_provider_trigger_lifecycle_and_dispatch apps/api/tests/test_workflows.py`
  (7 passed)
- `uv run ruff check apps/api/app/schemas.py apps/api/app/routers/workflows.py apps/api/tests/test_triggers.py`
- `uv run pytest apps/api/tests/test_triggers.py::test_github_provider_trigger_lifecycle_and_dispatch apps/api/tests/test_workflows.py apps/api/tests/test_enterprise.py::test_audit_log_records_actions`
  (8 passed)
- `uv run ruff check apps/api/app/services/provider_triggers.py apps/api/app/routers/workflows.py apps/api/tests/test_triggers.py`
- `uv run pytest apps/api/tests/test_triggers.py::test_github_provider_trigger_lifecycle_and_dispatch`
  (1 passed)
- `uv run ruff check apps/api/app/services/provider_triggers.py apps/api/tests/test_triggers.py`
- `npm run build` from `apps/web` passed with existing chunk-size warnings.
- `uv run pytest packages/nodes/tests/test_integrations_v2_transport.py`
  (5 passed)
- `uv run ruff check packages/nodes/noodle_nodes/integrations_v2/transport.py packages/nodes/tests/test_integrations_v2_transport.py`
- `uv run pytest packages/core/tests/test_ai_runtime.py packages/nodes/tests/test_ai_v2_nodes.py`
  (88 passed)
- `uv run ruff check packages/core/noodle/ai_runtime.py packages/core/tests/test_ai_runtime.py packages/nodes/noodle_nodes/ai_v2/factory.py packages/nodes/noodle_nodes/ai_v2/models.py packages/nodes/noodle_nodes/ai_v2/embeddings.py packages/nodes/noodle_nodes/ai_v2/providers/openai.py packages/nodes/noodle_nodes/ai_v2/providers/anthropic.py packages/nodes/noodle_nodes/ai_v2/providers/embeddings.py packages/nodes/tests/test_ai_v2_nodes.py`
- `uv run pytest packages/nodes/tests/test_ai_v2_nodes.py::test_guardrail_blocks_terms packages/nodes/tests/test_ai_v2_nodes.py::test_guardrail_redacts_patterns apps/api/tests/test_runs.py::test_run_timeline_includes_persisted_guardrail_events`
  (3 passed)
- `uv run ruff check packages/nodes/noodle_nodes/ai_v2/guardrails.py packages/nodes/tests/test_ai_v2_nodes.py apps/api/app/services/runner.py apps/api/app/routers/runs.py apps/api/tests/test_runs.py`
- `npm run build` from `apps/web` passed with existing chunk-size warnings.
- Broader WP13/WP14 regression:
  `uv run pytest packages/core/tests/test_ai_runtime.py packages/nodes/tests/test_ai_v2_nodes.py packages/nodes/tests/test_integrations_v2_transport.py packages/nodes/tests/test_integrations_v2_registry.py packages/nodes/tests/test_github_provider_triggers.py apps/api/tests/test_triggers.py apps/api/tests/test_workflows.py apps/api/tests/test_runs.py::test_run_timeline_includes_persisted_agent_events apps/api/tests/test_runs.py::test_run_timeline_includes_persisted_guardrail_events apps/api/tests/test_enterprise.py::test_audit_log_records_actions`
  (143 passed)
- Broad WP13/WP14 lint sweep passed after small style cleanups in
  `ai_v2/memory.py`, `google_sheets/__init__.py`,
  `google_sheets/options.py`, and `microsoft_outlook/__init__.py`.

Remaining WP14 work: none for this planned slice.

### WP15 - Release Cleanup And Documentation

Goal: make the system maintainable after the architecture lands.

Steps:

- Update README and developer docs.
- Add provider coverage matrix.
- Add generator docs.
- Document how to create a credential type.
- Document how to create an operation.
- Document how to create an AI provider.
- Remove dead legacy tests.
- Update example workflows.

Done when:

- A new contributor can add a provider operation using the generator and tests
  without reading n8n source.

Implementation notes (in progress):

- Added `docs/provider-coverage-matrix.md` to track official provider coverage
  across v2 operations, provider triggers, credentials, status, tests, and next
  targets.
- Added `docs/integration-development.md`, a contributor guide for building
  spec-driven Python-native provider operations/triggers with credentials,
  dynamic options, shared transport, observability, and required tests.
- Linked both docs from `docs/README.md` and the root `README.md`.
- Replaced the stale local-plan pointer in `docs/README.md` with the in-repo
  production plan link.
- Updated deployment/config docs with `PUBLIC_API_URL`, `WEBHOOK_ROLE`, and the
  provider-managed trigger callback requirement.
- Extended `docs/integration-development.md` with concrete credential spec
  examples, read-only credential test-handler patterns, `_TESTERS`
  registration, and the central redaction/latency behavior.
- Updated `docs/status-matrix.md` to mark the webhook ingress role shipped now
  that production `/webhook/{path}` and provider-managed
  `/provider-webhook/{subscription_id}` routes are not mounted when
  `WEBHOOK_ROLE=disabled`.
- Removed the stale README known-gap note for the webhook ingress role split and
  corrected deployment docs for the `WEBHOOK_ROLE=ingress` default.
- Added missing read-only credential test handlers for `openrouter`, `qdrant`,
  and `microsoft_outlook`.
- Updated `/credentials/{id}/test` to resolve a backend credential type's
  declared `test_service` before running the connection test, so OAuth ids such
  as `microsoft_outlook_oauth2` route to their provider tester.
- Added credential tests that assert every advertised credential `test_service`
  has a registered handler and that Outlook OAuth credentials use the
  `microsoft_outlook` tester.

Verification:

- Manual docs review after creation.
- `uv run pytest apps/api/tests/test_credentials_v2.py::test_list_credential_test_handlers_returns_registered_services apps/api/tests/test_credentials_v2.py::test_credential_type_test_services_have_handlers apps/api/tests/test_credentials_v2.py::test_oauth_credential_connection_uses_declared_test_service`
  (3 passed)
- `uv run ruff check apps/api/app/services/credential_tests.py apps/api/app/routers/credentials.py apps/api/tests/test_credentials_v2.py`
- Broader final validation:
  `uv run pytest apps/api/tests/test_credentials_v2.py apps/api/tests/test_triggers.py apps/api/tests/test_workflows.py apps/api/tests/test_nodes.py apps/api/tests/test_runs.py::test_run_timeline_includes_persisted_agent_events apps/api/tests/test_runs.py::test_run_timeline_includes_persisted_guardrail_events apps/api/tests/test_enterprise.py::test_audit_log_records_actions packages/core/tests/test_ai_runtime.py packages/nodes/tests/test_ai_v2_nodes.py packages/nodes/tests/test_integrations_v2_transport.py packages/nodes/tests/test_integrations_v2_registry.py packages/nodes/tests/test_github_provider_triggers.py packages/nodes/tests/test_google_sheets_v2.py packages/nodes/tests/test_integrations_v2_providers.py`
  (191 passed, 1 existing Pydantic warning)
- `npm run build` from `apps/web` passed with existing chunk-size warnings.
- Broad Python lint sweep passed:
  `uv run ruff check apps/api/app/services/credential_tests.py apps/api/app/routers/credentials.py apps/api/app/services/provider_triggers.py apps/api/app/services/runner.py apps/api/app/routers/runs.py apps/api/app/routers/workflows.py apps/api/app/routers/provider_webhooks.py apps/api/app/schemas.py apps/api/tests/test_credentials_v2.py apps/api/tests/test_triggers.py apps/api/tests/test_runs.py packages/core/noodle/ai_runtime.py packages/core/tests/test_ai_runtime.py packages/nodes/noodle_nodes/http_security.py packages/nodes/noodle_nodes/builtin.py packages/nodes/noodle_nodes/ai_v2 packages/nodes/noodle_nodes/integrations_v2 packages/nodes/tests/test_ai_v2_nodes.py packages/nodes/tests/test_builtin_nodes.py packages/nodes/tests/test_integrations_v2_transport.py packages/nodes/tests/test_integrations_v2_registry.py packages/nodes/tests/test_github_provider_triggers.py packages/nodes/tests/test_google_sheets_v2.py packages/nodes/tests/test_integrations_v2_providers.py`
- After mechanical import sorting, reran
  `uv run pytest packages/nodes/tests/test_integrations_v2_providers.py`
  (27 passed, 1 existing Pydantic warning).

Remaining WP15 work: none.

## Continuation Prompt Template

If another LLM needs to resume work, use this prompt:

```text
We are in D:\noodle. Read docs/production-automation-platform-plan.md first.
Continue the next incomplete work package. Keep all nodes Python-native, keep
the NDV Inspector | Python source path working, and do not copy n8n code from
D:\n8n-master. Run git status before editing, do not revert user changes, and
update the plan with files changed, tests run, risks, and remaining work.
```

## Success Criteria

Noodle is production-ready when:

- integrations are generated from specs and tested with mocked provider calls
- OAuth credentials refresh automatically
- typed AI ports prevent invalid wiring
- AI agents execute tools through the engine with visible step traces
- destructive AI tool calls can require approval
- RAG workflows support real document loading and vector stores
- provider triggers activate, renew, and deactivate reliably
- runs are observable, cancellable, retryable, and auditable
- secrets are never exposed in workflow JSON, logs, or debug snapshots
