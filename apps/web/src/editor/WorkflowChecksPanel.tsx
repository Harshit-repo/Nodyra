import { useEffect, useMemo, useState } from "react";

import { api, userFriendlyError } from "../api";
import type {
  WorkflowCheckCase,
  WorkflowCheckInfo,
  WorkflowCheckRunResult,
} from "../types";
import { A11yModal } from "./A11yModal";

interface Props {
  workflowId: string;
  onClose: () => void;
}

type BusyState = "load" | "generate" | "save" | "run-all" | `run:${string}` | `delete:${string}` | null;

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value);
  }
}

function normalizeCase(raw: Partial<WorkflowCheckCase>, index: number): WorkflowCheckCase {
  const assertions = Array.isArray(raw.assertions)
    ? raw.assertions.map((item) => String(item))
    : [];
  return {
    name: typeof raw.name === "string" && raw.name.trim()
      ? raw.name.trim()
      : `Generated check ${index + 1}`,
    input_data:
      raw.input_data && typeof raw.input_data === "object" && !Array.isArray(raw.input_data)
        ? raw.input_data
        : {},
    expected_outputs:
      raw.expected_outputs &&
      typeof raw.expected_outputs === "object" &&
      !Array.isArray(raw.expected_outputs)
        ? raw.expected_outputs
        : {},
    assertions,
  };
}

function formatDate(value?: string | null): string {
  if (!value) return "Never run";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function statusClass(status: string): string {
  if (status === "passed") return " workflow-check-status--passed";
  if (status === "failed") return " workflow-check-status--failed";
  return "";
}

function lastFailures(check: WorkflowCheckInfo): string[] {
  const failures = check.last_result?.failures;
  if (!Array.isArray(failures)) return [];
  return failures.map((failure) => String(failure));
}

export function WorkflowChecksPanel({ workflowId, onClose }: Props) {
  const [checks, setChecks] = useState<WorkflowCheckInfo[]>([]);
  const [draftCases, setDraftCases] = useState<WorkflowCheckCase[]>([]);
  const [runResults, setRunResults] = useState<Record<string, WorkflowCheckRunResult>>({});
  const [busy, setBusy] = useState<BusyState>("load");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setBusy("load");
    setError(null);
    api
      .listWorkflowChecks(workflowId)
      .then((rows) => {
        if (!cancelled) setChecks(rows);
      })
      .catch((err) => {
        if (!cancelled) setError(userFriendlyError(err));
      })
      .finally(() => {
        if (!cancelled) setBusy(null);
      });
    return () => {
      cancelled = true;
    };
  }, [workflowId]);

  const resultByCheck = useMemo(() => runResults, [runResults]);

  async function generateTests(): Promise<void> {
    setBusy("generate");
    setError(null);
    setNotice(null);
    try {
      const response = await api.generateWorkflowTests(workflowId);
      const generated = (response.tests ?? []).map((item, index) =>
        normalizeCase(item, index),
      );
      setDraftCases(generated);
      setNotice(
        generated.length
          ? `${generated.length} generated check${generated.length === 1 ? "" : "s"} ready.`
          : "No checks were generated.",
      );
    } catch (err) {
      setError(userFriendlyError(err));
    } finally {
      setBusy(null);
    }
  }

  async function saveDraftCases(): Promise<void> {
    if (!draftCases.length) return;
    setBusy("save");
    setError(null);
    setNotice(null);
    try {
      const saved = await api.saveWorkflowChecks(workflowId, {
        checks: draftCases,
        replace: false,
      });
      setChecks((existing) => [...existing, ...saved]);
      setDraftCases([]);
      setNotice(`${saved.length} check${saved.length === 1 ? "" : "s"} saved.`);
    } catch (err) {
      setError(userFriendlyError(err));
    } finally {
      setBusy(null);
    }
  }

  async function runAllChecks(): Promise<void> {
    if (!checks.length) return;
    setBusy("run-all");
    setError(null);
    setNotice(null);
    try {
      const results = await api.runWorkflowChecks(workflowId);
      setRunResults(Object.fromEntries(results.map((result) => [result.check.id, result])));
      setChecks(results.map((result) => result.check));
      const failed = results.filter((result) => !result.passed).length;
      const totalLabel = `${results.length} check${results.length === 1 ? "" : "s"}`;
      setNotice(
        failed
          ? `${failed} of ${totalLabel} failed.`
          : `${totalLabel} passed.`,
      );
    } catch (err) {
      setError(userFriendlyError(err));
    } finally {
      setBusy(null);
    }
  }

  async function runOneCheck(checkId: string): Promise<void> {
    setBusy(`run:${checkId}`);
    setError(null);
    setNotice(null);
    try {
      const result = await api.runWorkflowCheck(workflowId, checkId);
      setRunResults((current) => ({ ...current, [checkId]: result }));
      setChecks((current) =>
        current.map((check) => (check.id === checkId ? result.check : check)),
      );
      setNotice(result.passed ? "Check passed." : "Check failed.");
    } catch (err) {
      setError(userFriendlyError(err));
    } finally {
      setBusy(null);
    }
  }

  async function deleteCheck(checkId: string): Promise<void> {
    if (!window.confirm("Delete this workflow check?")) return;
    setBusy(`delete:${checkId}`);
    setError(null);
    setNotice(null);
    try {
      await api.deleteWorkflowCheck(workflowId, checkId);
      setChecks((current) => current.filter((check) => check.id !== checkId));
      setRunResults((current) => {
        const next = { ...current };
        delete next[checkId];
        return next;
      });
      setNotice("Check deleted.");
    } catch (err) {
      setError(userFriendlyError(err));
    } finally {
      setBusy(null);
    }
  }

  const isLoading = busy === "load";
  const isBusy = busy !== null;

  return (
    <A11yModal
      className="workflow-checks-modal modal-wide"
      titleId="workflow-checks-title"
      title="Workflow Checks"
      onClose={onClose}
    >
      <div className="modal-body workflow-checks-body">
        <div className="workflow-checks-toolbar">
          <div className="workflow-checks-counts">
            <span className="workflow-checks-count">{checks.length} saved</span>
            {draftCases.length > 0 && (
              <span className="workflow-checks-count workflow-checks-count--draft">
                {draftCases.length} generated
              </span>
            )}
          </div>
          <div className="workflow-checks-actions">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => void generateTests()}
              disabled={isBusy}
            >
              {busy === "generate" ? "Generating..." : "Generate tests"}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={() => void runAllChecks()}
              disabled={isBusy || !checks.length}
            >
              {busy === "run-all" ? "Running..." : "Run checks"}
            </button>
          </div>
        </div>

        {error && <p className="error-text workflow-checks-message">{error}</p>}
        {notice && !error && <p className="workflow-checks-message muted">{notice}</p>}
        {isLoading && <p className="muted">Loading checks...</p>}

        {!isLoading && draftCases.length > 0 && (
          <section className="workflow-checks-section" aria-labelledby="generated-checks-title">
            <div className="workflow-checks-section-head">
              <h3 id="generated-checks-title">Generated Checks</h3>
              <div className="workflow-checks-section-actions">
                <button
                  type="button"
                  className="btn btn-sm btn-ghost"
                  onClick={() => setDraftCases([])}
                  disabled={isBusy}
                >
                  Discard
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  onClick={() => void saveDraftCases()}
                  disabled={isBusy}
                >
                  {busy === "save" ? "Saving..." : "Save generated checks"}
                </button>
              </div>
            </div>
            <ul className="workflow-checks-list">
              {draftCases.map((testCase, index) => (
                <li key={`${testCase.name}-${index}`} className="workflow-check-row workflow-check-row--draft">
                  <div className="workflow-check-row-head">
                    <div className="workflow-check-title">
                      <strong>{testCase.name}</strong>
                    </div>
                  </div>
                  <CheckCaseBody testCase={testCase} />
                </li>
              ))}
            </ul>
          </section>
        )}

        {!isLoading && (
          <section className="workflow-checks-section" aria-labelledby="saved-checks-title">
            <div className="workflow-checks-section-head">
              <h3 id="saved-checks-title">Saved Checks</h3>
            </div>
            {checks.length === 0 ? (
              <p className="muted">No workflow checks saved.</p>
            ) : (
              <ul className="workflow-checks-list">
                {checks.map((check) => {
                  const result = resultByCheck[check.id];
                  const failures = result?.failures ?? lastFailures(check);
                  return (
                    <li key={check.id} className="workflow-check-row">
                      <div className="workflow-check-row-head">
                        <div className="workflow-check-title">
                          <strong>{check.name}</strong>
                          <span className="muted">{formatDate(check.last_run_at)}</span>
                        </div>
                        <span className={`workflow-check-status${statusClass(check.status)}`}>
                          {check.status}
                        </span>
                      </div>
                      <CheckCaseBody testCase={check} />
                      {failures.length > 0 && (
                        <ul className="workflow-check-failures">
                          {failures.map((failure, index) => (
                            <li key={`${check.id}-failure-${index}`}>{failure}</li>
                          ))}
                        </ul>
                      )}
                      <div className="workflow-check-row-actions">
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          onClick={() => void runOneCheck(check.id)}
                          disabled={isBusy}
                        >
                          {busy === `run:${check.id}` ? "Running..." : "Run"}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          onClick={() => void deleteCheck(check.id)}
                          disabled={isBusy}
                        >
                          {busy === `delete:${check.id}` ? "Deleting..." : "Delete"}
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        )}
      </div>
    </A11yModal>
  );
}

function CheckCaseBody({ testCase }: { testCase: WorkflowCheckCase }) {
  return (
    <div className="workflow-check-case">
      <div>
        <span>Input</span>
        <pre>{formatJson(testCase.input_data)}</pre>
      </div>
      <div>
        <span>Expected</span>
        <pre>{formatJson(testCase.expected_outputs)}</pre>
      </div>
      {testCase.assertions.length > 0 && (
        <div className="workflow-check-assertions">
          <span>Assertions</span>
          <ul>
            {testCase.assertions.map((assertion, index) => (
              <li key={`${assertion}-${index}`}>{assertion}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
