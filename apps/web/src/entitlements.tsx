import { createContext, useContext, type ReactNode } from "react";

import type { AuthState } from "./types";

export interface Entitlements {
  edition: string;
  has: (feature: string) => boolean;
  /** True when a finite cap (non-zero) has been reached. 0 = unlimited. */
  atLimit: (kind: string, count: number) => boolean;
  limit: (kind: string) => number;
  notice: string | null;
}

export interface EntitlementSource {
  edition?: string;
  entitlements?: string[];
  limits?: Record<string, number>;
  license_notice?: string | null;
}

export function computeEntitlements(s: EntitlementSource): Entitlements {
  const entitlements = s.entitlements ?? [];
  const limits = s.limits ?? {};
  return {
    edition: s.edition ?? "community",
    has: (feature: string) => entitlements.includes(feature),
    atLimit: (kind: string, count: number) => {
      const cap = limits[kind] ?? 0;
      return cap !== 0 && count >= cap;
    },
    limit: (kind: string) => limits[kind] ?? 0,
    notice: s.license_notice ?? null,
  };
}

const EntitlementsContext = createContext<Entitlements>(
  computeEntitlements({}),
);

export function EntitlementsProvider({
  auth,
  children,
}: {
  auth: AuthState | null;
  children: ReactNode;
}) {
  return (
    <EntitlementsContext.Provider value={computeEntitlements(auth ?? {})}>
      {children}
    </EntitlementsContext.Provider>
  );
}

export function useEntitlements(): Entitlements {
  return useContext(EntitlementsContext);
}
