import { CaretRight } from "@phosphor-icons/react";

import { useEditor } from "./store";

export function MetanodeBreadcrumb() {
  const drillStack = useEditor((s) => s.drillStack);
  const exitToDepth = useEditor((s) => s.exitToDepth);
  if (drillStack.length === 0) return null;
  return (
    <nav className="meta-breadcrumb" aria-label="Metanode path">
      <button type="button" onClick={() => exitToDepth(0)}>Workflow</button>
      {drillStack.map((frame, i) => (
        <span key={frame.metaId} className="meta-breadcrumb-seg">
          <CaretRight size={11} weight="bold" aria-hidden />
          <button
            type="button"
            onClick={() => exitToDepth(i + 1)}
            aria-current={i === drillStack.length - 1 ? "page" : undefined}
          >
            {frame.name}
          </button>
        </span>
      ))}
    </nav>
  );
}
