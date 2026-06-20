import { useState } from "react";

export type SidecarTab = "runs" | "diff" | "timeline" | "stats" | "info";

export interface RunSidecarState {
  activeTab: SidecarTab;
  setActiveTab: (tab: SidecarTab) => void;
  selectedRunId: string | null;
  setSelectedRunId: (id: string | null) => void;
  pinnedNodeId: string | null;
  setPinnedNodeId: (id: string | null) => void;
  diffPair: [string, string] | null;
  setDiffPair: (pair: [string, string] | null) => void;
}

export function useRunSidecar(): RunSidecarState {
  const [activeTab, setActiveTab] = useState<SidecarTab>("runs");
  const [selectedRunId, setSelectedRunIdRaw] = useState<string | null>(null);
  const [pinnedNodeId, setPinnedNodeId] = useState<string | null>(null);
  const [diffPair, setDiffPair] = useState<[string, string] | null>(null);

  function setSelectedRunId(id: string | null) {
    setSelectedRunIdRaw(id);
    setPinnedNodeId(null);
  }

  return {
    activeTab,
    setActiveTab,
    selectedRunId,
    setSelectedRunId,
    pinnedNodeId,
    setPinnedNodeId,
    diffPair,
    setDiffPair,
  };
}
