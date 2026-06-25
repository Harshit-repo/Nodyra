export const SHELL_OVERLAY_OPEN_EVENT = "noodle:shell-overlay-open";

export function requestShellOverlayOwnership(): void {
  window.dispatchEvent(new CustomEvent(SHELL_OVERLAY_OPEN_EVENT));
}
