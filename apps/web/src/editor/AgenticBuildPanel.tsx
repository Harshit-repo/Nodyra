import { useState, useRef, useCallback } from "react";

import type {
  AgenticBuildEvent,
  WorkflowGraph,
} from "../types";
import { api } from "../api";
import { A11yModal } from "./A11yModal";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AgenticBuildPanelProps {
  workflowId: string;
  onAccept: (graph: WorkflowGraph) => void;
  onClose: () => void;
}

type BuildStatus =
  | "idle"
  | "drafting"
  | "running"
  | "fixing"
  | "converged"
  | "max_iterations"
  | "error"
  | "cancelled";

interface BuildLogEntry {
  iteration: number;
  action: string;
  detail: string;
  timestamp: number;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AgenticBuildPanel({
  workflowId,
  onAccept,
  onClose,
}: AgenticBuildPanelProps) {
  const [goal, setGoal] = useState("");
  const [testDataRaw, setTestDataRaw] = useState("{}");
  const [maxIterations, setMaxIterations] = useState(5);
  const [status, setStatus] = useState<BuildStatus>("idle");
  const [currentIteration, setCurrentIteration] = useState(0);
  const [logs, setLogs] = useState<BuildLogEntry[]>([]);
  const [latestGraph, setLatestGraph] = useState<WorkflowGraph | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [remainingErrors, setRemainingErrors] = useState<
    Array<{ node_id: string; error: string }>
  >([]);
  const [failingNodes, setFailingNodes] = useState<string[]>([]);
  const [runId, setRunId] = useState<string | null>(null);

  const sourceRef = useRef<EventSource | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  // The panel is "busy" when the loop is running
  const busy =
    status === "drafting" ||
    status === "running" ||
    status === "fixing";

  const canAccept =
    (status === "converged" || status === "max_iterations") &&
    latestGraph !== null;

  // -----------------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------------

  const addLog = useCallback(
    (iteration: number, action: string, detail: string) => {
      setLogs((prev) => [
        ...prev,
        { iteration, action, detail, timestamp: Date.now() },
      ]);
      // Auto-scroll to bottom after render
      setTimeout(() => {
        logEndRef.current?.scrollIntoView({ behavior: "smooth" });
      }, 50);
    },
    [],
  );

  const handleEvent = useCallback(
    (event: AgenticBuildEvent) => {
      switch (event.type) {
        case "iteration_start": {
          setCurrentIteration(event.iteration);
          const actionLabel = event.action === "draft" ? "Drafting" : "Fixing";
          setStatus(event.action === "draft" ? "drafting" : "fixing");
          addLog(
            event.iteration,
            actionLabel,
            `Iteration ${event.iteration} — ${actionLabel} workflow...`,
          );
          break;
        }
        case "graph_updated": {
          setLatestGraph(event.graph);
          addLog(
            currentIteration,
            "Graph updated",
            `Graph updated: ${event.explanation.substring(0, 200)}`,
          );
          break;
        }
        case "run_started": {
          setStatus("running");
          setRunId(event.run_id);
          addLog(
            currentIteration,
            "Run started",
            `Test run ${event.run_id.substring(0, 8)}...`,
          );
          break;
        }
        case "run_failed": {
          setStatus("fixing");
          const ids = event.errors.map((e) => e.node_id);
          setFailingNodes(ids);
          addLog(
            currentIteration,
            "Run failed",
            `${event.errors.length} node(s) failed: ${ids.join(", ")}`,
          );
          break;
        }
        case "fix_planned": {
          addLog(
            currentIteration,
            "Fix planned",
            `Target nodes: ${event.target_nodes.join(", ")} — ${event.diagnosis.substring(0, 300)}`,
          );
          break;
        }
        case "converged": {
          setStatus("converged");
          setLatestGraph(event.final_graph);
          addLog(
            event.iterations,
            "Converged",
            `Workflow converged after ${event.iterations} iteration(s)!`,
          );
          break;
        }
        case "max_iterations_reached": {
          setStatus("max_iterations");
          setLatestGraph(event.best_graph);
          setRemainingErrors(event.remaining_errors);
          addLog(
            0,
            "Max iterations",
            `Max iterations reached. ${event.remaining_errors.length} error(s) remain.`,
          );
          break;
        }
        case "error": {
          setStatus("error");
          setErrorMessage(event.message);
          addLog(0, "Error", event.message);
          break;
        }
      }
    },
    [currentIteration, addLog],
  );

  // -----------------------------------------------------------------------
  // Actions
  // -----------------------------------------------------------------------

  const startBuild = useCallback(() => {
    if (!goal.trim()) return;

    // Reset state
    setStatus("drafting");
    setCurrentIteration(0);
    setLogs([]);
    setLatestGraph(null);
    setErrorMessage("");
    setRemainingErrors([]);
    setFailingNodes([]);
    setRunId(null);

    let testData: Record<string, unknown> | null = null;
    try {
      testData = JSON.parse(testDataRaw || "{}");
    } catch {
      setStatus("error");
      setErrorMessage("Invalid JSON in test data field.");
      return;
    }

    addLog(0, "Starting", "Starting agentic build loop...");

    const es = api.startAgenticBuild(
      workflowId,
      { goal: goal.trim(), test_data: testData, max_iterations: maxIterations },
      handleEvent,
      (err) => {
        setStatus("error");
        setErrorMessage(err.message);
        addLog(0, "Error", err.message);
      },
      () => {
        // onClose — no extra work needed
      },
    );
    sourceRef.current = es;
  }, [goal, testDataRaw, maxIterations, workflowId, handleEvent, addLog]);

  const cancelBuild = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    setStatus("cancelled");
    addLog(0, "Cancelled", "Build loop cancelled by user.");
  }, [addLog]);

  const handleAccept = useCallback(() => {
    if (latestGraph) {
      onAccept(latestGraph);
    }
  }, [latestGraph, onAccept]);

  // -----------------------------------------------------------------------
  // Render helpers
  // -----------------------------------------------------------------------

  const statusBadge = () => {
    const labels: Record<BuildStatus, { text: string; cls: string }> = {
      idle: { text: "Ready", cls: "badge-neutral" },
      drafting: { text: "Drafting...", cls: "badge-info" },
      running: { text: "Running...", cls: "badge-warning" },
      fixing: { text: "Fixing...", cls: "badge-warning" },
      converged: { text: "Converged!", cls: "badge-success" },
      max_iterations: { text: "Max iterations", cls: "badge-warning" },
      error: { text: "Error", cls: "badge-error" },
      cancelled: { text: "Cancelled", cls: "badge-neutral" },
    };
    const { text, cls } = labels[status];
    return <span className={`badge ${cls}`}>{text}</span>;
  };

  return (
    <A11yModal
      className="agentic-build-modal"
      titleId="agentic-build-title"
      title="Agentic Build"
      onClose={onClose}
      closeDisabled={busy}
    >
      <div className="agentic-build-body">
        {/* ---- Input section ------------------------------------------------ */}
        <section className="ab-section">
          <div className="ab-section-head">
            <h4>Goal</h4>
            <p>Describe the workflow you want to build.</p>
          </div>
          <textarea
            className="ab-textarea"
            rows={3}
            placeholder='e.g. "Fetch GitHub PRs, score by complexity, send Slack alert for score &gt; 7"'
            value={goal}
            disabled={busy}
            onChange={(e) => setGoal(e.target.value)}
          />
        </section>

        <section className="ab-section">
          <div className="ab-section-head">
            <h4>Test data (JSON)</h4>
            <p>Sample input for test runs (optional).</p>
          </div>
          <textarea
            className="ab-textarea ab-textarea-sm"
            rows={2}
            placeholder='{"text": "hello"}'
            value={testDataRaw}
            disabled={busy}
            onChange={(e) => setTestDataRaw(e.target.value)}
          />
        </section>

        <section className="ab-section">
          <label className="ab-field">
            <span className="ab-field-label">Max iterations</span>
            <input
              type="number"
              min={1}
              max={5}
              value={maxIterations}
              disabled={busy}
              className="ab-number-input"
              onChange={(e) => setMaxIterations(Number(e.target.value))}
            />
          </label>
        </section>

        {/* ---- Actions ---------------------------------------------------- */}
        <div className="ab-actions">
          {!busy && status === "idle" && (
            <button
              className="btn btn-primary"
              onClick={startBuild}
              disabled={!goal.trim()}
            >
              Start Build Loop
            </button>
          )}
          {busy && (
            <button className="btn btn-ghost" onClick={cancelBuild}>
              Cancel Loop
            </button>
          )}
          {canAccept && (
            <button className="btn btn-primary" onClick={handleAccept}>
              Accept This Version
            </button>
          )}
          {status !== "idle" && !busy && !canAccept && (
            <button className="btn btn-ghost" onClick={onClose}>
              Close
            </button>
          )}
        </div>

        {/* ---- Progress / Log section ------------------------------------- */}
        {status !== "idle" && (
          <section className="ab-section ab-progress-section">
            <div className="ab-section-head">
              <h4>
                Progress{" "}
                {currentIteration > 0 && (
                  <span className="ab-iteration-counter">
                    (Iteration {currentIteration} of {maxIterations})
                  </span>
                )}
              </h4>
              <div className="ab-status-area">{statusBadge()}</div>
            </div>

            {/* Run info */}
            {runId && (
              <div className="ab-run-id">
                Run: <code>{runId.substring(0, 12)}...</code>
              </div>
            )}

            {/* Failing nodes highlight */}
            {failingNodes.length > 0 && (
              <div className="ab-failing-nodes">
                <strong>Failing nodes:</strong>{" "}
                {failingNodes.map((nid) => (
                  <span key={nid} className="ab-failing-node-chip">
                    {nid}
                  </span>
                ))}
              </div>
            )}

            {/* Remaining errors after max iterations */}
            {remainingErrors.length > 0 && (
              <div className="ab-remaining-errors">
                <strong>Remaining errors:</strong>
                <ul>
                  {remainingErrors.map((e, i) => (
                    <li key={i}>
                      <code>{e.node_id}</code>: {e.error}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Error message */}
            {errorMessage && status === "error" && (
              <div className="ab-error">{errorMessage}</div>
            )}

            {/* Event log */}
            <div className="ab-log">
              {logs.map((entry, i) => (
                <div key={i} className="ab-log-entry">
                  <span className="ab-log-iteration">
                    {entry.iteration > 0 ? `#${entry.iteration}` : ""}
                  </span>
                  <span className={`ab-log-action ab-log-action--${entry.action.toLowerCase().replace(/\s+/g, "-")}`}>
                    {entry.action}
                  </span>
                  <span className="ab-log-detail">{entry.detail}</span>
                </div>
              ))}
              <div ref={logEndRef} />
            </div>
          </section>
        )}
      </div>
    </A11yModal>
  );
}
