import { Suspense, lazy, useEffect, useRef, useState } from "react";

/**
 * Lazy-loaded Monaco code editor for Python nodes.
 *
 * Monaco is ~5 MB gzipped — we only load it when a code node is actually opened
 * for editing. The Suspense fallback shows a styled textarea so the user isn't
 * staring at a blank panel while the editor loads.
 */

// ── lazy load ──────────────────────────────────────────────────────────────

const MonacoReact = lazy(() => import("@monaco-editor/react"));

// ── skeleton / fallback ────────────────────────────────────────────────────

function CodeEditorSkeleton({ value }: { value: string }) {
  const lineCount = (value.match(/\n/g) ?? []).length + 1;
  return (
    <textarea
      className="field-code monaco-skeleton"
      value={value}
      readOnly
      rows={Math.min(lineCount, 30)}
      aria-label="Loading code editor…"
      style={{ opacity: 0.7, pointerEvents: "none" }}
    />
  );
}

// ── props ──────────────────────────────────────────────────────────────────

export interface MonacoEditorProps {
  /** Python source code */
  value: string;
  /** Called on every change */
  onChange?: (value: string) => void;
  /** Read-only mode (e.g. viewing built-in source) */
  readOnly?: boolean;
  /** Minimum number of lines to show */
  minLines?: number;
  /** Maximum number of lines before scroll */
  maxLines?: number;
  /** Placeholder when empty */
  placeholder?: string;
  /** aria-label */
  label?: string;
  /** Inline diagnostics from lint / validation */
  diagnostics?: Array<{
    line: number;
    column: number;
    message: string;
    severity: "error" | "warning" | "info";
  }>;
}

// ── component ──────────────────────────────────────────────────────────────

export function MonacoEditor({
  value,
  onChange,
  readOnly = false,
  minLines = 6,
  maxLines = 30,
  placeholder,
  label = "Python code editor",
  diagnostics = [],
}: MonacoEditorProps) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const editorRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const monacoRef = useRef<any>(null);
  const [ready, setReady] = useState(false);
  // Holds the disposable returned by registerCompletionItemProvider so we can
  // clean it up on unmount — Monaco is a global singleton and accumulates
  // duplicate providers across remounts if the disposable is not released.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const completionDisposableRef = useRef<{ dispose: () => void } | null>(null);

  useEffect(() => () => { completionDisposableRef.current?.dispose(); }, []);

  // ── theme sync ────────────────────────────────────────────────────────
  // Monaco ships its own themes; we pick the closest match to Nodyra's
  // current palette so the editor doesn't flash light-on-dark.

  const isLight =
    typeof document !== "undefined" &&
    document.documentElement.dataset.theme === "light";

  // ── mount handler ─────────────────────────────────────────────────────

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function handleMount(editor: any, monaco: any) {
    editorRef.current = editor;
    monacoRef.current = monaco;
    setReady(true);

    // Register Nodyra SDK completions; store the disposable so the provider
    // is removed when this editor instance unmounts (prevents accumulation).
    completionDisposableRef.current = monaco.languages.registerCompletionItemProvider("python", {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      provideCompletionItems: (model: any, position: any) => {
        const word = model.getWordUntilPosition(position);
        const range = {
          startLineNumber: position.lineNumber,
          endLineNumber: position.lineNumber,
          startColumn: word.startColumn,
          endColumn: word.endColumn,
        };

        const suggestions: import("monaco-editor").languages.CompletionItem[] = [
          {
            label: "node_input",
            kind: monaco.languages.CompletionItemKind.Function,
            insertText: 'node_input("${1:port_name}")',
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            documentation: "Read input from the named port",
            range,
          },
          {
            label: "node_output",
            kind: monaco.languages.CompletionItemKind.Function,
            insertText: 'node_output(main={"${1:key}": ${2:value}})',
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            documentation: "Set the node's output data",
            range,
          },
          {
            label: "NodeOutput",
            kind: monaco.languages.CompletionItemKind.Class,
            insertText: "NodeOutput",
            documentation: "Typed output container for structured node results",
            range,
          },
          {
            label: "credential",
            kind: monaco.languages.CompletionItemKind.Function,
            insertText: 'credential("${1:name}")',
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            documentation: "Access a stored credential by name",
            range,
          },
          {
            label: "__nodyra_credential__",
            kind: monaco.languages.CompletionItemKind.Property,
            insertText: "__nodyra_credential__",
            documentation: "Credential reference marker (internal)",
            range,
          },
          {
            label: "ctx",
            kind: monaco.languages.CompletionItemKind.Variable,
            insertText: "ctx",
            documentation: "Execution context (run ID, workflow ID, node ID, env)",
            range,
          },
          {
            label: "log",
            kind: monaco.languages.CompletionItemKind.Function,
            insertText: "log.${1:info}(${2:message})",
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            documentation: "Log a message to the node's execution log (info/warning/error)",
            range,
          },
        ];

        return { suggestions };
      },
    });
  }

  // ── apply diagnostics ─────────────────────────────────────────────────

  useEffect(() => {
    if (!ready || !monacoRef.current || !editorRef.current) return;
    const monaco = monacoRef.current;
    const editor = editorRef.current;
    const model = editor.getModel();
    if (!model) return;

    const markers = diagnostics.map((d) => ({
      severity:
        d.severity === "error"
          ? monaco.MarkerSeverity.Error
          : d.severity === "warning"
            ? monaco.MarkerSeverity.Warning
            : monaco.MarkerSeverity.Info,
      startLineNumber: d.line,
      startColumn: d.column,
      endLineNumber: d.line,
      endColumn: d.column + 1,
      message: d.message,
    }));

    monaco.editor.setModelMarkers(model, "nodyra-lint", markers);
  }, [diagnostics, ready]);

  // ── auto-height ───────────────────────────────────────────────────────

  const lineCount = (value.match(/\n/g) ?? []).length + 1;
  const height = Math.max(minLines, Math.min(maxLines, lineCount)) * 19 + 16;

  return (
    <Suspense fallback={<CodeEditorSkeleton value={value} />}>
      <div className="monaco-editor-wrap" style={{ minHeight: height }}>
        <MonacoReact
          height={height}
          language="python"
          theme={isLight ? "vs" : "vs-dark"}
          value={value}
          onChange={(v) => onChange?.(v ?? "")}
          onMount={handleMount}
          options={{
            readOnly,
            minimap: { enabled: false },
            lineNumbers: "on",
            renderLineHighlight: "line",
            scrollBeyondLastLine: false,
            fontSize: 13,
            fontFamily: "var(--font-mono, 'IBM Plex Mono', monospace)",
            tabSize: 4,
            insertSpaces: true,
            wordWrap: "on",
            padding: { top: 10, bottom: 10 },
            folding: true,
            suggest: { showWords: true, showSnippets: true },
            bracketPairColorization: { enabled: true },
            autoClosingBrackets: "always",
            autoClosingQuotes: "always",
            matchBrackets: "always",
            glyphMargin: false,
            lineDecorationsWidth: 8,
            overviewRulerBorder: false,
            hideCursorInOverviewRuler: true,
            overviewRulerLanes: 0,
            scrollbar: {
              vertical: "auto",
              horizontal: "auto",
              verticalScrollbarSize: 8,
              horizontalScrollbarSize: 8,
            },
            ...(placeholder ? { placeholder } : {}),
          }}
          loading={<CodeEditorSkeleton value={value} />}
          aria-label={label}
        />
      </div>
    </Suspense>
  );
}
