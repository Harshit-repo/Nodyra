import type { Edge, NodeChange } from "@xyflow/react";

import type { ConnectionCheck } from "../connectionValidation";
import type { WorkflowGraph } from "../../types";
import type {
  ClipboardResult,
  EditorStore,
  NoodleNode,
  NodeSettingsPatch,
} from "./index";

export type GraphSlice = Pick<
  EditorStore,
  | "manifests"
  | "manifestsById"
  | "nodes"
  | "edges"
  | "selectedId"
  | "dirty"
  | "envId"
  | "envName"
  | "envPackages"
  | "environmentsList"
  | "applyEnvSwitch"
  | "ndvOpenId"
  | "chatOpen"
  | "showLoopFrames"
  | "workflowId"
  | "pinned"
  | "_past"
  | "_future"
  | "devMode"
  | "setManifests"
  | "loadGraph"
  | "toGraph"
  | "onNodesChange"
  | "onEdgesChange"
  | "onConnect"
  | "addNode"
  | "insertQuickFixNode"
  | "addStickyNote"
  | "addGroupNode"
  | "autoLayout"
  | "duplicateNode"
  | "collapseToMetanode"
  | "ungroupMetanode"
  | "updateParams"
  | "replaceNodeManifest"
  | "setSelected"
  | "markClean"
  | "deleteNode"
  | "deleteSelection"
  | "toggleDisabled"
  | "autoEnableAgentDependencies"
  | "updateNodeSettings"
  | "setWorkflowId"
  | "setPinned"
  | "setPinnedFor"
  | "undo"
  | "redo"
  | "toggleDevMode"
>;

export type GraphSliceState = Pick<
  GraphSlice,
  | "manifests"
  | "manifestsById"
  | "nodes"
  | "edges"
  | "selectedId"
  | "dirty"
  | "envId"
  | "envName"
  | "envPackages"
  | "environmentsList"
  | "applyEnvSwitch"
  | "ndvOpenId"
  | "chatOpen"
  | "showLoopFrames"
  | "workflowId"
  | "pinned"
  | "_past"
  | "_future"
  | "devMode"
>;

export const graphInitialState: GraphSliceState = {
  manifests: [],
  manifestsById: {},
  nodes: [],
  edges: [],
  selectedId: null,
  dirty: false,
  envId: null,
  envName: null,
  envPackages: [],
  environmentsList: [],
  applyEnvSwitch: null,
  ndvOpenId: null,
  chatOpen: false,
  showLoopFrames: true,
  workflowId: null,
  pinned: {},
  _past: [],
  _future: [],
  devMode: false,
};

export type GraphChangeBatch = {
  nodes: NodeChange<NoodleNode>[];
  edges: Edge[];
};

export type GraphMutationResult = {
  dirty: boolean;
  check?: ConnectionCheck;
  graph?: WorkflowGraph;
  clipboard?: ClipboardResult;
  settings?: NodeSettingsPatch;
};
