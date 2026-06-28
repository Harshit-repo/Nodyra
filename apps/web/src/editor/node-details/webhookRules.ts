import type { ParamSpec } from "../../types";

export const WEBHOOK_AUTH_TYPE_OPTIONS = [
  { value: "none", label: "None" },
  { value: "basic", label: "Basic Auth" },
  { value: "header", label: "Header Auth" },
  { value: "query", label: "Query Auth" },
  { value: "bearer", label: "Bearer Token" },
  { value: "jwt", label: "JWT" },
];

const WEBHOOK_CRED_BY_AUTH: Record<
  string,
  { type: string; fields: string[]; label: string }
> = {
  basic: {
    type: "http_basic",
    fields: ["username", "password"],
    label: "Basic Auth",
  },
  header: {
    type: "http_header",
    fields: ["name", "value"],
    label: "Header Auth",
  },
  query: {
    type: "http_query",
    fields: ["name", "value"],
    label: "Query Auth",
  },
  bearer: {
    type: "http_bearer",
    fields: ["token"],
    label: "Bearer Token",
  },
  jwt: {
    type: "http_jwt",
    fields: ["secret"],
    label: "JWT",
  },
};

export function webhookAuthLabel(authType: string): string {
  return (
    WEBHOOK_AUTH_TYPE_OPTIONS.find((option) => option.value === authType)?.label ??
    "None"
  );
}

// Build a synthetic ParamSpec for the auth_credentials picker whose
// credential type/fields swap based on the chosen auth_type.
export function webhookCredentialSpec(
  baseSpec: ParamSpec,
  authType: string,
): ParamSpec {
  const cfg = WEBHOOK_CRED_BY_AUTH[authType];
  if (!cfg) return baseSpec;
  return {
    ...baseSpec,
    credential: {
      type: cfg.type,
      key: "*",
      label: cfg.label,
      fields: cfg.fields,
      multi: true,
    },
  };
}

const WEBHOOK_LABEL_OVERRIDES: Record<string, string> = {
  auth_type: "Authentication",
  hmac_verification: "HMAC verification",
  ip_allowlist: "IP allowlist",
  trust_proxy: "Trust X-Forwarded-For",
  dedup: "Deduplicate",
  dedup_key: "Dedup key",
  raw_body: "Capture raw body",
  response_data: "Response data",
};

export function webhookParamLabel(
  manifestId: string,
  paramName: string,
  params: Record<string, unknown>,
): string | null {
  if (manifestId !== "webhook_trigger") return null;
  if (paramName === "auth_credentials") {
    const authType = String(params.auth_type || "none").toLowerCase();
    const label = webhookAuthLabel(authType);
    return `Credential for ${label}`;
  }
  return WEBHOOK_LABEL_OVERRIDES[paramName] ?? null;
}

// Generic optional-parameter grouping. A param's manifest `group` marks it as
// an optional "Add option" field the inspector tucks behind a chip.
export function paramGroup(spec: ParamSpec): string | null {
  return spec.group ? String(spec.group) : null;
}

export function groupActiveByValue(
  specs: ParamSpec[],
  params: Record<string, unknown>,
): boolean {
  return specs.some((spec) => {
    const value = params[spec.name];
    if (value === undefined || value === null || value === "") return false;
    return value !== spec.default;
  });
}

export function webhookHiddenParam(
  manifestId: string,
  paramName: string,
  params: Record<string, unknown>,
): boolean {
  if (manifestId !== "webhook_trigger") return false;
  if (
    paramName === "auth_username" ||
    paramName === "auth_password" ||
    paramName === "auth_header_name" ||
    paramName === "auth_header_value" ||
    paramName === "auth_query_name" ||
    paramName === "auth_query_value"
  ) {
    return true;
  }
  if (paramName === "auth_credentials") {
    const authType = String(params.auth_type || "none").toLowerCase();
    return authType === "none";
  }
  if (paramName === "auth_jwt_header") {
    return String(params.auth_type || "none").toLowerCase() !== "jwt";
  }
  if (
    paramName === "hmac_header" ||
    paramName === "hmac_algorithm" ||
    paramName === "hmac_prefix"
  ) {
    return String(params.hmac_verification || "off").toLowerCase() !== "on";
  }
  if (paramName === "trust_proxy") {
    return String(params.ip_allowlist || "").trim() === "";
  }
  if (paramName === "dedup_key") {
    return String(params.dedup || "off").toLowerCase() !== "on";
  }
  if (
    paramName === "response_data" ||
    paramName === "response_body" ||
    paramName === "response_headers"
  ) {
    const mode = String(params.response_mode || "On Received");
    if (mode !== "On Received") return true;
    if (paramName === "response_body" || paramName === "response_headers") {
      return String(params.response_data || "") !== "Custom";
    }
    return false;
  }
  return false;
}
