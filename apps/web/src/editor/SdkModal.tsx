import { useRef, useState } from "react";

import { useModalA11y } from "../useModalA11y";
import { useTimeout } from "../hooks/useTimeout";
import { useMountedRef } from "../hooks/useMountedRef";

interface SdkModalProps {
  nodeId: string;
  manifestId: string;
  onClose: () => void;
}

export function SdkModal({ nodeId, manifestId, onClose }: SdkModalProps) {
  const [copied, setCopied] = useState(false);
  const scheduleTimeout = useTimeout();
  const mountedRef = useMountedRef();
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  const snippet = `from nodyra_sdk import run_node

result = run_node(
    "${manifestId}",
    params={},
    inputs={},
)`;

  function handleCopy() {
    void navigator.clipboard.writeText(snippet).then(() => {
      if (!mountedRef.current) return;
      setCopied(true);
      scheduleTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        className="modal"
        style={{ minWidth: 480, maxWidth: 640 }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="sdk-modal-title"
        tabIndex={-1}
      >
        <header className="modal-head">
          <h2 id="sdk-modal-title" className="modal-title">
            Python SDK snippet
          </h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <div className="modal-body">
          <p style={{ marginTop: 0, fontSize: "0.875rem", opacity: 0.7 }}>
            Node: <code>{nodeId}</code>
          </p>
          <pre
            style={{
              background: "var(--color-surface-alt, #1e1e1e)",
              color: "var(--color-text, #d4d4d4)",
              padding: "1rem",
              borderRadius: 6,
              overflowX: "auto",
              fontSize: "0.85rem",
              lineHeight: 1.5,
              margin: 0,
            }}
          >
            {snippet}
          </pre>
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
            <button
              type="button"
              className="btn-primary"
              onClick={handleCopy}
              style={{ minWidth: 80 }}
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
