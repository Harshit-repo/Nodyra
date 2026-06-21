import { useRef, useState } from "react";

import { useModalA11y } from "./useModalA11y";

interface PromptDialogProps {
  title: string;
  body?: string;
  label: string;
  placeholder?: string;
  confirmLabel?: string;
  onCancel: () => void;
  onConfirm: (value: string) => void;
}

export function PromptDialog({
  title,
  body,
  label,
  placeholder,
  confirmLabel = "Confirm",
  onCancel,
  onConfirm,
}: PromptDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [value, setValue] = useState("");
  useModalA11y(dialogRef, onCancel);

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div
        ref={dialogRef}
        className="modal confirm-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="prompt-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="prompt-title">{title}</h2>
        {body && <p className="muted">{body}</p>}
        <div className="prompt-field">
          <label htmlFor="prompt-input">{label}</label>
          <input
            id="prompt-input"
            type="text"
            value={value}
            placeholder={placeholder}
            autoFocus
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                onConfirm(value);
              }
            }}
          />
        </div>
        <div className="modal-actions">
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onConfirm(value)}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
