import { useEffect, type RefObject } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/**
 * Baseline accessibility for a modal dialog:
 *  - `Esc` closes it.
 *  - Focus moves into the dialog on open (first focusable, else the dialog).
 *  - `Tab`/`Shift+Tab` are trapped within the dialog (optional).
 *  - Focus returns to the previously-focused element on close.
 *
 * `ref` must point at the dialog container (give it `tabIndex={-1}` so the
 * fallback focus target works). Pass `trapFocus: false` for dialogs that embed
 * their own keyboard-driven widgets (code editors etc.) where hijacking Tab
 * would fight the inner control.
 */
export function useModalA11y(
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
  options: { trapFocus?: boolean } = {},
): void {
  const { trapFocus = true } = options;
  useEffect(() => {
    const node = ref.current;
    const previouslyFocused = document.activeElement as HTMLElement | null;

    const initial = node?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
    (initial && initial.length ? initial[0] : node)?.focus();

    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (!trapFocus || e.key !== "Tab" || !node) return;
      const items = Array.from(
        node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      ).filter((el) => !el.hidden);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      previouslyFocused?.focus?.();
    };
  }, [ref, onClose, trapFocus]);
}
