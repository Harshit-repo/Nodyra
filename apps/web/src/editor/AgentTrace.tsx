import { CaretDown, CaretRight, Circle, Clock, Function, Lightning, Warning } from "@phosphor-icons/react";
import { useState } from "react";

// ── types ──────────────────────────────────────────────────────────────────

export interface AgentStep {
  /** Step type */
  type: "thought" | "tool_call" | "tool_result" | "output" | "error";
  /** Timestamp (ISO 8601) */
  timestamp?: string;
  /** Human-readable label */
  label: string;
  /** Optional detail content (tool arguments, thought text, etc.) */
  detail?: string;
  /** Tool name (for tool_call / tool_result steps) */
  tool?: string;
  /** Duration in ms */
  durationMs?: number;
}

export interface AgentTraceData {
  steps: AgentStep[];
  model?: string;
  totalTokens?: number;
  totalDurationMs?: number;
}

// ── step type config ──────────────────────────────────────────────────────

const STEP_CONFIG: Record<AgentStep["type"], { icon: typeof Circle; color: string; label: string }> = {
  thought: { icon: Circle, color: "#a78bfa", label: "Thought" },
  tool_call: { icon: Function, color: "#f6b44b", label: "Tool call" },
  tool_result: { icon: Function, color: "#57c98a", label: "Tool result" },
  output: { icon: Lightning, color: "#6ea8ff", label: "Output" },
  error: { icon: Warning, color: "#f0747a", label: "Error" },
};

// ── helpers ────────────────────────────────────────────────────────────────

function formatMs(ms?: number): string {
  if (ms === undefined || ms === null) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// ── component ──────────────────────────────────────────────────────────────

export function AgentTrace({ trace }: { trace: AgentTraceData | null | undefined }) {
  if (!trace || trace.steps.length === 0) {
    return <p className="muted">No agent trace available for this run.</p>;
  }

  return (
    <div className="agent-trace">
      {/* Summary bar */}
      <div className="agent-trace-summary">
        {trace.model && <span className="agent-trace-model">{trace.model}</span>}
        {trace.totalTokens != null && (
          <span className="agent-trace-tokens">{trace.totalTokens.toLocaleString()} tokens</span>
        )}
        {trace.totalDurationMs != null && (
          <span className="agent-trace-duration">{formatMs(trace.totalDurationMs)}</span>
        )}
      </div>

      {/* Step list */}
      <div className="agent-trace-steps">
        {trace.steps.map((step, i) => (
          <AgentStepRow key={i} step={step} index={i} />
        ))}
      </div>
    </div>
  );
}

function AgentStepRow({ step }: { step: AgentStep; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const config = STEP_CONFIG[step.type];
  const Icon = config.icon;
  const hasDetail = Boolean(step.detail);

  return (
    <div className={`agent-step agent-step--${step.type}`}>
      {/* Timeline connector */}
      <div className="agent-step-timeline">
        <span className="agent-step-dot" style={{ backgroundColor: config.color }}>
          <Icon size={10} weight="fill" color="#090b10" />
        </span>
      </div>

      {/* Content */}
      <div className="agent-step-body">
        <div
          className={`agent-step-head${hasDetail ? " is-expandable" : ""}`}
          onClick={hasDetail ? () => setExpanded((v) => !v) : undefined}
          role={hasDetail ? "button" : undefined}
          tabIndex={hasDetail ? 0 : undefined}
          aria-expanded={hasDetail ? expanded : undefined}
          onKeyDown={hasDetail ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setExpanded((v) => !v); } } : undefined}
        >
          <span className="agent-step-type" style={{ color: config.color }}>
            {config.label}
          </span>
          <span className="agent-step-label">{step.label}</span>
          <span className="agent-step-meta">
            {step.tool && <span className="agent-step-tool">{step.tool}</span>}
            {step.durationMs != null && (
              <span className="agent-step-time">
                <Clock size={10} />
                {formatMs(step.durationMs)}
              </span>
            )}
          </span>
          {hasDetail && (
            <span className="agent-step-chevron">
              {expanded ? <CaretDown size={12} /> : <CaretRight size={12} />}
            </span>
          )}
        </div>

        {expanded && step.detail && (
          <pre className="agent-step-detail">{step.detail}</pre>
        )}
      </div>
    </div>
  );
}

/**
 * Extract agent trace data from run metadata.
 * Parses the runMeta structure to build a timeline of agent steps.
 */
export function extractAgentTrace(
  runMeta: Record<string, unknown> | undefined,
): AgentTraceData | null {
  if (!runMeta) return null;

  const steps: AgentStep[] = [];

  // Extract agent steps from common metadata formats
  const agentSteps = runMeta.agent_steps as Array<Record<string, unknown>> | undefined;
  const llmCalls = runMeta.llm_calls as Array<Record<string, unknown>> | undefined;
  const toolCalls = runMeta.tool_calls as Array<Record<string, unknown>> | undefined;

  if (agentSteps) {
    for (const s of agentSteps) {
      steps.push({
        type: (s.type as AgentStep["type"]) ?? "thought",
        label: String(s.label ?? s.action ?? ""),
        detail: s.detail ? String(s.detail) : undefined,
        tool: s.tool ? String(s.tool) : undefined,
        timestamp: s.timestamp ? String(s.timestamp) : undefined,
        durationMs: s.duration_ms ? Number(s.duration_ms) : undefined,
      });
    }
  } else if (llmCalls) {
    // Fallback: construct from LLM call records
    for (const call of llmCalls) {
      if (call.prompt) {
        steps.push({
          type: "thought",
          label: "LLM prompt",
          detail: String(call.prompt).slice(0, 500),
          timestamp: call.timestamp ? String(call.timestamp) : undefined,
        });
      }
      if (call.completion) {
        steps.push({
          type: "output",
          label: "LLM response",
          detail: String(call.completion).slice(0, 1000),
          timestamp: call.timestamp ? String(call.timestamp) : undefined,
        });
      }
    }
  }

  if (toolCalls) {
    for (const tc of toolCalls) {
      steps.push({
        type: "tool_call",
        label: String(tc.name ?? "tool"),
        tool: String(tc.name ?? ""),
        detail: tc.args ? JSON.stringify(tc.args, null, 2) : undefined,
        timestamp: tc.timestamp ? String(tc.timestamp) : undefined,
        durationMs: tc.duration_ms ? Number(tc.duration_ms) : undefined,
      });
      if (tc.result !== undefined) {
        steps.push({
          type: "tool_result",
          label: `Result from ${String(tc.name ?? "tool")}`,
          detail: typeof tc.result === "string" ? tc.result : JSON.stringify(tc.result, null, 2),
          tool: String(tc.name ?? ""),
          timestamp: tc.timestamp ? String(tc.timestamp) : undefined,
        });
      }
    }
  }

  if (steps.length === 0 && runMeta.error) {
    steps.push({
      type: "error",
      label: "Agent error",
      detail: String(runMeta.error),
    });
  }

  if (steps.length === 0 && runMeta.output) {
    steps.push({
      type: "output",
      label: "Final output",
      detail: typeof runMeta.output === "string" ? runMeta.output : JSON.stringify(runMeta.output, null, 2),
    });
  }

  if (steps.length === 0) return null;

  const totalTokens = (runMeta.total_tokens as number) ??
    (runMeta.token_usage as Record<string, number>)?.total_tokens;

  const totalDurationMs = (runMeta.total_duration_ms as number) ??
    (runMeta.duration_ms as number);

  return {
    steps: steps.sort((a, b) => (a.timestamp ?? "").localeCompare(b.timestamp ?? "")),
    model: (runMeta.model as string) ?? (runMeta.model_name as string),
    totalTokens: totalTokens ?? undefined,
    totalDurationMs: totalDurationMs ?? undefined,
  };
}
