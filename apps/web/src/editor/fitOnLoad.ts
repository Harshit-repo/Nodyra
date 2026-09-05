/** State the canvas keeps about which workflow it has already fitted. */
export interface FitState {
  /** The workflow currently open, or null before one is selected. */
  workflowId: string | null;
  /** The workflow the view was last fitted for, or null if never. */
  fittedFor: string | null;
}

/**
 * Should the canvas fit the view now?
 *
 * `<ReactFlow fitView>` fits on the render where it mounts. The graph is
 * fetched after that, so that fit runs while React Flow knows about one node
 * or none. With `maxZoom={2}` the viewport is then left at 2x centred on that
 * single node, and `onlyRenderVisibleElements` culls everything else - opening
 * a three-node workflow showed two of them, and the fit-view control could not
 * recover it because React Flow considered the view already fitted.
 *
 * `nodesInitialized` is the signal that matters: React Flow has to have
 * *measured* the nodes before it can compute bounds for them. Fitting merely
 * when the node count changes is too early - the measurement happens after the
 * commit, so the fit still sees one node.
 *
 * Fit once per workflow. Not on later changes: re-fitting whenever the node
 * count moves would pull the viewport out from under someone every time they
 * add a node.
 */
export function shouldFitOnLoad(
  state: FitState,
  workflowId: string | null,
  nodeCount: number,
  nodesInitialized: boolean,
): boolean {
  if (!workflowId || nodeCount <= 0 || !nodesInitialized) return false;
  return state.fittedFor !== workflowId;
}
