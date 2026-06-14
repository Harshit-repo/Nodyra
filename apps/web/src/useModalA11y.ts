import { useEffect, useRef, type RefObject } from "react";

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
  options: { trapFocus?: boolean; enabled?: boolean } = {},
): void {
  const { trapFocus = true, enabled = true } = options;
  // Keep the latest onClose in a ref so the effect below does NOT depend on its
  // identity. Callers usually pass an inline `() => setOpen(false)`, which is a
  // new function every render; depending on it would re-run this effect (and its
  // initial-focus grab) on every parent render — e.g. stealing focus to the
  // first focusable on every keystroke in an embedded editor.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose; // stable across renders
  useEffect(() => {
    // `enabled` lets a modal rendered inline in an always-mounted parent call
    // this hook unconditionally and only activate while the dialog is open —
    // otherwise the global Esc handler would linger when the dialog is closed.
    if (!enabled) return;
    const node = ref.current;
    const previouslyFocused = document.activeElement as HTMLElement | null;

    const initial = node?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
    (initial && initial.length ? initial[0] : node)?.focus();

    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
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
  }, [ref, trapFocus, enabled]);
}
