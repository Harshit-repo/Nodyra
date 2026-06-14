import { useRef, type ReactNode } from "react";
import { useModalA11y } from "../useModalA11y";

/**
 * Modal shell with baseline a11y (Esc, focus trap + return, dialog ARIA).
 * Mounts only while open, so `useModalA11y`'s global Esc handler never
 * lingers when the dialog is closed.
 */
export function A11yModal({
  className,
  titleId,
  title,
  onClose,
  closeDisabled = false,
  children,
}: {
  className: string;
  titleId: string;
  title: string;
  onClose: () => void;
  closeDisabled?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useModalA11y(ref, onClose);
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className={`modal ${className}`}
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2 id={titleId}>{title}</h2>
          <button
            className="btn btn-sm btn-ghost"
            onClick={onClose}
            disabled={closeDisabled}
            aria-label="Close"
          >
            ✕
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
