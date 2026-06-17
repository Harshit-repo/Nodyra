import { useEffect, useRef } from "react";

export interface UseAutosaveOptions {
  enabled: boolean;
  dirty: boolean;
  delayMs?: number;
  onSave: () => void;
}

/**
 * Calls `onSave` once, `delayMs` after `dirty` becomes (or stays) true.
 * The timer restarts whenever `dirty`/`enabled` change, so rapid edits
 * coalesce into a single save once editing pauses.
 */
export function useAutosave({ enabled, dirty, delayMs = 1500, onSave }: UseAutosaveOptions): void {
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;

  useEffect(() => {
    if (!enabled || !dirty) return;
    const handle = window.setTimeout(() => onSaveRef.current(), delayMs);
    return () => window.clearTimeout(handle);
  }, [enabled, dirty, delayMs]);
}
