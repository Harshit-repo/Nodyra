import { useCallback, useEffect, useRef } from "react";

/** Schedule component-owned timeouts and cancel any that remain on unmount. */
export function useTimeout(): (callback: () => void, delayMs: number) => number {
  const timeoutIds = useRef<Set<number>>(new Set());
  const mountedRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      for (const timeoutId of timeoutIds.current) {
        window.clearTimeout(timeoutId);
      }
      timeoutIds.current.clear();
    };
  }, []);

  return useCallback((callback: () => void, delayMs: number): number => {
    if (!mountedRef.current) return -1;
    const timeoutId = window.setTimeout(() => {
      timeoutIds.current.delete(timeoutId);
      if (mountedRef.current) callback();
    }, delayMs);
    timeoutIds.current.add(timeoutId);
    return timeoutId;
  }, []);
}
