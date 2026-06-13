import type { EditorStore } from "./index";

export type RunSlice = Pick<
  EditorStore,
  | "runId"
  | "running"
  | "runStatus"
  | "runOutputs"
  | "runMeta"
  | "runError"
  | "agentActive"
  | "agentToolCalls"
  | "runHandler"
  | "setRunHandler"
  | "runFromNode"
  | "runFromTrigger"
  | "startRun"
  | "applyRunEvent"
  | "applyRunInfo"
  | "clearRun"
  | "setNodeOutput"
>;

export type RunSliceState = Pick<
  RunSlice,
  | "runId"
  | "running"
  | "runStatus"
  | "runOutputs"
  | "runMeta"
  | "runError"
  | "agentActive"
  | "agentToolCalls"
  | "runHandler"
>;

export const runInitialState: RunSliceState = {
  runId: null,
  running: false,
  runStatus: {},
  runOutputs: {},
  runMeta: {},
  runError: null,
  agentActive: {},
  agentToolCalls: {},
  runHandler: null,
};
