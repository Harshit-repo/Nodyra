# Production AI/ML Nodes Plan

This plan describes how to add production-grade AI, ML, fine-tuning,
evaluation, RAG, model-serving, and model-monitoring nodes to Noodle without
turning the global node registry into a heavy ML import surface.

The strategy is to make Noodle stronger than generic automation platforms by
leaning into its Python-native runtime, environment package management,
DatasetRef tables, artifacts, durable runs, and runner pools. The goal is not
to add random AI wrappers; the goal is to make Noodle a visual ML operations
and AI application workflow system.

## Goals

- Add first-class LLM fine-tuning, evaluation, synthetic-data, RAG lifecycle,
  local model training, serving, and monitoring nodes.
- Preserve fast node registry loading even when heavy packages such as
  `torch`, `transformers`, `trl`, `peft`, `ragas`, `mlflow`, `onnxruntime`, or
  `vllm` are not installed.
- Use Noodle's existing `requirements` metadata so the editor can prompt users
  to install packages into the workflow environment.
- Keep large data and model payloads artifact-backed instead of inline JSON.
- Make long-running training workflows observable, cancellable, resumable where
  possible, and safe to run on remote/GPU runner pools.
- Provide production semantics: idempotency, retries, timeouts, status polling,
  audit events, retention, credential scoping, and clear failure modes.

## Non-Goals

- Do not import heavy optional packages at module import time.
- Do not make base Noodle installs include GPU or ML training packages.
- Do not make a single "do everything AI" node that hides data, training,
  evaluation, and deployment steps.
- Do not store full model weights, large eval rows, or raw documents inline in
  run outputs.
- Do not require untrusted multi-tenant sandboxing for v1. These nodes follow
  Noodle's current trusted workflow-author model.

## Existing Capabilities To Reuse

Noodle already has the architecture needed for the first implementation slice:

- Node manifests expose `requirements`.
- The editor computes missing packages and offers an environment install action.
- The API preflight blocks runs when required packages are absent.
- Nodes can run inside per-environment subprocess virtualenvs.
- Remote runner pools can route runs to machines with different capabilities.
- DatasetRef keeps tabular data artifact-backed.
- Artifacts can store JSONL files, reports, model files, checkpoints, and
  adapter folders.
- Run and node-run records capture status, output, logs, debug, timing, and
  artifacts.
- The durable queue and runner pools provide admission control and recovery
  hooks for production execution.

## Hard Invariants

Every new AI/ML node must follow these rules.

### 1. No Heavy Global Imports

Allowed at module scope:

- Standard library modules.
- Noodle SDK/core helpers.
- Lightweight typing helpers.

Not allowed at module scope:

- `openai`
- `tiktoken`
- `pandas`
- `numpy`
- `sklearn`
- `torch`
- `transformers`
- `datasets`
- `trl`
- `peft`
- `accelerate`
- `ragas`
- `deepeval`
- `mlflow`
- `wandb`
- `onnxruntime`
- `vllm`
- `llama_cpp`
- provider SDKs unless already lightweight and globally accepted elsewhere.

Use a lazy helper:

```python
def _require_openai():
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "This node requires the openai package. Add openai to the "
            "workflow environment, rebuild it, then run again."
        ) from exc
    return OpenAI
```

### 2. Declare Requirements On The Node

Every node that can need optional packages must declare them:

```python
@node(
    name="OpenAI Create Fine-Tune Job",
    id="openai_create_fine_tune_job",
    category="Machine Learning",
    requirements=["openai>=1.0"],
)
def openai_create_fine_tune_job(...):
    OpenAI = _require_openai()
```

If a package is optional for only one mode, split the node rather than hiding a
large optional dependency behind a parameter. For example, keep OpenAI
fine-tuning, Hugging Face SFT, and Unsloth SFT as separate nodes.

### 3. Return Small References

Large outputs must become references:

- Training JSONL -> artifact ref.
- Eval row-level results -> DatasetRef.
- Model checkpoints -> artifact refs.
- LoRA adapters -> artifact refs or remote path refs.
- Provider jobs -> FineTuneJobRef.
- Registered model -> ModelRegistryRef.

Inline outputs should contain status, ids, summary metrics, artifact refs, and
small previews only.

### 4. Prefer Job Nodes For Long Work

Long work should be split into:

```text
prepare -> submit/start -> poll/status -> collect artifacts -> evaluate -> gate
```

This is better than one blocking node for provider-side jobs. Local GPU training
can still be a blocking node in v1, but it must write checkpoints and logs as it
runs and must support workflow/node timeouts.

### 5. Credential Boundaries

Provider nodes must use existing credential patterns:

- OpenAI fine-tuning uses the LLM/OpenAI credential shape.
- Hugging Face Hub operations use a Hugging Face token credential.
- Weights & Biases and MLflow nodes use their own credentials.
- Cloud GPU/runner nodes should not expose host secrets in node outputs.

### 6. Security And Governance

Nodes that write to filesystem paths, launch servers, execute training scripts,
or publish model artifacts must be classified for unsafe-node policy review.
Production deployments should surface warnings for:

- shell/process execution;
- arbitrary filesystem paths;
- public model publishing;
- external data upload;
- provider-side fine-tuning using potentially sensitive training data.

## File Layout

Add focused modules instead of one giant AI file:

```text
packages/nodes/noodle_nodes/
  llm_training.py          # fine-tuning datasets, provider jobs, local SFT
  llm_evals.py             # model comparisons, judges, eval gates, RAG evals
  synthetic_data.py        # synthetic examples, labels, preference pairs
  rag_lifecycle.py         # chunk tuning, index diffs, retriever evals
  model_serving.py         # local serving, benchmark, endpoint checks
  model_monitoring.py      # drift, cost, quality, traces
```

Update:

- `packages/nodes/noodle_nodes/__init__.py`
- `packages/nodes/tests/test_llm_training.py`
- `packages/nodes/tests/test_llm_evals.py`
- `packages/nodes/tests/test_synthetic_data.py`
- `packages/nodes/tests/test_rag_lifecycle.py`
- `packages/nodes/tests/test_model_serving.py`
- `packages/nodes/tests/test_model_monitoring.py`

## Reference Envelopes

Use JSON-friendly envelopes with stable markers. These can live in helper
functions inside the relevant node module first, then move to core if multiple
packages need them.

### FineTuneDatasetRef

```json
{
  "__noodle_finetune_dataset__": true,
  "version": 1,
  "format": "openai_chat_jsonl",
  "artifact": {"__noodle_artifact__": true},
  "n_examples": 128,
  "token_estimate": 42000,
  "columns": ["messages"],
  "validation": {
    "errors": [],
    "warnings": []
  }
}
```

### FineTuneJobRef

```json
{
  "__noodle_finetune_job__": true,
  "version": 1,
  "provider": "openai",
  "job_id": "ftjob_...",
  "status": "running",
  "training_file_id": "file_...",
  "model": "gpt-4.1-mini",
  "fine_tuned_model": null,
  "created_at": "2026-06-05T00:00:00Z"
}
```

### ModelArtifactRef

```json
{
  "__noodle_model_artifact__": true,
  "version": 1,
  "kind": "lora_adapter",
  "base_model": "meta-llama/Llama-3.1-8B",
  "artifact": {"__noodle_artifact__": true},
  "metrics": {},
  "metadata": {}
}
```

### EvalResultRef

```json
{
  "__noodle_eval_result__": true,
  "version": 1,
  "summary": {
    "accuracy": 0.91,
    "win_rate": 0.62
  },
  "rows": {"__noodle_dataset__": true},
  "report": {"__noodle_artifact__": true}
}
```

## Phase 0 - Platform Checks

Objective: make sure the engine/platform paths are ready for these nodes.

Tasks:

1. Confirm all new nodes can declare `requirements` and show missing-package
   prompts in the editor.
2. Confirm API preflight blocks runs with missing packages.
3. Confirm artifacts can store JSONL and model-adapter files under the current
   storage backend.
4. Confirm DatasetRef can feed dataset-builder and eval nodes.
5. Confirm workflow-level and node-level timeouts are visible enough for long
   training runs.
6. Add a smoke test that importing `noodle_nodes` does not import any of:
   `openai`, `torch`, `transformers`, `trl`, `peft`, `ragas`, `mlflow`, `vllm`.

Acceptance:

- `import noodle_nodes` succeeds in a base environment with no optional ML
  packages.
- Missing package UI and preflight work for at least one new test node.
- A JSONL artifact written by a node can be read by a downstream node.

## Phase 1 - OpenAI Fine-Tuning MVP

This is the first production slice because it does not require GPU runner
hardening.

### Nodes

#### LLM Fine-Tune Dataset

- ID: `llm_fine_tune_dataset`
- Category: `Machine Learning`
- Requirements: `pandas>=2.0`, `tiktoken>=0.7`
- Inputs: DatasetRef or records
- Outputs: FineTuneDatasetRef
- Purpose: convert rows into provider-ready JSONL.
- Parameters:
  - `format`: `openai_chat_jsonl`, `prompt_completion_jsonl`
  - `messages_column`
  - `system_column`
  - `user_column`
  - `assistant_column`
  - `min_examples`
  - `validation_split`
  - `dedupe`
  - `max_tokens_per_example`
- Output:
  - JSONL artifact ref
  - row counts
  - validation warnings
  - token estimates

#### OpenAI Upload Fine-Tune File

- ID: `openai_upload_fine_tune_file`
- Requirements: `openai>=1.0`
- Inputs: FineTuneDatasetRef or artifact
- Outputs: file ref
- Purpose: upload training or validation file with `purpose="fine-tune"`.
- Production concerns:
  - idempotency key from artifact checksum;
  - avoid uploading the same artifact repeatedly when possible;
  - redact file contents from logs.

#### OpenAI Create Fine-Tune Job

- ID: `openai_create_fine_tune_job`
- Requirements: `openai>=1.0`
- Inputs: uploaded training file id, optional validation file id
- Outputs: FineTuneJobRef
- Parameters:
  - `model`
  - `suffix`
  - `method`: supervised first, DPO/RFT later if supported by the provider
  - `n_epochs`
  - `batch_size`
  - `learning_rate_multiplier`
  - `seed`
  - `metadata`
- Production concerns:
  - do not wait for completion in this node;
  - return job id immediately;
  - store provider job id in run output and audit metadata.

#### OpenAI Fine-Tune Status

- ID: `openai_fine_tune_status`
- Requirements: `openai>=1.0`
- Inputs: FineTuneJobRef or job id
- Outputs: updated FineTuneJobRef
- Parameters:
  - `include_events`
  - `include_result_files`
  - `fail_if_failed`
- Production concerns:
  - normalize provider statuses;
  - do not throw for normal `running` status;
  - throw only if `fail_if_failed` and provider job failed.

#### OpenAI Fine-Tune Checkpoints

- ID: `openai_fine_tune_checkpoints`
- Requirements: `openai>=1.0`
- Inputs: FineTuneJobRef or job id
- Outputs: checkpoint list
- Purpose: expose checkpoint model ids and metrics.

#### OpenAI Cancel Fine-Tune Job

- ID: `openai_cancel_fine_tune_job`
- Requirements: `openai>=1.0`
- Inputs: FineTuneJobRef or job id
- Outputs: cancelled job status
- Production concerns:
  - idempotent cancellation;
  - safe to retry.

#### Register Fine-Tuned Model

- ID: `register_fine_tuned_model`
- Requirements: none for provider id registration
- Inputs: FineTuneJobRef/checkpoint/model id
- Outputs: ModelRegistryRef
- Purpose: store model id, provider, base model, training dataset artifact,
  eval metrics, and owner metadata.

### Tests

- Registry imports without `openai`, `pandas`, or `tiktoken`.
- `llm_fine_tune_dataset` creates valid JSONL from records and DatasetRef.
- Missing message columns produce friendly errors.
- OpenAI client calls are fully mocked.
- Status node handles queued/running/succeeded/failed/cancelled.
- Cancel node is idempotent.

## Phase 2 - Evaluation And Release Gates

Objective: make fine-tuning measurable. A model should not be registered or
promoted without eval evidence.

### Nodes

#### LLM Eval Dataset

- ID: `llm_eval_dataset`
- Requirements: `pandas>=2.0`, optional `tiktoken>=0.7`
- Inputs: DatasetRef or records
- Outputs: DatasetRef plus summary
- Purpose: create holdout eval examples with prompts, expected outputs,
  metadata, and optional rubric.

#### LLM Compare Models

- ID: `llm_compare_models`
- Requirements: provider SDK, usually `openai>=1.0`
- Inputs: eval DatasetRef
- Outputs: EvalResultRef
- Parameters:
  - `baseline_model`
  - `candidate_model`
  - `prompt_column`
  - `expected_column`
  - `max_rows`
  - `concurrency`
  - `temperature`
- Output:
  - row-level DatasetRef with baseline/candidate/expected outputs;
  - summary metrics;
  - cost and token estimates.

#### LLM Judge

- ID: `llm_judge`
- Requirements: provider SDK
- Inputs: DatasetRef of model outputs
- Outputs: EvalResultRef
- Parameters:
  - `judge_model`
  - `rubric`
  - `score_scale`
  - `pairwise`
  - `require_reason`
  - `strict_json`
- Production concerns:
  - structured output schema;
  - judge prompt stored with eval result;
  - deterministic retry for parse failures.

#### Exact/Regex/JSON Eval

- ID: `llm_rule_eval`
- Requirements: none or `jsonschema>=4.21`
- Inputs: DatasetRef
- Outputs: EvalResultRef
- Purpose: cheap deterministic evals before LLM-as-judge.
- Modes:
  - exact match;
  - contains;
  - regex;
  - JSON schema;
  - JSON field equality;
  - numeric tolerance.

#### Eval Gate

- ID: `eval_gate`
- Requirements: none
- Inputs: EvalResultRef or metrics dict
- Outputs: `pass`, `fail`
- Purpose: branch/fail based on metric thresholds.
- Parameters:
  - `metric`
  - `operator`
  - `threshold`
  - `on_fail`: branch, error

#### Eval Report

- ID: `eval_report`
- Requirements: optional `jinja2`, `markdown`, `plotly`
- Inputs: EvalResultRef
- Outputs: report artifact
- Purpose: create Markdown/HTML/PDF-ready eval reports.

### Tests

- Rule evals are deterministic.
- Compare models uses mocked provider calls.
- Judge node validates structured judge responses.
- Eval gate branches correctly.
- Row-level output is DatasetRef, not inline records.

## Phase 3 - Synthetic Data And Dataset Quality

Objective: help users create, clean, label, and govern training/eval data.

### Nodes

#### Synthetic Examples Generate

- ID: `synthetic_examples_generate`
- Requirements: provider SDK
- Inputs: seed examples, schema, or instructions
- Outputs: DatasetRef
- Purpose: generate candidate training/eval examples.
- Production concerns:
  - dedupe;
  - diversity controls;
  - safety filters;
  - source tag = synthetic.

#### Preference Pair Generate

- ID: `preference_pair_generate`
- Requirements: provider SDK
- Outputs: DatasetRef suitable for DPO/preference tuning
- Purpose: generate chosen/rejected response pairs.

#### Weak Label

- ID: `weak_label`
- Requirements: optional `skrub`/`snorkel` later, provider SDK for LLM mode
- Outputs: DatasetRef with labels and confidence
- Modes:
  - rule-based;
  - LLM;
  - embedding similarity;
  - ensemble vote.

#### Dataset Deduplicate Semantic

- ID: `dataset_deduplicate_semantic`
- Requirements: `sentence-transformers`, `numpy`, optional `faiss-cpu`
- Outputs: DatasetRef plus duplicate clusters
- Purpose: remove near-duplicate training examples.

#### Dataset PII Scan

- ID: `dataset_pii_scan`
- Requirements: `presidio-analyzer` or provider moderation path
- Outputs: DatasetRef/report artifact
- Purpose: find sensitive fields before provider upload.

#### Dataset Decontaminate

- ID: `dataset_decontaminate`
- Requirements: `datasketch`, optional `rapidfuzz`
- Purpose: remove examples overlapping eval/test sets or forbidden corpora.

#### Token Profile

- ID: `token_profile`
- Requirements: `tiktoken`
- Outputs: metrics and histogram artifact
- Purpose: estimate token cost, max context pressure, and outlier rows.

## Phase 4 - Hugging Face And Local Fine-Tuning

Objective: support local/open-source fine-tuning on CPU/GPU runner pools.

This phase is higher risk than provider fine-tuning because it touches GPU
drivers, large dependencies, model licenses, disk usage, and memory pressure.

### Runner Requirements

- Define a `GPU / LLM Training` environment preset.
- Define runner-pool labels/capabilities:
  - `gpu=true`
  - `cuda_version`
  - `vram_gb`
  - `disk_gb`
  - `supports_docker`
- Add UI copy that local fine-tuning should be run on a GPU runner pool.
- Add recommended timeout presets for training workflows.

### Nodes

#### Hugging Face Dataset Export

- ID: `hf_dataset_export`
- Requirements: `datasets>=2.0`, `pandas>=2.0`
- Inputs: DatasetRef
- Outputs: artifact/model dataset ref
- Purpose: convert Noodle DatasetRef to HF dataset format.

#### Train LoRA Adapter

- ID: `train_lora_adapter`
- Requirements:
  - `torch`
  - `transformers`
  - `datasets`
  - `accelerate`
  - `trl`
  - `peft`
- Inputs: FineTuneDatasetRef or HF dataset artifact
- Outputs: ModelArtifactRef
- Parameters:
  - `base_model`
  - `text_column` or chat format
  - `output_name`
  - `epochs`
  - `learning_rate`
  - `batch_size`
  - `gradient_accumulation_steps`
  - `max_seq_length`
  - `lora_rank`
  - `lora_alpha`
  - `lora_dropout`
  - `save_steps`
  - `resume_from_checkpoint`
- Production concerns:
  - write checkpoints as artifacts or controlled workdir files;
  - return adapter ref, not loaded model object;
  - capture trainer logs;
  - fail early if no GPU when `require_gpu=true`.

#### Train QLoRA Adapter

- ID: `train_qlora_adapter`
- Requirements:
  - `torch`
  - `transformers`
  - `datasets`
  - `accelerate`
  - `trl`
  - `peft`
  - `bitsandbytes`
- Same contract as LoRA, with quantization-specific parameters.

#### Train Unsloth Adapter

- ID: `train_unsloth_adapter`
- Requirements:
  - `unsloth`
  - `torch`
  - `transformers`
  - `trl`
  - `peft`
- Purpose: optimized local fine-tuning for supported models.
- Keep separate from generic LoRA to avoid making `unsloth` a dependency for
  all local training nodes.

#### Merge LoRA Adapter

- ID: `merge_lora_adapter`
- Requirements: `torch`, `transformers`, `peft`
- Inputs: ModelArtifactRef
- Outputs: merged model artifact/ref

#### Quantize Model

- ID: `quantize_model`
- Requirements vary by backend:
  - `llama-cpp-python` for GGUF workflows where applicable;
  - `auto-gptq` for GPTQ;
  - `autoawq` for AWQ.
- Prefer separate backend-specific nodes if dependencies conflict.

#### Push Model To Hugging Face

- ID: `push_model_to_hf`
- Requirements: `huggingface_hub`
- Inputs: ModelArtifactRef
- Outputs: repository URL/commit metadata
- Safety:
  - require explicit public/private choice;
  - audit event for publish;
  - never publish by default.

### Tests

- Import tests mock missing heavy packages.
- Unit tests mock trainer classes and verify parameters.
- Integration tests are marked slow/gpu and skipped by default.
- Artifact layout tests verify adapters/checkpoints are stored correctly.

## Phase 5 - RAG Lifecycle Nodes

Objective: support production RAG systems beyond simple chunk/embed/query.

### Nodes

#### Chunking Experiment

- ID: `rag_chunking_experiment`
- Requirements: `pandas`, optional tokenizer package
- Inputs: documents DatasetRef/artifacts
- Outputs: DatasetRef of chunk variants and metrics
- Purpose: compare chunk size, overlap, splitter strategy.

#### Embedding Drift Monitor

- ID: `embedding_drift_monitor`
- Requirements: `numpy`, optional `scipy`
- Inputs: current embeddings and baseline embeddings
- Outputs: drift metrics/report

#### Retriever Benchmark

- ID: `retriever_benchmark`
- Requirements: `pandas`, provider/vector-store SDKs as needed
- Inputs: query set DatasetRef
- Outputs: EvalResultRef
- Metrics:
  - recall@k;
  - precision@k;
  - MRR;
  - answer hit rate.

#### RAG Answer Eval

- ID: `rag_answer_eval`
- Requirements: optional `ragas`, provider SDK
- Inputs: questions, contexts, generated answers
- Outputs: EvalResultRef
- Metrics:
  - faithfulness;
  - answer relevance;
  - context relevance;
  - groundedness.

#### Index Diff

- ID: `vector_index_diff`
- Requirements: vector-store SDK only for selected store
- Purpose: compute add/update/delete set before mutating an index.

#### Index Health Check

- ID: `vector_index_health_check`
- Purpose: sample queries, check vector dimensions, count, latency, namespace
  health, and stale documents.

#### RAG Regression Suite

- ID: `rag_regression_suite`
- Purpose: run a pinned set of queries against a retriever/answer chain and
  detect regressions before deployment.

## Phase 6 - Model Serving And Deployment Nodes

Objective: let Noodle build, start, test, and monitor model endpoints.

### Nodes

#### Start Ollama Model

- ID: `ollama_start_model`
- Requirements: `requests`
- Purpose: pull/start/check local Ollama model on a runner.
- Safety: runner-local side effect; mark unsafe/side-effecting.

#### Start vLLM Server

- ID: `vllm_start_server`
- Requirements: `vllm`
- Purpose: start an OpenAI-compatible server for a model artifact or HF model.
- Production concern: long-lived service lifecycle does not fit a normal node
  perfectly. Prefer generating a deployment spec first.

#### Generate Model Deployment Spec

- ID: `model_deployment_spec`
- Requirements: none
- Outputs: artifact containing Docker/Kubernetes/Compose spec
- Purpose: define deployment without starting it inline.

#### Probe Model Endpoint

- ID: `model_endpoint_probe`
- Requirements: `requests`
- Purpose: check `/v1/models`, simple completion, latency, and response schema.

#### Benchmark Model Endpoint

- ID: `model_endpoint_benchmark`
- Requirements: `httpx`, optional `numpy`, `pandas`
- Outputs: DatasetRef/report
- Metrics:
  - p50/p95 latency;
  - tokens/sec;
  - error rate;
  - max concurrency reached.

#### Shadow Compare Endpoint

- ID: `shadow_compare_endpoint`
- Requirements: provider SDK or `httpx`
- Purpose: send traffic samples to old and new endpoints, compare quality,
  latency, and cost.

## Phase 7 - Monitoring, Observability, And MLOps

Objective: keep models and AI workflows healthy after deployment.

### Nodes

#### AI Trace Export

- ID: `ai_trace_export`
- Requirements: none for Noodle run data; optional provider SDK
- Purpose: export prompts, responses, tool calls, costs, and labels into a
  DatasetRef for eval/fine-tuning.

#### Prompt Drift Monitor

- ID: `prompt_drift_monitor`
- Requirements: `numpy`, optional embedding provider
- Purpose: detect shift in user prompts over time.

#### Response Quality Monitor

- ID: `response_quality_monitor`
- Requirements: provider SDK for judge mode
- Purpose: sample production responses and score with rules or judge model.

#### Cost Budget Gate

- ID: `cost_budget_gate`
- Requirements: none
- Purpose: branch/fail when token or provider cost exceeds a budget.

#### Latency SLO Gate

- ID: `latency_slo_gate`
- Requirements: none
- Purpose: branch/fail based on run/node latency metrics.

#### Model Registry Query

- ID: `model_registry_query`
- Requirements: none
- Purpose: look up registered models by provider, task, status, tag, metric.

#### Model Promote

- ID: `model_promote`
- Requirements: none
- Purpose: mark model version as staging/production after eval gates pass.

#### Model Rollback

- ID: `model_rollback`
- Requirements: none
- Purpose: revert production alias to previous registered version.

#### MLflow Log Metrics

- ID: `mlflow_log_metrics`
- Requirements: `mlflow`
- Purpose: log Noodle eval/training metrics to MLflow.

#### Weights & Biases Log

- ID: `wandb_log`
- Requirements: `wandb`
- Purpose: log training/eval metrics externally.

## Phase 8 - Advanced High-Differentiation Nodes

These are not first-wave nodes, but they are high-value differentiators.

### Agent And Tooling

- `agent_tool_trace_eval`: evaluate whether agent tool calls were necessary,
  correct, and safe.
- `tool_schema_fuzzer`: generate invalid/edge-case tool arguments and check
  tool validation.
- `agent_plan_replay`: replay an agent run deterministically against fixed tool
  outputs.
- `agent_safety_policy_check`: inspect proposed tool calls against policy.
- `mcp_tool_eval`: evaluate MCP tool results and schemas.

### Prompt Engineering

- `prompt_variant_generate`: generate prompt variants from a seed.
- `prompt_ab_test`: compare prompts against an eval set.
- `prompt_optimizer`: iterative prompt improvement with held-out eval guard.
- `prompt_contract_test`: assert output schema, forbidden phrases, style, and
  latency constraints.
- `system_prompt_diff`: compare behavior changes between prompt versions.

### Data Acquisition And Curation

- `web_corpus_builder`: crawl allowed URLs and produce clean document artifacts.
- `html_main_content_extract`: extract readable article/body text.
- `pdf_table_extract`: extract tables from PDFs.
- `document_layout_parse`: preserve headings, tables, figures, and page refs.
- `ocr_batch`: OCR document/image artifacts.
- `citation_grounding_check`: verify answer citations against source chunks.

### Classic ML And Data Science

- `train_forecaster`: time-series forecasting.
- `forecast_backtest`: rolling-window evaluation.
- `anomaly_detect`: tabular/time-series anomaly detection.
- `cluster_dataset`: embeddings or tabular clustering.
- `feature_importance_report`: model explainability.
- `shap_explain`: SHAP explanations.
- `optuna_tune_model`: hyperparameter search.
- `calibrate_classifier`: probability calibration.
- `fairness_report`: group metrics and bias checks.

### Model Conversion And Runtime

- `export_onnx`: convert compatible models to ONNX.
- `onnx_inference`: run ONNX model inference.
- `coreml_export`: export compatible models for Apple runtimes.
- `tensorrt_build`: build TensorRT engine.
- `gguf_convert`: convert supported models to GGUF.
- `embedding_cache`: cache embeddings keyed by content hash/model.
- `batch_inference`: run large DatasetRef inference jobs with concurrency and
  checkpointing.

### Governance

- `license_scan_model`: check model/dataset license metadata.
- `training_data_manifest`: create audit manifest for data used in training.
- `model_card_generate`: generate a model card from training/eval metadata.
- `risk_assessment_generate`: create a release checklist for sensitive models.
- `red_team_prompt_suite`: run jailbreak/safety test sets.
- `privacy_leakage_test`: test memorization/PII leakage against canaries.

## Production UX Requirements

### Editor

- Missing-package warning badge on nodes.
- Missing-package banner in node details with:
  - Add packages to current env;
  - Switch to env that satisfies requirements;
  - Link to environment package drawer.
- GPU warning for local training nodes when workflow has no GPU runner pool.
- Long-running node warning when default timeout is likely too low.
- Artifact cards for JSONL, eval report, model artifact, checkpoint, and model
  card outputs.
- DatasetRef preview for eval row results and generated training examples.

### Runs And Timeline

- Training/fine-tuning nodes must show:
  - submitted job id;
  - provider status;
  - latest metric/event when available;
  - links to artifacts;
  - elapsed time.
- Eval nodes must show:
  - pass/fail;
  - primary metric;
  - row count;
  - cost estimate;
  - report artifact.

### Environments

Add presets:

- `AI Provider Fine-Tuning`
  - `openai`
  - `tiktoken`
  - `pandas`
- `LLM Evaluation`
  - `openai`
  - `pandas`
  - `numpy`
  - optional `ragas`, `deepeval`
- `Local LLM Training`
  - `torch`
  - `transformers`
  - `datasets`
  - `accelerate`
  - `trl`
  - `peft`
- `Local QLoRA Training`
  - Local LLM Training packages plus `bitsandbytes`
- `Model Serving`
  - `vllm` or `llama-cpp-python` depending on selected path

Presets should be templates, not default installs.

## Backend And API Enhancements

These are not required for the OpenAI fine-tuning MVP, but are needed for a
fully production-grade AI/ML lifecycle.

### Model Registry Tables

Add tables:

- `model_registry_entries`
- `model_registry_versions`
- `model_registry_aliases`
- `model_registry_metrics`

Fields:

- provider;
- model id or artifact id;
- base model;
- task;
- owner;
- created run id;
- training dataset artifact;
- eval result artifact;
- status: draft, candidate, staging, production, archived;
- tags;
- metadata JSON;
- audit timestamps.

### Training Job Table

Provider-side fine-tuning jobs can outlive a workflow run. Add optional table:

- `training_jobs`

Fields:

- provider;
- provider job id;
- workflow id;
- run id;
- node id;
- status;
- model;
- fine-tuned model;
- training file id;
- validation file id;
- last polled at;
- finished at;
- error;
- metadata.

This lets status polling and dashboards work outside the original run.

### Node Progress Events

The engine currently has node started/finished events. For long local training,
add a supported progress channel later:

```python
from noodle.context import node_progress

node_progress.emit({
    "step": 100,
    "epoch": 1,
    "loss": 1.23,
    "learning_rate": 0.0002,
})
```

If this is too large for v1, use logs/debug summaries and artifact logs first.

### Runner Capabilities

Add capability metadata to runner pools/runners:

- labels;
- GPU count;
- VRAM;
- CUDA version;
- disk free;
- Python version;
- package cache status.

Training nodes can warn or fail early if requirements are not met.

## Testing Strategy

### Unit Tests

- Import tests prove optional dependencies are not imported globally.
- Requirement metadata tests for every node.
- Envelope validation tests.
- Dataset conversion tests.
- Artifact read/write tests.
- Branching tests for eval gates.

### Mocked Provider Tests

- OpenAI file upload/create/status/cancel/checkpoints.
- Hugging Face Hub push.
- MLflow/W&B logging.
- Provider failures, rate limits, retries, and malformed responses.

### Slow Integration Tests

Marked and skipped by default:

- actual OpenAI fine-tune smoke with tiny dataset, if credentials are present;
- local LoRA mock or tiny model training;
- vector-store benchmark against local Qdrant/Chroma;
- model endpoint benchmark against local test server.

### Frontend Tests

- Missing-package banner appears for new nodes.
- Add-to-env action calls bulk package API.
- Artifact cards render fine-tune datasets/eval reports/model artifacts.
- GPU warnings render for local training nodes without a GPU runner pool.

### Production Tests

- Run through durable queue.
- Run through subprocess environment.
- Run through remote runner pool.
- Cancel long-running local training node.
- Timeout local training node.
- Store artifacts in local and S3 backends.

## Observability

Every node should emit or return enough metadata to debug production runs:

- provider request id, if safe;
- provider job id;
- model ids;
- token counts;
- cost estimates;
- row counts;
- artifact ids;
- elapsed time;
- retry count;
- rate-limit info when available;
- redacted errors.

Do not log prompts, responses, training rows, or file contents by default.
Expose explicit "include sample rows" options only for trusted local workflows.

## Security Checklist

- Redact credentials and provider tokens.
- Warn before uploading datasets to third-party providers.
- Provide PII scan/decontamination nodes before provider fine-tuning.
- Do not publish models publicly by default.
- Audit model promotion, rollback, and publishing.
- Respect artifact retention settings.
- Mark local serving/training process nodes as side-effecting.
- Avoid arbitrary output paths unless policy allows filesystem nodes.

## Rollout Plan

### Milestone A - Safe MVP

Ship:

1. `llm_fine_tune_dataset`
2. `openai_upload_fine_tune_file`
3. `openai_create_fine_tune_job`
4. `openai_fine_tune_status`
5. `openai_cancel_fine_tune_job`
6. `llm_rule_eval`
7. `eval_gate`

Why:

- No GPU dependency.
- Completes a real provider fine-tuning loop.
- Exercises package prompts, artifacts, credentials, and run status.

### Milestone B - Measurable Release

Ship:

1. `llm_eval_dataset`
2. `llm_compare_models`
3. `llm_judge`
4. `eval_report`
5. `register_fine_tuned_model`

Why:

- Fine-tuning becomes defensible and production-worthy.

### Milestone C - Data Quality

Ship:

1. `synthetic_examples_generate`
2. `preference_pair_generate`
3. `dataset_pii_scan`
4. `dataset_deduplicate_semantic`
5. `token_profile`

Why:

- Improves dataset quality before training and reduces risk.

### Milestone D - Local/GPU Training Beta

Ship:

1. `hf_dataset_export`
2. `train_lora_adapter`
3. `train_qlora_adapter`
4. `merge_lora_adapter`
5. `push_model_to_hf`

Why:

- This is the strongest Python-native differentiator, but should be beta until
  runner capability checks and slow tests are stable.

### Milestone E - RAG Lifecycle

Ship:

1. `rag_chunking_experiment`
2. `retriever_benchmark`
3. `rag_answer_eval`
4. `vector_index_diff`
5. `index_health_check`
6. `rag_regression_suite`

### Milestone F - Serving And Monitoring

Ship:

1. `model_endpoint_probe`
2. `model_endpoint_benchmark`
3. `shadow_compare_endpoint`
4. `prompt_drift_monitor`
5. `response_quality_monitor`
6. `cost_budget_gate`
7. `model_promote`
8. `model_rollback`

## First Implementation Slice

Start with this exact PR shape:

1. Add `packages/nodes/noodle_nodes/llm_training.py`.
2. Register it in `packages/nodes/noodle_nodes/__init__.py`.
3. Add helper envelope functions and lazy import helpers.
4. Implement:
   - `llm_fine_tune_dataset`
   - `openai_upload_fine_tune_file`
   - `openai_create_fine_tune_job`
   - `openai_fine_tune_status`
   - `openai_cancel_fine_tune_job`
5. Add `packages/nodes/noodle_nodes/llm_evals.py`.
6. Implement:
   - `llm_rule_eval`
   - `eval_gate`
7. Add tests:
   - `packages/nodes/tests/test_llm_training.py`
   - `packages/nodes/tests/test_llm_evals.py`
8. Add one workflow template:
   - DatasetRef -> Fine-Tune Dataset -> Upload -> Create Job -> Status
9. Add docs:
   - node list;
   - package presets;
   - example workflow;
   - production cautions.

Acceptance:

- Base test environment can import and enumerate all nodes without optional
  ML/AI packages installed.
- Missing packages are visible in editor and blocked by API preflight.
- Mocked OpenAI tests pass.
- JSONL artifacts and eval outputs are artifact/DatasetRef-backed.
- No node stores full training rows in normal run output.

## Recommended Initial Node IDs

Use stable, explicit ids:

- `llm_fine_tune_dataset`
- `openai_upload_fine_tune_file`
- `openai_create_fine_tune_job`
- `openai_fine_tune_status`
- `openai_fine_tune_checkpoints`
- `openai_cancel_fine_tune_job`
- `register_fine_tuned_model`
- `llm_eval_dataset`
- `llm_compare_models`
- `llm_judge`
- `llm_rule_eval`
- `eval_gate`
- `eval_report`
- `synthetic_examples_generate`
- `preference_pair_generate`
- `weak_label`
- `dataset_deduplicate_semantic`
- `dataset_pii_scan`
- `dataset_decontaminate`
- `token_profile`
- `hf_dataset_export`
- `train_lora_adapter`
- `train_qlora_adapter`
- `train_unsloth_adapter`
- `merge_lora_adapter`
- `quantize_model`
- `push_model_to_hf`
- `rag_chunking_experiment`
- `retriever_benchmark`
- `rag_answer_eval`
- `vector_index_diff`
- `vector_index_health_check`
- `rag_regression_suite`
- `model_endpoint_probe`
- `model_endpoint_benchmark`
- `shadow_compare_endpoint`
- `prompt_drift_monitor`
- `response_quality_monitor`
- `cost_budget_gate`
- `latency_slo_gate`
- `model_registry_query`
- `model_promote`
- `model_rollback`

## Open Questions

- Should model registry live in artifact metadata only for v1, or should it get
  database tables before local training ships?
- Should provider fine-tuning jobs be tracked in a persistent `training_jobs`
  table from the start?
- What is the canonical GPU runner-pool capability schema?
- Should long-running local training use normal workflow timeouts or a separate
  training-job lease?
- Which eval package should be blessed first: built-in deterministic evals,
  `ragas`, `deepeval`, or a Noodle-native lightweight evaluator?

## Recommendation

Implement Milestone A and B first. They deliver a complete fine-tuning and
evaluation loop without needing GPU infrastructure:

```text
DatasetRef
  -> LLM Fine-Tune Dataset
  -> OpenAI Upload Fine-Tune File
  -> OpenAI Create Fine-Tune Job
  -> OpenAI Fine-Tune Status
  -> LLM Compare Models
  -> Eval Gate
  -> Register Fine-Tuned Model
```

After that, add data-quality nodes and only then ship local LoRA/QLoRA training
as a beta tied to GPU runner pools.
