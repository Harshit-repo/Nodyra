import { setOrgId, setToken, setUser } from "./api";
import { queryClient } from "./queries";

/** Removes every piece of client state whose meaning depends on the current
 * user or selected workspace. This prevents cached tenant data crossing an
 * authentication boundary. */
export function clearClientDataScope(): void {
  setOrgId(null);
  queryClient.clear();
}

export function clearClientSession(): void {
  clearClientDataScope();
  setToken(null);
  setUser(null);
}
