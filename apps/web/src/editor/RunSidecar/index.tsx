import { useRunSidecar, type SidecarTab } from "./useRunSidecar";
import { RunList } from "./RunList";
import { RunDiff } from "./RunDiff";
import { RunTimeline } from "./RunTimeline";
import { RunStats } from "./RunStats";
import { RunInfo } from "./RunInfo";

interface RunSidecarProps {
  workflowId: string;
}

const TABS: Array<{ id: SidecarTab; icon: string; label: string; title: string }> = [
  { id: "runs", icon: "≡", label: "Runs", title: "Run history" },
  { id: "diff", icon: "⇄", label: "Diff", title: "Compare two runs" },
  { id: "timeline", icon: "◫", label: "Time", title: "Execution waterfall" },
  { id: "stats", icon: "⬡", label: "Stats", title: "Reliability" },
  { id: "info", icon: "◎", label: "Info", title: "Trigger payload & notes" },
];

export function RunSidecar({ workflowId }: RunSidecarProps) {
  const {
    activeTab,
    setActiveTab,
    selectedRunId,
    setSelectedRunId,
    pinnedNodeId,
    setPinnedNodeId,
    diffPair,
    setDiffPair,
  } = useRunSidecar();

  return (
    <aside className="run-sidecar">
      <nav className="sc-tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`sc-tab${activeTab === tab.id ? " active" : ""}`}
            title={tab.title}
            onClick={() => setActiveTab(tab.id)}
          >
            <span className="sc-tab-icon">{tab.icon}</span>
            <span className="sc-tab-lbl">{tab.label}</span>
          </button>
        ))}
      </nav>

      <div className={`sc-panel${activeTab === "runs" ? " active" : ""}`} role="tabpanel">
        <RunList
          workflowId={workflowId}
          selectedRunId={selectedRunId}
          onSelectRun={setSelectedRunId}
          pinnedNodeId={pinnedNodeId}
          onPinNode={setPinnedNodeId}
          onSwitchTab={setActiveTab}
        />
      </div>

      <div className={`sc-panel${activeTab === "diff" ? " active" : ""}`} role="tabpanel">
        <RunDiff workflowId={workflowId} diffPair={diffPair} onChangePair={setDiffPair} />
      </div>

      <div className={`sc-panel${activeTab === "timeline" ? " active" : ""}`} role="tabpanel">
        <RunTimeline selectedRunId={selectedRunId} />
      </div>

      <div className={`sc-panel${activeTab === "stats" ? " active" : ""}`} role="tabpanel">
        <RunStats workflowId={workflowId} />
      </div>

      <div className={`sc-panel${activeTab === "info" ? " active" : ""}`} role="tabpanel">
        <RunInfo selectedRunId={selectedRunId} onOpenDiff={() => setActiveTab("diff")} />
      </div>
    </aside>
  );
}
