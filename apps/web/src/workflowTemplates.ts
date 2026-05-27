import type { WorkflowGraph } from "./types";

export interface WorkflowTemplate {
  id: string;
  name: string;
  description: string;
  graph?: () => WorkflowGraph;
}

function node(
  id: string,
  type: string,
  params: Record<string, unknown>,
  x: number,
  y: number,
) {
  return {
    id,
    type,
    params,
    position: { x, y },
    disabled: false,
    outputs_override: null,
    on_error: "stop",
    retry_on_fail: false,
    retries: 1,
    retry_wait_seconds: 0,
    retry_backoff: false,
    always_output_data: false,
    timeout_seconds: null,
  };
}

export const CANVAS_STARTERS: WorkflowTemplate[] = [
  {
    id: "manual-http",
    name: "Manual + HTTP",
    description: "Trigger a request and inspect the API response.",
    graph: () => ({
      nodes: [
        node("manual", "manual_trigger", { data: {} }, 0, 40),
        node(
          "fetch",
          "http_request",
          {
            url: "https://jsonplaceholder.typicode.com/users",
            method: "GET",
            headers: {},
            query: {},
            body: {},
          },
          300,
          40,
        ),
      ],
      edges: [
        {
          id: "e1",
          source: "manual",
          source_output: "main",
          target: "fetch",
          target_input: "input",
        },
      ],
    }),
  },
  {
    id: "webhook-code",
    name: "Webhook + Code",
    description: "Capture an event and normalize it in Python.",
    graph: () => ({
      nodes: [
        node(
          "hook",
          "webhook_trigger",
          { path: "incoming-event", response_mode: "On Received" },
          0,
          40,
        ),
        node(
          "code",
          "code",
          {
            code:
              "event = input or {}\n" +
              "output = {\n" +
              "    'received': True,\n" +
              "    'event': event,\n" +
              "}",
          },
          300,
          40,
        ),
      ],
      edges: [
        {
          id: "e1",
          source: "hook",
          source_output: "main",
          target: "code",
          target_input: "input",
        },
      ],
    }),
  },
  {
    id: "schedule-api",
    name: "Schedule + API",
    description: "Run on a timer, fetch data, and keep a small sample.",
    graph: () => ({
      nodes: [
        node(
          "schedule",
          "schedule_trigger",
          { every: 1, interval: "hours", cron: "", timezone: "UTC" },
          0,
          40,
        ),
        node(
          "fetch",
          "http_request",
          {
            url: "https://jsonplaceholder.typicode.com/posts",
            method: "GET",
            headers: {},
            query: {},
            body: {},
          },
          300,
          40,
        ),
        node("limit", "limit", { max_items: 5, keep: "first" }, 600, 40),
      ],
      edges: [
        {
          id: "e1",
          source: "schedule",
          source_output: "main",
          target: "fetch",
          target_input: "input",
        },
        {
          id: "e2",
          source: "fetch",
          source_output: "main",
          target: "limit",
          target_input: "input",
        },
      ],
    }),
  },
];

export const WORKFLOW_TEMPLATES: WorkflowTemplate[] = [
  {
    id: "blank",
    name: "Blank workflow",
    description: "Start with an empty canvas.",
  },
  {
    id: "api-code",
    name: "API fetch + Python",
    description: "Fetch JSON, transform it in Code, inspect output.",
    graph: () => ({
      nodes: [
        node("manual", "manual_trigger", { data: {} }, 0, 40),
        node(
          "fetch",
          "http_request",
          {
            url: "https://jsonplaceholder.typicode.com/users",
            method: "GET",
            headers: {},
            query: {},
            body: {},
          },
          260,
          40,
        ),
        node(
          "code",
          "code",
          {
            code:
              "rows = input or []\n" +
              "output = {\n" +
              "    'row_count': len(rows),\n" +
              "    'emails': [row.get('email') for row in rows],\n" +
              "    'records': rows,\n" +
              "}",
          },
          520,
          40,
        ),
      ],
      edges: [
        {
          id: "e1",
          source: "manual",
          source_output: "main",
          target: "fetch",
          target_input: "input",
        },
        {
          id: "e2",
          source: "fetch",
          source_output: "main",
          target: "code",
          target_input: "input",
        },
      ],
    }),
  },
  {
    id: "webhook-slack",
    name: "Webhook + Slack",
    description: "Capture a webhook and send a Slack notification.",
    graph: () => ({
      nodes: [
        node(
          "hook",
          "webhook_trigger",
          { path: "incoming-event", response_mode: "On Received" },
          0,
          40,
        ),
        node(
          "slack",
          "slack_send_message",
          {
            bot_token: "",
            channel: "",
            text: "New webhook event: {{ $json }}",
            blocks: null,
            thread_ts: "",
          },
          300,
          40,
        ),
      ],
      edges: [
        {
          id: "e1",
          source: "hook",
          source_output: "main",
          target: "slack",
          target_input: "input",
        },
      ],
    }),
  },
  {
    id: "schedule-http",
    name: "Schedule + HTTP transform",
    description: "Run on a schedule, call an API, and normalize fields.",
    graph: () => ({
      nodes: [
        node(
          "schedule",
          "schedule_trigger",
          { every: 1, interval: "hours", cron: "", timezone: "UTC" },
          0,
          40,
        ),
        node(
          "fetch",
          "http_request",
          {
            url: "https://jsonplaceholder.typicode.com/posts",
            method: "GET",
            headers: {},
            query: {},
            body: {},
          },
          280,
          40,
        ),
        node("limit", "limit", { max_items: 5, keep: "first" }, 560, 40),
      ],
      edges: [
        {
          id: "e1",
          source: "schedule",
          source_output: "main",
          target: "fetch",
          target_input: "input",
        },
        {
          id: "e2",
          source: "fetch",
          source_output: "main",
          target: "limit",
          target_input: "input",
        },
      ],
    }),
  },
];
