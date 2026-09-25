# Nodyra is in public beta: self-hosted Python workflows with a visual editor, MCP, and Docker workers

Hey everyone — I’m the developer of **Nodyra**, and it’s now in **public beta**.

Nodyra is a self-hosted workflow automation platform where **the nodes are real Python**. You can build on a visual canvas, write your own nodes, or connect an MCP-capable AI agent to create and edit workflows for you. The resulting graph stays visible and editable, with node inputs, outputs, logs, and errors available to inspect.

For example: ask an agent to fetch records from Postgres, transform them with pandas, and send a summary to Slack. Inspect the generated workflow, choose its Python environment, test it, and publish a version to run on a schedule.

**GitHub, setup instructions, and docs:** https://github.com/Harshit-repo/Nodyra

There’s quite a bit in the beta already:

**Building workflows and working with AI**

- **Visual workflow editor:** connect nodes, configure parameters, use expressions, and inspect data as it moves through the graph.
- **Python nodes:** use ordinary Python functions and packages, including your own internal libraries. Upload custom node modules and reuse helper code through the Code Library.
- **Missing a node? Build it:** write a custom node in Python yourself, or ask an LLM connected over MCP to create one for you. Inspect and edit the generated code, then reuse the node in your workflows.
- **MCP server:** let tools such as Claude Code or other compatible MCP clients create, edit, validate, run, and publish workflows, with scoped API tokens.
- **MCP tools in both directions:** expose published workflows as tools for external agents, or connect external MCP servers and use their tools inside workflows.
- **AI and RAG building blocks:** agents, tool calling, LLM chains, memory, embeddings, document loaders, text splitters, vector stores, retrievers, structured outputs, and guardrails. Provider support includes OpenAI-compatible endpoints, Azure OpenAI, and Anthropic.
- **Reusable workflows:** starter templates, branching, sub-workflows, import/export, and GitHub sync.

**Python environments, workers, and sandboxing**

- **Environment creation:** create separate Python environments, choose an interpreter version, install dependencies, import requirements, and bind workflows to the environment they need. Backends include venv, conda, and pixi.
- **Worker pools:** warm local subprocess workers, configurable pool sizes, and fixed, elastic, or fresh-process execution options.
- **Remote runner pools:** send execution to other machines instead of running everything alongside the web/API service. Bind environments to the appropriate pool.
- **Docker workers and Kubernetes runners:** run workloads on container infrastructure, with runner capacity, heartbeats, and drain controls.
- **Docker sandboxing:** container-based workflow execution with non-root users, read-only root filesystems, dropped capabilities, and CPU, memory, and process limits. Supports gVisor or Kata where installed and configured.
- **Durable execution queue:** leases, heartbeats, retries, timeouts, dead-letter handling, and graceful worker shutdown. Retry from a failed node is also available in beta.

**Triggers, integrations, and data**

- **Scheduled and event-driven runs:** cron schedules with timezones, intervals, webhooks, manual runs, and error workflows.
- **Versioned deployments:** publish an immutable workflow version and pin scheduled deployments to it.
- **Chat workflows:** test conversations in the editor and share a chat page from your own instance, with login-required or secret-link access.
- **API endpoints:** define HTTP methods and routes that trigger different branches of a workflow.
- **Integrations:** HTTP, databases, files, cloud storage, messaging, and business apps, including Postgres, Slack, GitHub, Google Sheets, Notion, Stripe, Airtable, and Outlook.
- **Data processing:** typed data serialization, artifact-backed datasets, DuckDB transformations, and local or S3-compatible artifact storage.
- **Debugging:** per-node inputs and outputs, logs, timing, errors, and run history.
- **CLI and Python client:** manage and run workflows programmatically. Export options include Python scripts, code-first modules, and Docker bundles.

**Self-hosting and team features**

Docker Compose is the starting point, with Helm/Kubernetes deployment options too. The platform includes local authentication, role-based access, encrypted credentials, credential connection tests, and unsafe-node policies.

The Community edition is free for permitted personal and internal business use, with resource limits. Nodyra is **source-available / fair-code under the Sustainable Use License**, rather than an OSI-approved open-source license. Paid tiers raise limits and add capabilities such as OpenTelemetry observability; Enterprise features include SSO/SAML/OIDC, multi-tenancy, audit export, and external key management. License verification works offline.

This is a **public beta**, so I’m looking for people willing to try it, report bugs, and tell me where the workflow feels awkward. There’s no hosted Nodyra service yet. Also, workflows execute arbitrary Python: the default local execution mode assumes trusted authors, and sandboxing must be configured explicitly.

If you try it, I’d particularly like feedback on setup, MCP-driven workflow creation, environment/dependency management, and Docker or remote-worker execution.

**What would you build with it, and what would stop you from using it?**
