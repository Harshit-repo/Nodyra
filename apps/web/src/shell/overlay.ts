export const SHELL_OVERLAY_OPEN_EVENT = "nodyra:shell-overlay-open";

export function requestShellOverlayOwnership(): void {
  window.dispatchEvent(new CustomEvent(SHELL_OVERLAY_OPEN_EVENT));
}
