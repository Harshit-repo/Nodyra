import { useEffect, useRef, useState } from "react";

export interface OverflowItem {
  id: string;
  label: string;
  danger?: boolean;
  dividerBefore?: boolean;
  onSelect: () => void;
}

export function OverflowMenu({ items }: { items: (OverflowItem | null | false)[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const visible = items.filter((i): i is OverflowItem => Boolean(i));

  return (
    <div className="overflow-menu" ref={ref}>
      <button
        type="button"
        className="btn btn-icon overflow-trigger"
        aria-label="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        ⋯
      </button>
      {open && (
        <div className="overflow-dropdown" role="menu">
          {visible.map((item) => (
            <div key={item.id}>
              {item.dividerBefore && <div className="overflow-sep" />}
              <button
                type="button"
                role="menuitem"
                className={`overflow-item${item.danger ? " is-danger" : ""}`}
                onClick={() => {
                  setOpen(false);
                  item.onSelect();
                }}
              >
                {item.label}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
