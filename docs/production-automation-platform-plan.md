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
| WP1 | planned | Core manifest schema v2 |
| WP2 | planned | Typed connection validation |
| WP3 | planned | Backend credential type registry and OAuth |
| WP4 | planned | Provider transport layer |
| WP5 | planned | Integration SDK and operation registry |
| WP6 | planned | Google Sheets v2 provider |
| WP7 | planned | Microsoft Outlook v2 provider |
| WP8 | planned | Legacy wrapper replacement |
| WP9 | planned | AI runtime contracts |
| WP10 | planned | AI v2 supplier nodes |
| WP11 | planned | Agent engine request/resume loop |
| WP12 | planned | RAG and vector store stack |
| WP13 | planned | Provider trigger framework |
| WP14 | planned | Observability, security, and production policy |
| WP15 | planned | Release cleanup and documentation |

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

### WP11 - Agent Engine Request/Resume Loop

Goal: make agent tool execution observable, resumable, and approval-aware.

Primary files:

- `packages/core/noodle/engine.py`
- `packages/core/noodle/models.py`
- `apps/api/app/models.py`
- `apps/api/app/services/runner.py`
- `apps/web/src/editor/NDVPanels.tsx`
- run/event UI components

New concepts:

- `AgentActionRequest`
- `AgentActionResponse`
- `AgentStepEvent`
- pending approval state
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
- resume after process restart if persistence is implemented

Done when:

- Agent steps are visible in run history.
- Tool execution is not hidden inside one opaque node invocation.

### WP12 - RAG And Vector Store Stack

Goal: support production document ingestion and retrieval workflows.

New files:

- `packages/nodes/noodle_nodes/ai_v2/document_loaders.py`
- `packages/nodes/noodle_nodes/ai_v2/text_splitters.py`
- `packages/nodes/noodle_nodes/ai_v2/retrievers.py`
- `packages/nodes/noodle_nodes/ai_v2/vectorstores.py`

Initial scope:

- file loader
- URL loader
- recursive text splitter
- OpenAI embeddings
- in-memory vector store
- PGVector or Qdrant
- vector retriever
- RAG chain

Tests:

- chunking behavior
- embedding adapter batching
- vector upsert/query/delete
- retriever returns normalized documents
- RAG chain cites retrieved chunks in debug metadata

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
