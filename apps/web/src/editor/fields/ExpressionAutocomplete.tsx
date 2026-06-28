import { useEffect, useRef, useState } from "react";

type ExprContext = {
  json?: unknown;
  inputs?: Record<string, unknown>;
  nodes?: Record<string, unknown>;
};

interface Props {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
  ctx?: ExprContext;
  dropHandlers?: {
    onDragOver?: React.DragEventHandler<HTMLInputElement>;
    onDrop?: React.DragEventHandler<HTMLInputElement>;
  };
}

export function ExpressionAutocomplete({
  value,
  onChange,
  placeholder,
  className,
  ctx,
  dropHandlers,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [sel, setSel] = useState(0);
  const [open, setOpen] = useState(false);
  const blurTimer = useRef<number | null>(null);
  useEffect(() => () => { if (blurTimer.current) clearTimeout(blurTimer.current); }, []);

  const refresh = (cursorPos: number, val: string) => {
    const beforeCursor = val.slice(0, cursorPos);
    const lastOpen = beforeCursor.lastIndexOf("{{");
    const lastClose = beforeCursor.lastIndexOf("}}");
    const insideExpression =
      lastOpen !== -1 && (lastClose === -1 || lastClose < lastOpen);
    if (!insideExpression) {
      setOpen(false);
      setSuggestions([]);
      return;
    }
    // Get the token being typed after {{
    const afterOpen = beforeCursor.slice(lastOpen + 2).trimStart();
    const partial = afterOpen.replace(/^(\{\{)\s*/, "");
    if (!partial || !partial.startsWith("$")) {
      setOpen(false);
      setSuggestions([]);
      return;
    }
    // Build suggestions from context
    const items: string[] = [];
    if (ctx?.json && typeof ctx.json === "object") {
      for (const key of Object.keys(ctx.json as Record<string, unknown>)) {
        items.push(`$json.${key}`);
      }
    }
    if (ctx?.inputs && typeof ctx.inputs === "object") {
      for (const key of Object.keys(ctx.inputs)) {
        items.push(`$input.${key}`);
      }
    }
    items.push("$workflow.id", "$run.id", "$env.NODYRA_BASE_URL");
    // Filter by partial match
    const filtered = items.filter((s) =>
      s.toLowerCase().startsWith(partial.toLowerCase()),
    );
    setSuggestions(filtered.slice(0, 8));
    setSel(0);
    setOpen(filtered.length > 0);
  };

  const accept = (suggestion: string) => {
    const input = inputRef.current;
    const cursorPos = input?.selectionStart ?? value.length;
    const beforeCursor = value.slice(0, cursorPos);
    const lastOpen = beforeCursor.lastIndexOf("{{");
    const afterCursor = value.slice(cursorPos);
    const hasClose = afterCursor.includes("}}");
    const closeIdx = hasClose ? afterCursor.indexOf("}}") + 2 : 0;

    let next: string;
    if (hasClose) {
      // Replace the expression content, preserving the closing }}
      const exprStart = lastOpen;
      next =
        value.slice(0, exprStart) +
        "{{ " +
        suggestion +
        " }}" +
        afterCursor.slice(closeIdx);
    } else {
      next =
        beforeCursor.slice(0, lastOpen) +
        "{{ " +
        suggestion +
        " }}" +
        afterCursor;
    }
    onChange(next);
    setOpen(false);
  };

  return (
    <div className="expr-ac-wrap">
      <input
        ref={inputRef}
        className={className}
        type="text"
        placeholder={placeholder}
        value={value}
        role="combobox"
        aria-expanded={open}
        aria-controls="expr-ac-listbox"
        aria-activedescendant={open && suggestions.length > 0 ? `expr-ac-${sel}` : undefined}
        onChange={(e) => {
          onChange(e.target.value);
          refresh(
            e.target.selectionStart ?? e.target.value.length,
            e.target.value,
          );
        }}
        onKeyDown={(e) => {
          if (!open || suggestions.length === 0) return;
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setSel((s) => (s + 1) % suggestions.length);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setSel(
              (s) => (s - 1 + suggestions.length) % suggestions.length,
            );
          } else if (e.key === "Enter" || e.key === "Tab") {
            e.preventDefault();
            accept(suggestions[sel]);
          } else if (e.key === "Escape") {
            e.preventDefault();
            setOpen(false);
          }
        }}
        onKeyUp={(e) => {
          if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
            refresh(
              e.currentTarget.selectionStart ?? value.length,
              value,
            );
          }
        }}
        onBlur={() => { blurTimer.current = window.setTimeout(() => setOpen(false), 120); }}
        {...dropHandlers}
      />
      {open && suggestions.length > 0 && (
        <ul
          className="expr-ac"
          id="expr-ac-listbox"
          role="listbox"
          aria-label="Expression suggestions"
        >
          {suggestions.map((s, i) => (
            <li
              key={s}
              id={`expr-ac-${i}`}
              role="option"
              aria-selected={i === sel}
              className={`expr-ac-item${i === sel ? " sel" : ""}`}
              onMouseDown={(e) => {
                e.preventDefault();
                accept(s);
              }}
              onMouseEnter={() => setSel(i)}
            >
              {s}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
