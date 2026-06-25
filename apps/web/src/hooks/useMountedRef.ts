import { useEffect, useRef } from "react";
import type { MutableRefObject } from "react";

/** Ref for guarding continuations of promises that cannot be cancelled. */
export function useMountedRef(): MutableRefObject<boolean> {
  const mountedRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  return mountedRef;
}
