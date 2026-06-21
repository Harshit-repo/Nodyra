import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useRef,
  useState,
} from "react";

import { ConfirmDialog } from "./ConfirmDialog";
import { PromptDialog } from "./PromptDialog";

export interface ConfirmOptions {
  title: string;
  body: string;
  confirmLabel?: string;
}

export interface PromptOptions {
  title: string;
  body?: string;
  label: string;
  placeholder?: string;
  confirmLabel?: string;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;
type PromptFn = (options: PromptOptions) => Promise<string | null>;

const ConfirmContext = createContext<ConfirmFn | null>(null);
const PromptContext = createContext<PromptFn | null>(null);

/**
 * App-wide confirmation and text-prompt dialogs. Renders themed, focus-trapped
 * dialogs and exposes promise-based hooks so call sites read almost exactly
 * like the native browser APIs they replace:
 *
 *   if (!(await confirm({ title, body }))) return;
 *   const name = await prompt({ title, label });
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<ConfirmOptions | null>(null);
  const resolverRef = useRef<((value: boolean) => void) | null>(null);

  const [pendingPrompt, setPendingPrompt] = useState<PromptOptions | null>(null);
  const promptResolverRef = useRef<((value: string | null) => void) | null>(null);

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

  const prompt = useCallback<PromptFn>((options) => {
    return new Promise<string | null>((resolve) => {
      promptResolverRef.current = resolve;
      setPendingPrompt(options);
    });
  }, []);

  const settlePrompt = useCallback((value: string | null) => {
    promptResolverRef.current?.(value);
    promptResolverRef.current = null;
    setPendingPrompt(null);
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      <PromptContext.Provider value={prompt}>
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
        {pendingPrompt && (
          <PromptDialog
            title={pendingPrompt.title}
            body={pendingPrompt.body}
            label={pendingPrompt.label}
            placeholder={pendingPrompt.placeholder}
            confirmLabel={pendingPrompt.confirmLabel}
            onCancel={() => settlePrompt(null)}
            onConfirm={(value) => settlePrompt(value)}
          />
        )}
      </PromptContext.Provider>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    return (options) =>
      Promise.resolve(window.confirm(`${options.title}\n\n${options.body}`));
  }
  return ctx;
}

export function usePrompt(): PromptFn {
  const ctx = useContext(PromptContext);
  if (!ctx) {
    return (options) => Promise.resolve(window.prompt(options.label));
  }
  return ctx;
}
