import type { Edge } from "@xyflow/react";

import type { ClipboardResult, EditorStore, NodyraNode } from "./index";

export type ClipboardSlice = Pick<
  EditorStore,
  | "clipboardNodeCount"
  | "_clipboard"
  | "copySelection"
  | "cutSelection"
  | "pasteSelection"
>;

export type ClipboardSliceState = Pick<
  ClipboardSlice,
  "clipboardNodeCount" | "_clipboard"
>;

export const clipboardInitialState: ClipboardSliceState = {
  clipboardNodeCount: 0,
  _clipboard: null,
};

export type ClipboardSnapshot = {
  nodes: NodyraNode[];
  edges: Edge[];
  result: ClipboardResult;
};
