import type { WorkflowGraph } from "../../types";
import type { ChildWorkflowState, EditorStore } from "./index";

export type ChildWorkflowSlice = Pick<
  EditorStore,
  | "childWorkflows"
  | "loadChildGraph"
  | "setChildWorkflowLoading"
  | "markChildClean"
  | "removeChildWorkflow"
  | "addBodyNode"
>;

export type ChildWorkflowSliceState = Pick<ChildWorkflowSlice, "childWorkflows">;

export const childWorkflowInitialState: ChildWorkflowSliceState = {
  childWorkflows: {},
};

export type ChildWorkflowSnapshot = {
  id: string;
  state: ChildWorkflowState;
  graph: WorkflowGraph;
};
