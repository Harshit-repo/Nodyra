import { createContext, useContext, type ReactNode } from "react";

import type { AuthState } from "./types";

const AuthRuntimeContext = createContext<AuthState | null>(null);
const SignOutContext = createContext<(() => void) | null>(null);

export function AuthRuntimeProvider({
  auth,
  signOut,
  children,
}: {
  auth: AuthState;
  signOut: (() => void) | null;
  children: ReactNode;
}) {
  return (
    <AuthRuntimeContext.Provider value={auth}>
      <SignOutContext.Provider value={signOut}>
        {children}
      </SignOutContext.Provider>
    </AuthRuntimeContext.Provider>
  );
}

/** Live server-authenticated identity. Components may fall back to the legacy
 * cache only when rendered outside App (primarily isolated unit tests). */
export function useRuntimeAuthState(): AuthState | null {
  return useContext(AuthRuntimeContext);
}

/** Sign-out callback injected by App. Returns null outside the auth gate. */
export function useSignOutCallback(): (() => void) | null {
  return useContext(SignOutContext);
}
