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
  {
    id: "datasetref-api-sql",
    name: "DatasetRef + SQL",
    description: "Turn API rows into a DatasetRef and query them with DuckDB.",
    graph: () => ({
      nodes: [
        node("manual", "manual_trigger", { data: {} }, 0, 80),
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
          80,
        ),
        node("dataset", "records_to_dataset", {}, 520, 80),
        node("sql", "duckdb_sql", { sql: "SELECT id, name, email FROM input ORDER BY id" }, 780, 80),
        node("preview", "dataset_preview", { limit: 25 }, 1040, 80),
      ],
      edges: [
        { id: "e1", source: "manual", source_output: "main", target: "fetch", target_input: "input" },
        { id: "e2", source: "fetch", source_output: "main", target: "dataset", target_input: "input" },
        { id: "e3", source: "dataset", source_output: "main", target: "sql", target_input: "input" },
        { id: "e4", source: "sql", source_output: "main", target: "preview", target_input: "input" },
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
          "slack_send_message_v2",
          {
            credentials: "",
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
  {
    id: "datasetref-api-sql",
    name: "DatasetRef — API to SQL",
    description: "Fetch rows, promote them to a DatasetRef, query with DuckDB SQL, then preview the result.",
    graph: () => ({
      nodes: [
        node("manual", "manual_trigger", { data: {} }, 0, 80),
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
          80,
        ),
        node("dataset", "records_to_dataset", {}, 520, 80),
        node(
          "sql",
          "duckdb_sql",
          { sql: "SELECT id, name, email, company.name AS company FROM input ORDER BY id" },
          780,
          80,
        ),
        node("preview", "dataset_preview", { limit: 25 }, 1040, 80),
      ],
      edges: [
        { id: "e1", source: "manual", source_output: "main", target: "fetch", target_input: "input" },
        { id: "e2", source: "fetch", source_output: "main", target: "dataset", target_input: "input" },
        { id: "e3", source: "dataset", source_output: "main", target: "sql", target_input: "input" },
        { id: "e4", source: "sql", source_output: "main", target: "preview", target_input: "input" },
      ],
    }),
  },
  {
    id: "datasetref-filter-export",
    name: "DatasetRef — filter + CSV",
    description: "Keep table data artifact-backed, filter/limit it, then export a CSV only at the end.",
    graph: () => ({
      nodes: [
        node("manual", "manual_trigger", { data: {} }, 0, 120),
        node(
          "sample",
          "code",
          {
            code:
              "rows = [\n" +
              "    {'id': 1, 'region': 'west', 'amount': 120},\n" +
              "    {'id': 2, 'region': 'east', 'amount': 80},\n" +
              "    {'id': 3, 'region': 'west', 'amount': 240},\n" +
              "    {'id': 4, 'region': 'north', 'amount': 45},\n" +
              "]\n" +
              "output = rows",
          },
          260,
          120,
        ),
        node("dataset", "records_to_dataset", {}, 520, 120),
        node("filter", "dataset_filter", { where: "amount >= 100" }, 780, 60),
        node("limit", "dataset_limit", { limit: 100, offset: 0 }, 1040, 60),
        node("csv", "csv_write", { filename: "filtered-sales.csv", delimiter: ",", include_header: true }, 1300, 60),
        node("records", "dataset_to_records", { max_rows: 100, allow_truncate: false }, 1040, 200),
      ],
      edges: [
        { id: "e1", source: "manual", source_output: "main", target: "sample", target_input: "input" },
        { id: "e2", source: "sample", source_output: "main", target: "dataset", target_input: "input" },
        { id: "e3", source: "dataset", source_output: "main", target: "filter", target_input: "input" },
        { id: "e4", source: "filter", source_output: "main", target: "limit", target_input: "input" },
        { id: "e5", source: "limit", source_output: "main", target: "csv", target_input: "input" },
        { id: "e6", source: "limit", source_output: "main", target: "records", target_input: "input" },
      ],
    }),
  },
  {
    id: "ml-classification",
    name: "ML — Classification",
    description:
      "Generate labeled data, train a classifier, save the model, and score predictions. Assign the \"ML / Data Science\" environment to run.",
    graph: () => ({
      nodes: [
        node("trigger", "manual_trigger", { data: {} }, 0, 160),
        node(
          "generate",
          "code",
          {
            code:
              "import random\n" +
              "rng = random.Random(42)\n" +
              "rows = []\n" +
              "for _ in range(240):\n" +
              "    if rng.random() < 0.5:\n" +
              "        rows.append({\n" +
              "            'petal_length': round(rng.gauss(1.5, 0.25), 3),\n" +
              "            'petal_width': round(rng.gauss(0.3, 0.1), 3),\n" +
              "            'species': 'setosa',\n" +
              "        })\n" +
              "    else:\n" +
              "        rows.append({\n" +
              "            'petal_length': round(rng.gauss(4.8, 0.4), 3),\n" +
              "            'petal_width': round(rng.gauss(1.6, 0.25), 3),\n" +
              "            'species': 'versicolor',\n" +
              "        })\n" +
              "output = rows",
          },
          260,
          160,
        ),
        node("dataset", "records_to_dataset", {}, 520, 160),
        node(
          "train",
          "train_classifier",
          {
            target_column: "species",
            feature_columns: "",
            algorithm: "random_forest",
            test_size: 0.25,
            scale: true,
            random_state: 42,
          },
          780,
          80,
        ),
        node("save", "save_model", { name: "iris-species" }, 1040, 80),
        node("register", "register_model", { name: "iris-species" }, 1300, 80),
        node(
          "predict",
          "ml_predict",
          { output_column: "prediction", include_proba: true },
          1040,
          260,
        ),
      ],
      edges: [
        {
          id: "e1",
          source: "trigger",
          source_output: "main",
          target: "generate",
          target_input: "input",
        },
        {
          id: "e2",
          source: "generate",
          source_output: "main",
          target: "dataset",
          target_input: "input",
        },
        {
          id: "e3",
          source: "dataset",
          source_output: "main",
          target: "train",
          target_input: "input",
        },
        {
          id: "e4",
          source: "train",
          source_output: "model",
          target: "save",
          target_input: "model",
        },
        {
          id: "e5",
          source: "train",
          source_output: "model",
          target: "predict",
          target_input: "model",
        },
        {
          id: "e6",
          source: "dataset",
          source_output: "main",
          target: "predict",
          target_input: "data",
        },
        {
          id: "e7",
          source: "train",
          source_output: "model",
          target: "register",
          target_input: "model",
        },
      ],
    }),
  },
  {
    id: "ml-regression",
    name: "ML — Regression",
    description:
      "Generate numeric data, rank features, fit a linear regressor, and save the model. Assign the \"ML / Data Science\" environment to run.",
    graph: () => ({
      nodes: [
        node("trigger", "manual_trigger", { data: {} }, 0, 160),
        node(
          "generate",
          "code",
          {
            code:
              "import random\n" +
              "rng = random.Random(7)\n" +
              "rows = []\n" +
              "for _ in range(240):\n" +
              "    size = round(rng.uniform(50, 250), 1)\n" +
              "    bedrooms = rng.randint(1, 5)\n" +
              "    age = round(rng.uniform(0, 40), 1)\n" +
              "    price = 50000 + 3000 * size + 25000 * bedrooms - 1200 * age\n" +
              "    price += rng.gauss(0, 8000)\n" +
              "    rows.append({\n" +
              "        'size_sqm': size,\n" +
              "        'bedrooms': bedrooms,\n" +
              "        'age_years': age,\n" +
              "        'price': round(price, 2),\n" +
              "    })\n" +
              "output = rows",
          },
          260,
          160,
        ),
        node("dataset", "records_to_dataset", {}, 520, 160),
        node(
          "select",
          "select_features",
          { target_column: "price", k: 2, task: "regression" },
          780,
          260,
        ),
        node(
          "train",
          "train_regressor",
          {
            target_column: "price",
            feature_columns: "",
            algorithm: "linear_regression",
            test_size: 0.25,
            scale: false,
            random_state: 7,
          },
          780,
          80,
        ),
        node("save", "save_model", { name: "house-prices" }, 1040, 80),
      ],
      edges: [
        {
          id: "e1",
          source: "trigger",
          source_output: "main",
          target: "generate",
          target_input: "input",
        },
        {
          id: "e2",
          source: "generate",
          source_output: "main",
          target: "dataset",
          target_input: "input",
        },
        {
          id: "e3",
          source: "dataset",
          source_output: "main",
          target: "train",
          target_input: "input",
        },
        {
          id: "e4",
          source: "dataset",
          source_output: "main",
          target: "select",
          target_input: "input",
        },
        {
          id: "e5",
          source: "train",
          source_output: "model",
          target: "save",
          target_input: "model",
        },
      ],
    }),
  },
  {
    id: "webhook-predict",
    name: "ML — Webhook Prediction API",
    description:
      "Expose a webhook that loads a registered model and returns a live prediction. Run the \"ML — Classification\" template first to register the \"iris-species\" model. Assign the \"ML / Data Science\" environment.",
    graph: () => ({
      nodes: [
        node(
          "hook",
          "webhook_trigger",
          {
            http_method: "POST",
            path: "predict",
            response_mode: "Last Node",
            response_code: 200,
            auth_type: "none",
            auth_credentials: {},
          },
          0,
          160,
        ),
        node(
          "extract",
          "code",
          {
            code:
              "# Build a single-row dataset from the webhook JSON body.\n" +
              "body = (input or {}).get('body') or input or {}\n" +
              "row = {\n" +
              "    'petal_length': float(body.get('petal_length', 0) or 0),\n" +
              "    'petal_width': float(body.get('petal_width', 0) or 0),\n" +
              "}\n" +
              "output = [row]",
          },
          260,
          160,
        ),
        node("dataset", "records_to_dataset", {}, 520, 160),
        node("load", "load_model", { name: "iris-species" }, 520, 320),
        node(
          "predict",
          "ml_predict",
          { output_column: "prediction", include_proba: true },
          780,
          160,
        ),
        node("rows", "dataset_to_records", { max_rows: 1 }, 1040, 160),
        node(
          "shape",
          "code",
          {
            code:
              "rows = input or []\n" +
              "first = rows[0] if rows else {}\n" +
              "output = {\n" +
              "    'prediction': first.get('prediction'),\n" +
              "    'inputs': {k: v for k, v in first.items() if k not in ('prediction',)},\n" +
              "}",
          },
          1300,
          160,
        ),
        node(
          "respond",
          "respond_to_webhook",
          { status_code: 200, headers: {}, body_field: "" },
          1560,
          160,
        ),
      ],
      edges: [
        { id: "e1", source: "hook", source_output: "main", target: "extract", target_input: "input" },
        { id: "e2", source: "extract", source_output: "main", target: "dataset", target_input: "input" },
        { id: "e3", source: "dataset", source_output: "main", target: "predict", target_input: "data" },
        { id: "e4", source: "load", source_output: "model", target: "predict", target_input: "model" },
        { id: "e5", source: "predict", source_output: "main", target: "rows", target_input: "input" },
        { id: "e6", source: "rows", source_output: "main", target: "shape", target_input: "input" },
        { id: "e7", source: "shape", source_output: "main", target: "respond", target_input: "input" },
      ],
    }),
  },
  {
    id: "chart-dashboard",
    name: "Charts — Quick Dashboard",
    description:
      "Generate sample sales data and render an interactive bar chart with hover tooltips. Open the Chart node's data viewer to explore it.",
    graph: () => ({
      nodes: [
        node("trigger", "manual_trigger", { data: {} }, 0, 160),
        node(
          "generate",
          "code",
          {
            code:
              "months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun']\n" +
              "import random\n" +
              "rng = random.Random(3)\n" +
              "rows = []\n" +
              "for m in months:\n" +
              "    revenue = rng.randint(80, 200)\n" +
              "    rows.append({'month': m, 'revenue': revenue, 'cost': round(revenue * rng.uniform(0.5, 0.8))})\n" +
              "output = rows",
          },
          260,
          160,
        ),
        node("dataset", "records_to_dataset", {}, 520, 160),
        node(
          "chart",
          "chart",
          { chart_type: "bar", x: "month", y: "revenue, cost", title: "Revenue vs cost" },
          780,
          160,
        ),
      ],
      edges: [
        { id: "e1", source: "trigger", source_output: "main", target: "generate", target_input: "input" },
        { id: "e2", source: "generate", source_output: "main", target: "dataset", target_input: "input" },
        { id: "e3", source: "dataset", source_output: "main", target: "chart", target_input: "input" },
      ],
    }),
  },
  {
    id: "model-report",
    name: "ML — Model Report",
    description:
      "Train a classifier, chart its feature importances, and assemble a drag-and-drop report with the chart, a metrics tile, and a data table. Assign the \"ML / Data Science\" environment.",
    graph: () => ({
      nodes: [
        node("trigger", "manual_trigger", { data: {} }, 0, 200),
        node(
          "generate",
          "code",
          {
            code:
              "import random\n" +
              "rng = random.Random(5)\n" +
              "rows = []\n" +
              "for _ in range(240):\n" +
              "    a = rng.uniform(0, 1)\n" +
              "    b = rng.uniform(0, 1)\n" +
              "    c = rng.uniform(0, 1)\n" +
              "    label = 'yes' if (0.7 * a + 0.2 * b + 0.1 * c) > 0.5 else 'no'\n" +
              "    rows.append({'a': round(a, 3), 'b': round(b, 3), 'c': round(c, 3), 'label': label})\n" +
              "output = rows",
          },
          260,
          200,
        ),
        node("dataset", "records_to_dataset", {}, 520, 200),
        node(
          "train",
          "train_classifier",
          {
            target_column: "label",
            feature_columns: "",
            algorithm: "random_forest",
            test_size: 0.25,
            scale: false,
            random_state: 5,
          },
          780,
          120,
        ),
        node(
          "imp_chart",
          "metrics_chart",
          { source: "feature_importances", title: "Feature importances" },
          1040,
          120,
        ),
        node("report", "build_report", { title: "Model report", columns: 12 }, 1300, 200),
      ],
      edges: [
        { id: "e1", source: "trigger", source_output: "main", target: "generate", target_input: "input" },
        { id: "e2", source: "generate", source_output: "main", target: "dataset", target_input: "input" },
        { id: "e3", source: "dataset", source_output: "main", target: "train", target_input: "input" },
        { id: "e4", source: "train", source_output: "metrics", target: "imp_chart", target_input: "input" },
        { id: "e5", source: "imp_chart", source_output: "main", target: "report", target_input: "tile1" },
        { id: "e6", source: "train", source_output: "metrics", target: "report", target_input: "tile2" },
        { id: "e7", source: "dataset", source_output: "main", target: "report", target_input: "tile3" },
      ],
    }),
  },
];
