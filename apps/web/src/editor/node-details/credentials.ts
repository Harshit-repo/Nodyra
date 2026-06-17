import type { Credential, CredentialParamSpec } from "../../types";

// Catch-all credential types accepted by any picker regardless of declared type.
const GENERIC_CRED_TYPES = ["generic", "apiKey", "oauth2"];

/** Decide whether a stored credential is eligible for a node param's picker.
 *
 * A credential qualifies when its type lines up with the param's declared
 * credential type, or it is a generic catch-all, and it carries the field(s)
 * the node will read.
 *
 * Multi-field credentials, the single "Credentials" picker where `key === "*"`
 * legitimately store only a subset of declared fields. Matching at least one
 * declared field keeps partially filled provider credentials selectable.
 */
export function credentialMatchesParam(
  cred: Pick<Credential, "type" | "keys">,
  meta: CredentialParamSpec | null | undefined,
  targetKey: string,
): boolean {
  if (
    meta?.type &&
    cred.type !== meta.type &&
    !GENERIC_CRED_TYPES.includes(cred.type)
  ) {
    return false;
  }
  if (meta?.multi) {
    const fields = meta.fields?.length ? meta.fields : [meta.key];
    return fields.some((field) => cred.keys.includes(field));
  }
  return cred.keys.includes(targetKey);
}
