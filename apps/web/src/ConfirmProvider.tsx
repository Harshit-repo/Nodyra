import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useRef,
  useState,
} from "react";

import { ConfirmDialog } from "./ConfirmDialog";

export interface ConfirmOptions {
  title: string;
  body: string;
  confirmLabel?: string;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

/**
 * App-wide confirmation prompts. Renders the themed, focus-trapped
 * `ConfirmDialog` and exposes a promise-based `confirm()` so call sites read
 * almost exactly like the old `window.confirm` they replace:
 *
 *   if (!(await confirm({ title, body }))) return;
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<ConfirmOptions | null>(null);
  // The active promise resolver. Held in a ref (not state) so settling never
  // depends on a re-render and can't double-resolve under StrictMode.
  const resolverRef = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve;
      setPending(options);
    });
  }, []);

  const settle = useCallback((value: boolean) => {
    resolverRef.current?.(value);
    resolverRef.current = null;
    setPending(null);
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && (
        <ConfirmDialog
          title={pending.title}
          body={pending.body}
          confirmLabel={pending.confirmLabel}
          onCancel={() => settle(false)}
          onConfirm={() => settle(true)}
        />
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  // Fallback to the native prompt when no provider is mounted (e.g. unit tests
  // that render a single page in isolation) so behaviour stays correct.
  if (!ctx) {
    return (options) =>
      Promise.resolve(window.confirm(`${options.title}\n\n${options.body}`));
  }
  return ctx;
}
