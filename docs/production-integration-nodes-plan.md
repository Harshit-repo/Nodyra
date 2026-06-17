# Production Integration Nodes Phased Plan

This plan turns Noodle's current integration work into a phased backlog for
production-level provider nodes. It assumes the existing v2 integration
architecture remains the standard path:

- nodes stay Python-native;
- provider operations are defined with `OperationSpec`;
- provider triggers are defined with `ProviderTriggerSpec`;
- credentials are backend-owned and encrypted;
- provider HTTP calls go through shared transports;
- provider tests use mocked calls by default;
- legacy wrappers stay visible until v2 replacements are complete.

The goal is not only to add more nodes. The goal is to make every official
provider pack reliable enough for production workflows, with credentials,
observability, tests, docs, and lifecycle behavior included.

## Current Baseline

Already in place:

- v2 operation registry and generated Python node manifests.
- v2 provider trigger framework with durable provider subscriptions.
- shared `ProviderTransport` with retries, rate-limit hints, structured
  provider errors, request ids, latency, and redacted debug events.
- backend credential type registry and credential test contract.
- OAuth start/callback/refresh plumbing for supported providers.
- dynamic option loader registry.
- provider coverage matrix.
- current v2 providers for Google Sheets, Microsoft Outlook, GitHub, Slack,
  Stripe, Airtable, and Notion.
- generic HTTP, GraphQL, webhook, schedule, manual, error, and API endpoint
  trigger nodes.
- node-as-tool mode in the engine and editor: executable action nodes can emit
  an `ai_tool` output, and `$fromAI(...)` parameter expressions define the
  arguments an AI Agent supplies at call time.

Known gaps:

- several credential test handlers exist without matching backend credential
  type specs.
- multiple provider packs are still shallow: one to five operations each.
- provider trigger renewal and recovery is not complete for expiring
  subscription providers.
- many legacy wrappers in `communication.py`, `saas.py`, `storage.py`,
  `cloud_devops.py`, and unreplaced sections of `integrations.py` are not v2
  production packs yet.
- dynamic provider resource loaders are incomplete for several existing v2
  providers.
- many pre-v2 wrapper nodes are tool-capable through the SDK default, but their
  `tool_side_effecting` metadata still needs provider-by-provider review.
- provider packs need tool-mode examples that show which params should stay
  fixed as credentials/resource ids and which params are safe to mark
  `From AI`.

## Tool-Mode Review Update

Node-as-tool mode changes the rollout priority. Every provider operation now
has two user-facing modes:

- normal workflow node;
- AI Agent tool with fixed params plus `$fromAI(...)` model-supplied params.

This makes metadata quality more important than before. New provider work must
include:

- strong parameter names and descriptions because they become AI tool schema
  text;
- explicit `tool_side_effecting=False` for read-only operations;
- conservative side-effect approval for writes, sends, deletes, clears,
  refunds, payments, and external state changes;
- tests proving generated v2 operations expose the right tool flags;
- examples that keep credentials, workspace ids, channel ids, account ids, and
  other sensitive routing params fixed rather than AI-supplied.

Immediate implementation focus after this review:

1. finish provider-depth slices for the current v2 packs;
2. add missing dynamic options for the deeper operation packs;
3. add trigger reliability for Slack, Stripe, Microsoft Graph, and Google watch
   channels;
4. run a legacy wrapper tool-safety pass for existing non-v2 integrations.

## Production Definition

A provider node pack is production-ready only when it includes the following.

### Provider Metadata

- provider package under
  `packages/nodes/noodle_nodes/integrations_v2/providers/<provider>/`;
- stable provider id, display name, icon, documentation links, and resource
  names;
- coverage matrix row updated for operations, triggers, credentials, status,
  tests, and remaining gaps.

### Credentials

- backend `CredentialTypeSpec` for each supported auth method;
- least-privilege default scopes for OAuth credentials;
- read-only credential test handler;
- redaction tests for credential-adjacent errors and output paths;
- OAuth refresh support where applicable;
- clear missing-scope behavior in manifests and UI metadata.

### Operation Nodes

- `OperationSpec` for every operation;
- explicit Python executor signature;
- `usable_as_tool` defaults to true for executable operation nodes, unless the
  operation cannot safely be called by an AI agent;
- `tool_side_effecting` is true for create/update/delete/send/clear operations
  and false for read-only list/get/search/metadata operations;
- shared provider transport or narrow provider-specific transport subclass;
- grouped optional parameters;
- validation metadata for required ids, emails, URLs, enums, and numeric limits;
- pagination handling for list/search operations;
- idempotency keys for create/update operations where the provider supports
  them;
- stable structured errors with provider, operation, status, code, message,
  retryability, and request id where available.

### Dynamic Options

- loaders for workspaces, teams, projects, users, channels, tables, fields,
  labels, lists, stores, accounts, and other provider resources;
- loaders use credentials and transports consistently with operations;
- loaders return compact option objects only;
- loader tests cover missing context, provider error, and happy path.

### Trigger Nodes

- `ProviderTriggerSpec` for subscription-based triggers;
- activation creates the provider subscription;
- deactivation deletes the provider subscription;
- challenge and signature verification tests;
- duplicate delivery dedupe;
- safe delivery metadata only, never raw headers, bodies, signatures, or
  credential values;
- renewal/recovery for expiring subscriptions;
- status, audit, and timeline events.

### Tests And Docs

- manifest registration and generated source tests;
- tool-mode compatibility tests proving generated operation manifests expose
  `usable_as_tool` and the correct `tool_side_effecting` value;
- mocked operation tests for success, auth failure, provider error, retries,
  pagination, and redaction;
- mocked trigger lifecycle tests for activation, deactivation, challenge,
  signature failure, duplicate delivery, dispatch, audit, and timeline metadata;
- focused UI tests for credential matching and dynamic options when metadata
  changes;
- docs in `docs/provider-coverage-matrix.md` and provider-specific notes when
  behavior is non-obvious.

## Ordering Principles

1. Credentials before operations.
2. Core action operations before provider triggers.
3. Read/list/get before create/update/delete.
4. Dynamic options before broad adoption in templates.
5. Hide legacy wrappers only after equivalent v2 coverage and tests exist.
6. Add provider trigger renewal before adding multiple expiring trigger
   providers.
7. Prefer depth in high-value providers over shallow coverage across many
   providers.
8. Keep generic `HTTP Request` and `GraphQL Request` as escape hatches.
9. Optional live provider tests must be environment-gated and never required in
   normal CI.

## Phase 0 - Foundation Cleanup

Goal: remove framework and catalog gaps before adding a large number of new
providers.

### Deliverables

- Add backend credential type specs for credential ids already used by official
  nodes:
  - `stripe`
  - `airtable`
  - `notion`
  - `discord_webhook`
  - `smtp`
  - `postgres`
  - `mysql`
  - `aws`
  - `mongodb`
  - `redis`
  - `elasticsearch`
  - `azure_blob`
  - `telegram_bot`
  - `teams_webhook`
  - `sendgrid`
  - `twilio`
  - `pushover`
  - legacy SaaS credentials from `saas.py`.
- Add or confirm read-only test handlers for those credential types.
- Add tests asserting every advertised credential `test_service` exists.
- Add tests asserting every official node credential type exists in the backend
  credential catalog, except explicitly documented development-only types.
- Add tests asserting every v2 provider operation manifest is compatible with
  node-as-tool mode unless explicitly opted out.
- Add provider guidance for tool-mode safety:
  - mark read-only operations as non-side-effecting;
  - keep write/send/delete operations side-effecting;
  - avoid provider outputs that expose secrets or raw request details to an AI
    agent;
  - use clear parameter descriptions because `$fromAI` tool schemas derive from
    parameter metadata.
- Create a provider-pack implementation checklist in docs.
- Expand the provider coverage matrix with columns for:
  - credential catalog;
  - dynamic options;
  - trigger renewal;
  - legacy replacement status.
- Add shared helper guidance for:
  - cursor/token pagination;
  - next-link pagination;
  - file upload/download operations;
  - idempotency keys;
  - provider-specific 200-with-error response shapes.

### Acceptance Criteria

- Existing v2 and legacy official nodes do not reference unknown credential
  types.
- New v2 operation nodes appear in the editor as usable AI tools once
  node-as-tool mode is enabled.
- Contributors can add a provider by following one checklist.
- Coverage matrix clearly distinguishes shipped, beta, planned, and legacy
  wrapper status.

## Phase 1 - Complete Existing V2 Provider Packs

Goal: turn current beta v2 providers into credible production packs before
starting broad new provider families.

### 1A - Google Sheets

Add operations:

- create spreadsheet;
- get spreadsheet metadata;
- add sheet;
- rename sheet;
- delete sheet;
- read values;
- append values;
- update values;
- clear values;
- lookup row;
- upsert row;
- batch update values.

Add dynamic options:

- spreadsheet tabs;
- header row columns;
- named ranges where available.

Add triggers:

- spreadsheet updated watch channel;
- sheet/range changed watch channel where feasible.

Production notes:

- Google watch channels expire and require renewal.
- Activation should fail clearly if `PUBLIC_API_URL` is not externally
  reachable.
- Service account and OAuth credential flows should both be documented.

### 1B - Microsoft Outlook

Add operations:

- send mail;
- list messages;
- get message;
- reply to message;
- forward message;
- create draft;
- send draft;
- download attachment metadata/content;
- list calendar events;
- create calendar event;
- update calendar event;
- delete calendar event.

Add dynamic options:

- mail folders;
- calendars;
- users where delegated permissions allow.

Add triggers:

- new message subscription;
- message updated subscription;
- calendar event created/updated subscription.

Production notes:

- Microsoft Graph subscriptions expire and need renewal.
- Subscription activation should persist expiry and renewal status.

### 1C - GitHub

Add operations:

- get repository;
- list issues;
- create issue;
- update issue;
- comment on issue;
- list pull requests;
- get pull request;
- create pull request;
- merge pull request;
- create release;
- get file contents;
- create/update file contents;
- dispatch workflow.

Add dynamic options:

- repositories;
- branches;
- labels;
- workflows.

Add triggers:

- repository event trigger already exists;
- add event filters for issues, pull requests, releases, push, workflow runs.

Production notes:

- Keep webhook signature validation mandatory for production triggers.
- Store only safe delivery metadata: delivery id, event name, repository, action.

### 1D - Slack

Add operations:

- send message;
- reply in thread;
- update message;
- delete message;
- upload file;
- add reaction;
- list users;
- list channels;
- open direct message.

Add dynamic options:

- channels;
- users;
- emoji/reaction names if cheap enough.

Add triggers:

- signed event trigger;
- slash command trigger if the callback contract can be made clear.

Production notes:

- Slack returns application errors in HTTP 200 responses; all operations must
  convert `ok: false` into `ProviderError`.
- Event trigger must verify Slack signing secret and timestamp freshness.

### 1E - Stripe

Add operations:

- create/get/update/list customer;
- create checkout session;
- create/get/list payment intent;
- create refund;
- create/list invoice;
- create/list subscription;
- cancel subscription;
- list products/prices.

Add dynamic options:

- products;
- prices;
- customers.

Add triggers:

- checkout session completed;
- payment intent succeeded/failed;
- invoice paid/payment failed;
- customer subscription created/updated/deleted.

Production notes:

- Use idempotency keys for create operations where possible.
- Stripe webhook signatures must be verified against raw body bytes.

### 1F - Airtable

Add operations:

- list records;
- get record;
- create record;
- update record;
- delete record;
- batch create records;
- batch update records;
- upsert records.

Add dynamic options:

- bases;
- tables;
- views;
- fields.

Add triggers:

- webhook trigger where provider plan/API supports it;
- polling fallback for changed records if webhook support is constrained.

Production notes:

- Field mapping should avoid forcing users to hand-write field names for common
  operations.

### 1G - Notion

Add operations:

- search;
- query database;
- get page;
- create page;
- update page properties;
- archive page;
- append block children;
- retrieve block children.

Add dynamic options:

- databases;
- pages;
- database properties;
- select/status options.

Add triggers:

- polling trigger for recently edited pages/databases if no webhook path is
  available.

Production notes:

- Notion property payloads are nested; provide practical field helpers and
  examples instead of exposing only raw JSON.

### Phase 1 Acceptance Criteria

- Existing v2 provider packs cover common read/write workflows.
- Legacy wrappers for equivalent operations are hidden or deprecated with
  replacement ids.
- Templates and AI builder recommendations use v2 nodes.
- Provider coverage matrix marks each provider's remaining gaps explicitly.

## Phase 2 - Provider Trigger Reliability

Goal: make provider-managed triggers safe to depend on in production.

### Deliverables

- Renewal loop for expiring subscriptions.
- Recovery loop for failed/stale subscriptions.
- Subscription activation backoff policy.
- Manual resync endpoint for admins.
- Status timeline for activation, renewal, failure, and deactivation.
- Replay/backfill design per provider where available.
- Trigger-specific tests for Slack, Stripe, Microsoft Graph, Google watch
  channels, and Shopify once those triggers exist.

### Acceptance Criteria

- A production deployment can run provider triggers for months without manual
  reactivation when providers require periodic renewal.
- Failed renewal surfaces clearly in workflow/deployment status.
- Duplicate deliveries do not create duplicate runs when a provider supplies a
  stable delivery id.

## Phase 3 - Google And Microsoft Workspace Suites

Goal: add the highest-value office-suite integrations because they unlock many
internal automation workflows.

### 3A - Google Drive

Operations:

- list files;
- search files;
- get file metadata;
- download file;
- upload file;
- copy file;
- move file;
- delete file;
- create folder;
- update permissions;
- export Google Docs/Sheets/Slides file.

Dynamic options:

- drives;
- folders;
- MIME types.

Triggers:

- file created;
- file updated;
- file deleted if supported through change feeds/watch channels.

### 3B - Gmail

Operations:

- send message;
- search messages;
- get message;
- list labels;
- add/remove label;
- archive;
- mark read/unread;
- download attachments.

Dynamic options:

- labels;
- sender aliases where available.

Triggers:

- new email;
- label-applied email.

### 3C - Google Calendar

Operations:

- list events;
- get event;
- create event;
- update event;
- delete event;
- add attendees;
- RSVP/update attendee response.

Dynamic options:

- calendars;
- conference types where available.

Triggers:

- event created;
- event updated;
- event cancelled.

### 3D - Microsoft Excel

Operations:

- list workbooks;
- list worksheets;
- read range;
- append rows to table;
- update range;
- clear range;
- create table;
- list table rows.

Dynamic options:

- drives/sites;
- workbooks;
- worksheets;
- tables;
- columns.

### 3E - Microsoft OneDrive And SharePoint

Operations:

- list files;
- search files;
- download file;
- upload file;
- copy/move/delete;
- create folder;
- create sharing link;
- list sites and drives.

Triggers:

- file created/updated subscription.

### 3F - Microsoft Teams

Operations:

- list teams;
- list channels;
- send channel message;
- reply to message;
- send chat message where permissions allow;
- mention user or channel.

Dynamic options:

- teams;
- channels;
- users.

Triggers:

- channel message event if supported by configured Graph permissions.

### Phase 3 Acceptance Criteria

- Google and Microsoft packs can support common internal workflows without
  custom HTTP nodes.
- OAuth scopes are clear and least-privilege by operation family.
- File operations use artifact references for large downloads/uploads instead
  of stuffing file bytes into node output.

## Phase 4 - Work Management And CRM

Goal: support operational workflows across project management, support, and
sales systems.

### 4A - Jira

Operations:

- list projects;
- list issue types;
- search issues with JQL;
- get issue;
- create issue;
- update issue;
- transition issue;
- add comment;
- attach file.

Dynamic options:

- projects;
- issue types;
- statuses/transitions;
- users;
- fields.

Triggers:

- issue created;
- issue updated;
- comment created.

### 4B - Linear

Operations:

- list teams;
- list projects;
- list issues;
- get issue;
- create issue;
- update issue;
- add comment;
- create project.

Dynamic options:

- teams;
- projects;
- assignees;
- states;
- labels.

Triggers:

- issue created/updated.

### 4C - Trello

Operations:

- list boards;
- list lists;
- list cards;
- get card;
- create card;
- update card;
- move card;
- add comment;
- add/remove label.

Dynamic options:

- boards;
- lists;
- labels;
- members.

Triggers:

- card created/updated/moved.

### 4D - Asana

Operations:

- list workspaces;
- list projects;
- list tasks;
- get task;
- create task;
- update task;
- add comment;
- add task to project.

Dynamic options:

- workspaces;
- projects;
- sections;
- assignees;
- tags.

Triggers:

- task created/updated/completed.

### 4E - HubSpot

Operations:

- contact CRUD;
- company CRUD;
- deal CRUD;
- ticket CRUD;
- search objects;
- create engagement/note;
- associate objects.

Dynamic options:

- pipelines;
- deal stages;
- owners;
- properties.

Triggers:

- object created/updated webhooks.

### 4F - Salesforce

Operations:

- SOQL query;
- get object;
- create object;
- update object;
- delete object;
- upsert object;
- bulk create/update;
- describe object.

Dynamic options:

- object types;
- fields;
- record types.

Triggers:

- platform event;
- change data capture where configured.

### 4G - Support Systems

Providers:

- Zendesk;
- Freshdesk;
- Intercom;
- Help Scout.

Common operations:

- ticket/conversation CRUD;
- add note/comment;
- assign owner/team;
- change status/priority;
- list users/agents;
- search contacts.

Common triggers:

- ticket created;
- ticket updated;
- conversation replied.

### Phase 4 Acceptance Criteria

- Workflows can route support/sales/project events across at least Jira,
  Linear, HubSpot, and Salesforce without custom HTTP nodes.
- Dynamic field/resource selection is available for high-friction providers.

## Phase 5 - Commerce, Billing, And Finance

Goal: support revenue, billing, order, and accounting automations.

### 5A - Shopify

Operations:

- list/get/create/update orders;
- list/get/update customers;
- list/get/create/update products;
- update inventory;
- create fulfillment;
- create discount code.

Dynamic options:

- locations;
- products;
- variants;
- fulfillment services.

Triggers:

- order created/paid/cancelled/fulfilled;
- customer created/updated;
- product updated.

### 5B - WooCommerce

Operations:

- orders CRUD;
- products CRUD;
- customers CRUD;
- coupons CRUD;
- refunds.

Triggers:

- order created/updated;
- product updated;
- customer created.

### 5C - PayPal

Operations:

- get/create payment;
- capture order;
- refund capture;
- list transactions;
- create invoice;
- send invoice;
- subscription operations.

Triggers:

- payment completed/failed;
- subscription created/updated/cancelled.

### 5D - Xero

Operations:

- contacts CRUD;
- invoices CRUD;
- payments;
- accounts;
- bank transactions;
- reports.

Dynamic options:

- tenants;
- accounts;
- tax rates;
- tracking categories.

### 5E - QuickBooks

Operations:

- customers CRUD;
- invoices CRUD;
- payments;
- vendors;
- bills;
- reports.

Dynamic options:

- companies;
- accounts;
- items;
- tax codes.

### Phase 5 Acceptance Criteria

- Stripe and Shopify are production-ready first.
- At least one accounting provider has tenant/account dynamic options and safe
  OAuth refresh before finance workflows are recommended.

## Phase 6 - Communication And Notifications

Goal: promote current communication wrappers to v2 and add inbound event
coverage where practical.

### Providers

- Discord;
- Telegram;
- Microsoft Teams incoming webhook and Graph-backed Teams;
- Twilio;
- SendGrid;
- Mailchimp;
- Pushover;
- SMTP.

### Common Operations

- send message/email/SMS;
- send rich message/card;
- upload attachment/file where supported;
- list templates/campaigns/lists;
- add/update subscriber;
- unsubscribe or suppress recipient where supported.

### Common Triggers

- inbound SMS;
- email delivered/bounced/opened/clicked;
- Discord/Telegram command/message where bot setup supports it.

### Acceptance Criteria

- Existing communication wrappers have v2 replacements or documented reasons
  to remain simple nodes.
- Provider errors are structured and redacted.
- High-volume event providers have dedupe keys.

## Phase 7 - Data, Storage, And DevOps

Goal: make operational data movement and infrastructure automation production
safe.

### 7A - SQL And Data Stores

Providers:

- Postgres;
- MySQL;
- MongoDB;
- Redis;
- Elasticsearch/OpenSearch;
- Snowflake;
- BigQuery.

Operations:

- query/read;
- execute write statement;
- insert/update/delete records;
- bulk load/export where supported;
- schema/table/collection metadata;
- test connection.

Production notes:

- Use DatasetRef for large tabular outputs.
- Add optional read-only mode for credentials and operations.
- Avoid logging SQL parameters or connection strings.

### 7B - Object Storage

Providers:

- S3 and S3-compatible storage;
- Google Cloud Storage;
- Azure Blob Storage;
- SFTP.

Operations:

- upload artifact;
- download to artifact;
- list objects;
- copy/move/delete;
- signed URL where supported;
- object metadata/head.

Production notes:

- Large object payloads should flow through artifact refs, not inline JSON.

### 7C - AWS And Cloud Services

Providers/services:

- Lambda;
- SQS;
- SNS;
- DynamoDB;
- EventBridge;
- CloudWatch Logs.

Operations:

- invoke Lambda;
- send/receive/delete SQS messages;
- publish SNS message;
- DynamoDB get/put/update/query;
- put EventBridge event;
- fetch log events.

### 7D - DevOps Platforms

Providers:

- GitLab;
- Bitbucket;
- Jenkins;
- Cloudflare;
- Sentry;
- Datadog;
- PagerDuty.

Operations:

- create/list issues/incidents;
- trigger pipeline/job;
- read deployment/status;
- purge cache;
- create incident/alert;
- acknowledge/resolve incident.

Triggers:

- pipeline/job finished;
- incident triggered/resolved;
- deployment created;
- alert received.

### Phase 7 Acceptance Criteria

- Data nodes preserve large outputs through DatasetRef or artifacts.
- Cloud and DevOps credentials have safe test handlers.
- Destructive operations are clearly named and can be governed by unsafe-node
  policy where appropriate.

## Phase 8 - Security, Identity, And Admin Providers

Goal: support IT and security automation for production operators.

### Providers

- Okta;
- Microsoft Entra ID;
- Google Workspace Admin;
- Auth0;
- ServiceNow;
- Splunk;
- CrowdStrike or similar security event provider.

### Operations

- user lookup;
- create/update/disable user;
- group membership add/remove;
- service desk ticket CRUD;
- search events/logs;
- create security case;
- update incident status.

### Triggers

- user created/updated;
- group membership changed;
- security alert received;
- incident created/updated.

### Acceptance Criteria

- Destructive identity operations are marked as side-effecting and auditable.
- Scope requirements are explicit in credential metadata.
- No provider event stores raw sensitive payloads in subscription metadata.

## Phase 9 - Long-Tail Provider Expansion

Goal: scale provider breadth without reducing quality.

### Deliverables

- Provider generator or scaffolding command for:
  - provider package;
  - credential type stub;
  - operation specs;
  - dynamic option loader stubs;
  - mocked transport tests;
  - coverage matrix entry.
- Provider review checklist enforced by CI or a docs/test convention.
- Long-tail provider priority scoring:
  - customer demand;
  - workflow value;
  - API stability;
  - auth complexity;
  - trigger support;
  - implementation effort;
  - maintenance risk.

### Candidate Providers

- Monday.com;
- ClickUp;
- Smartsheet;
- Typeform;
- Webflow;
- DocuSign;
- Dropbox;
- Box;
- Miro;
- Figma;
- Harvest;
- Toggl;
- BambooHR;
- Greenhouse;
- Lever;
- Segment;
- Mixpanel;
- Amplitude.

### Acceptance Criteria

- New providers can be added repeatably without bespoke framework work.
- Long-tail providers still meet the production definition before being marked
  shipped.

## Phase 10 - Templates, AI Builder, And Migration

Goal: make the integration catalog usable after nodes ship.

### Deliverables

- Update workflow templates to prefer v2 provider nodes.
- Update AI workflow builder examples and provider recommendations.
- Add migration notes from legacy wrappers to v2 replacements.
- Add examples for:
  - webhook to Slack/Teams alert;
  - form submission to Google Sheets/Airtable;
  - Stripe payment to CRM update;
  - GitHub issue to Jira/Linear sync;
  - email attachment to Drive/S3;
  - database query to report artifact;
  - support ticket to incident notification.
- Add docs for credential setup per high-value provider pack.

### Acceptance Criteria

- Users can discover the right production node from templates and palette
  recommendations.
- Legacy nodes no longer appear in new starter workflows once v2 replacements
  exist.

## Phase 11 - Operational Hardening

Goal: make integration-heavy production deployments observable and controllable.

### Deliverables

- Provider-level request metrics:
  - count;
  - latency;
  - error rate;
  - retry count;
  - rate-limit count.
- Provider health dashboard or ops endpoint.
- Circuit breaker design for providers with persistent failures.
- Per-provider concurrency/rate-limit configuration.
- Run timeline filters for provider events.
- Audit events for credential use by provider and node id, without secrets.
- Optional live integration test harness gated by environment variables.

### Acceptance Criteria

- Operators can distinguish provider outage, credential failure, rate limiting,
  and workflow logic errors.
- Provider incidents can be mitigated without disabling the whole workflow
  engine.

## Suggested Implementation Order

1. Tool-mode safety audit for current v2 providers and high-risk legacy
   wrappers.
2. Phase 0 foundation cleanup.
3. Phase 1D Slack and 1E Stripe because they have shallow v2 coverage and high
   workflow value.
4. Phase 1C GitHub because the trigger framework already has a GitHub path.
5. Phase 1A Google Sheets and 1B Outlook because they exercise OAuth,
   pagination, dynamic options, and expiring subscriptions.
6. Phase 2 provider trigger renewal/recovery.
7. Phase 3 Google Drive, Gmail, OneDrive, and Teams.
8. Phase 4 Jira, Linear, HubSpot, and Salesforce.
9. Phase 5 Shopify, PayPal, Xero, and QuickBooks.
10. Phase 6 communication wrapper migrations.
11. Phase 7 data/storage/devops migrations.
12. Phase 8 security/admin providers.
13. Phase 9 long-tail provider generator and expansion.
14. Phase 10 templates and migration cleanup.
15. Phase 11 operational hardening.

## Implemented Slice - Slack V2 Depth

The first tool-mode-driven provider-depth slice adds:

- `slack_list_channels_v2` - read-only, useful for fixed channel discovery and
  AI-assisted routing.
- `slack_list_users_v2` - read-only, useful for user lookup and mention
  workflows.
- `slack_update_message_v2` - side-effecting, updates bot-owned messages.
- `slack_delete_message_v2` - side-effecting, deletes bot-owned messages.
- `slack_add_reaction_v2` - side-effecting, reacts to a message.

These nodes are generated v2 operation nodes, so they are usable as AI tools by
default. The two list operations are non-side-effecting; update/delete/reaction
operations remain side-effecting and should use the approval gate when called by
an agent.

Follow-up operation nodes added in the same Phase 1 operation pass:

- `slack_reply_in_thread_v2` - side-effecting, posts a threaded reply with an
  explicit `thread_ts`.
- `slack_open_direct_message_v2` - side-effecting, opens or resumes a direct
  message conversation.

Deferred Slack work:

- file upload, after deciding the artifact/file upload contract;
- signed Slack Events trigger;
- slash command trigger;
- richer dynamic option loaders for channel/user pickers.

## Implemented Slice - Production Core V2 Provider Depth

The next provider-depth slice adds production-core operations across the
existing v2 providers, with node-as-tool metadata verified for every new node.
All read/list/get/search/lookup operations are non-side-effecting tools; writes,
sends, deletes, refunds, payment creation, workflow dispatch, and external
state changes remain side-effecting tools.

Stripe nodes added:

- `stripe_get_customer_v2`;
- `stripe_list_customers_v2`;
- `stripe_update_customer_v2`;
- `stripe_create_checkout_session_v2`;
- `stripe_create_payment_intent_v2`;
- `stripe_get_payment_intent_v2`;
- `stripe_list_payment_intents_v2`;
- `stripe_create_refund_v2`;
- `stripe_list_products_v2`;
- `stripe_list_prices_v2`;
- `stripe_create_invoice_v2`;
- `stripe_list_invoices_v2`;
- `stripe_create_subscription_v2`;
- `stripe_list_subscriptions_v2`;
- `stripe_cancel_subscription_v2`.

GitHub nodes added:

- `github_list_issues_v2`;
- `github_get_issue_v2`;
- `github_update_issue_v2`;
- `github_create_issue_comment_v2`;
- `github_list_issue_comments_v2`;
- `github_list_pull_requests_v2`;
- `github_get_pull_request_v2`;
- `github_create_pull_request_v2`;
- `github_merge_pull_request_v2`;
- `github_get_file_contents_v2`;
- `github_put_file_contents_v2`;
- `github_workflow_dispatch_v2`;
- `github_create_release_v2`.

Google Sheets nodes added:

- `google_sheets_create_spreadsheet_v2`;
- `google_sheets_batch_update_values_v2`;
- `google_sheets_add_sheet_v2`;
- `google_sheets_rename_sheet_v2`;
- `google_sheets_delete_sheet_v2`;
- `google_sheets_lookup_rows_v2`;
- `google_sheets_upsert_row_v2`.

Microsoft Outlook nodes added:

- `outlook_create_draft_v2`;
- `outlook_reply_message_v2`;
- `outlook_forward_message_v2`;
- `outlook_send_draft_v2`;
- `outlook_update_message_v2`;
- `outlook_delete_message_v2`;
- `outlook_list_message_attachments_v2`;
- `outlook_get_message_attachment_v2`;
- `outlook_create_calendar_event_v2`;
- `outlook_update_calendar_event_v2`;
- `outlook_delete_calendar_event_v2`.

Airtable nodes added:

- `airtable_get_record_v2`;
- `airtable_update_record_v2`;
- `airtable_delete_record_v2`;
- `airtable_batch_create_records_v2`;
- `airtable_batch_update_records_v2`;
- `airtable_upsert_records_v2`.

Notion nodes added:

- `notion_get_page_v2`;
- `notion_update_page_v2`;
- `notion_archive_page_v2`;
- `notion_query_database_v2`;
- `notion_get_database_v2`;
- `notion_search_v2`;
- `notion_list_block_children_v2`;
- `notion_append_block_children_v2`.

Deferred production-hardening work after this slice:

- Stripe idempotency-key parameters for create/refund/payment operations;
- Stripe signed webhook triggers;
- Outlook artifact-backed attachment downloads and Graph subscription renewal;
- Google watch-channel triggers and broader spreadsheet dynamic options;
- Airtable bases/tables/views/fields option loaders;
- Notion database/page/property option loaders and practical property helpers;
- provider-specific examples showing which fields should be fixed versus
  supplied by `$fromAI(...)`.

## First Three Milestones

### Milestone A - Catalog Integrity

Scope:

- Phase 0 credential catalog cleanup;
- provider-pack checklist;
- coverage matrix expansion;
- tests for unknown credential references.

Exit criteria:

- All official node credential references resolve to backend credential types.
- CI catches missing credential type/test handler drift.

### Milestone B - Existing Provider Depth

Scope:

- Slack v2 depth;
- Stripe v2 depth;
- GitHub v2 depth;
- Google Sheets and Outlook v2 depth;
- Airtable and Notion v2 depth;
- Airtable and Notion dynamic options.

Exit criteria:

- Slack, Stripe, GitHub, Google Sheets, Outlook, Airtable, and Notion support
  common production workflows without legacy wrappers for the covered
  operations.
- Existing templates use v2 nodes only for those providers.

### Milestone C - Subscription-Trigger Reliability

Scope:

- Slack signed event trigger;
- Stripe signed webhook trigger;
- Microsoft Graph subscription renewal;
- Google watch-channel renewal;
- trigger recovery/status tests.

Exit criteria:

- Provider triggers can activate, renew, fail visibly, recover, and deactivate
  with mocked provider tests.

## Tracking

Track progress in:

- `docs/provider-coverage-matrix.md` for provider status;
- this plan for phase-level scope and sequencing;
- `docs/production-automation-platform-plan.md` only for architecture-level
  work packages and historical implementation notes.
