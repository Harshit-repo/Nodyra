import {
  Eye,
  EyeSlash,
  LinkSimpleHorizontal,
  Plugs,
  SealCheck,
  ShieldCheck,
  Trash,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorMessage, userFriendlyError } from "../api";
import { useConfirm } from "../ConfirmProvider";
import { useEntitlements } from "../entitlements";
import { useToast } from "../ToastProvider";
import type { SSOConfig } from "../types";

import "../settings.css";

type Protocol = "oidc" | "saml";

const PROTOCOL_LABELS: Record<Protocol, string> = {
  oidc: "OpenID Connect (OIDC)",
  saml: "SAML 2.0",
};

const PROTOCOL_DESCRIPTIONS: Record<Protocol, string> = {
  oidc: "Google Workspace, Azure AD, Okta, and any OIDC-compliant IdP. Recommended — covers 90% of providers.",
  saml: "Okta, Azure AD, PingFederate, and any SAML 2.0-compliant IdP.",
};

export default function SSOSettingsPage() {
  const { has } = useEntitlements();
  const toast = useToast();
  const confirm = useConfirm();
  const [config, setConfig] = useState<SSOConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    status: string;
    detail: string;
  } | null>(null);
  const [showSecret, setShowSecret] = useState(false);
  const [showCert, setShowCert] = useState(false);
  const [error, setError] = useState("");

  // Form fields
  const [protocol, setProtocol] = useState<Protocol>("oidc");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [discoveryUrl, setDiscoveryUrl] = useState("");
  const [idpEntityId, setIdpEntityId] = useState("");
  const [idpSsoUrl, setIdpSsoUrl] = useState("");
  const [idpCertificate, setIdpCertificate] = useState("");
  const [emailDomain, setEmailDomain] = useState("");
  const [jitProvisioning, setJitProvisioning] = useState(true);

  const ssoEnabled = has("sso");
  const hasChangesRef = useRef(false);

  useEffect(() => {
    if (!ssoEnabled) {
      setLoading(false);
      return;
    }
    api
      .getSSOConfig()
      .then((cfg) => {
        if (cfg) {
          setConfig(cfg);
          setProtocol(cfg.protocol);
          setClientId(cfg.client_id ?? "");
          setClientSecret(cfg.client_secret ?? "");
          setDiscoveryUrl(cfg.discovery_url ?? "");
          setIdpEntityId(cfg.idp_entity_id ?? "");
          setIdpSsoUrl(cfg.idp_sso_url ?? "");
          setIdpCertificate(cfg.idp_certificate ?? "");
          setEmailDomain(cfg.email_domain ?? "");
          setJitProvisioning(cfg.jit_provisioning ?? true);
        }
        setLoading(false);
      })
      .catch((err) => {
        setError(errorMessage(err));
        setLoading(false);
      });
  }, [ssoEnabled]);

  const handleProtocolChange = useCallback((p: Protocol) => {
    setProtocol(p);
    hasChangesRef.current = true;
  }, []);

  const formDirty =
    config?.protocol !== protocol ||
    (config?.client_id ?? "") !== clientId ||
    (config?.discovery_url ?? "") !== discoveryUrl ||
    (config?.idp_entity_id ?? "") !== idpEntityId ||
    (config?.idp_sso_url ?? "") !== idpSsoUrl ||
    (config?.email_domain ?? "") !== emailDomain ||
    (config?.jit_provisioning ?? true) !== jitProvisioning ||
    // Secret/certificate fields are dirty if changed (masked value != new value)
    (clientSecret !== "" && clientSecret !== config?.client_secret) ||
    (idpCertificate !== "" && idpCertificate !== config?.idp_certificate);

  async function saveConfig(): Promise<void> {
    if (!ssoEnabled || saving) return;
    setSaving(true);
    setError("");
    try {
      const payload: Partial<SSOConfig> = {
        protocol,
        client_id: clientId || undefined,
        discovery_url: discoveryUrl || undefined,
        idp_entity_id: idpEntityId || undefined,
        idp_sso_url: idpSsoUrl || undefined,
        email_domain: emailDomain || undefined,
        jit_provisioning: jitProvisioning,
      };
      if (clientSecret && clientSecret !== config?.client_secret) {
        payload.client_secret = clientSecret;
      }
      if (idpCertificate && idpCertificate !== config?.idp_certificate) {
        payload.idp_certificate = idpCertificate;
      }
      if (protocol === "saml") {
        payload.idp_certificate = idpCertificate || undefined;
      }

      await api.upsertSSOConfig(payload);
      toast.success("SSO configuration saved");
      hasChangesRef.current = false;
      // Reload config to refresh masked secrets
      const updated = await api.getSSOConfig();
      if (updated) {
        setConfig(updated);
        setClientSecret(updated.client_secret ?? "");
        setIdpCertificate(updated.idp_certificate ?? "");
      }
    } catch (err) {
      setError(userFriendlyError(err));
    } finally {
      setSaving(false);
    }
  }

  async function deleteConfig(): Promise<void> {
    const ok = await confirm({
      title: "Remove SSO configuration?",
      message:
        "This will disable SSO login for your organization. Existing sessions are not affected.",
      confirmLabel: "Remove",
      variant: "danger",
    });
    if (!ok) return;
    try {
      await api.deleteSSOConfig();
      setConfig(null);
      setProtocol("oidc");
      setClientId("");
      setClientSecret("");
      setDiscoveryUrl("");
      setIdpEntityId("");
      setIdpSsoUrl("");
      setIdpCertificate("");
      setEmailDomain("");
      setJitProvisioning(true);
      toast.success("SSO configuration removed");
    } catch (err) {
      setError(userFriendlyError(err));
    }
  }

  async function testConnection(): Promise<void> {
    if (testing) return;
    setTesting(true);
    setTestResult(null);
    setError("");
    try {
      const payload: Partial<SSOConfig> = { protocol };
      if (protocol === "oidc") {
        payload.discovery_url = discoveryUrl;
      } else {
        payload.idp_sso_url = idpSsoUrl;
        payload.idp_entity_id = idpEntityId;
      }
      const result = await api.testSSOConnection(payload);
      setTestResult(result);
    } catch (err) {
      setTestResult({ status: "error", detail: userFriendlyError(err) });
    } finally {
      setTesting(false);
    }
  }

  if (!ssoEnabled) {
    return (
      <div className="screen-center">
        <ShieldCheck size={48} />
        <h2>SSO not available</h2>
        <p className="muted">
          Single sign-on requires an Enterprise license.{" "}
          <a href="/settings/license">Upgrade your plan</a> to enable SSO.
        </p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="screen-center">
        <div className="noodle-settings-skeleton" aria-label="Loading SSO settings">
          <span />
          <span />
          <span />
        </div>
      </div>
    );
  }

  return (
    <div className="noodle-sso-settings">
      <section className="noodle-settings-card">
        <div className="noodle-settings-card-head">
          <span className="noodle-settings-card-icon" aria-hidden="true">
            <ShieldCheck size={18} />
          </span>
          <div>
            <h2>SSO Configuration</h2>
            <p>
              Configure single sign-on for your organization. Changes apply
              immediately.
            </p>
          </div>
        </div>

        <div className="noodle-settings-card-body">
          {/* Protocol selector */}
          <div className="noodle-sso-protocol-selector">
            {(Object.keys(PROTOCOL_LABELS) as Protocol[]).map((p) => (
              <label
                key={p}
                className={`noodle-sso-protocol-option${
                  protocol === p ? " is-selected" : ""
                }`}
              >
                <input
                  type="radio"
                  name="sso-protocol"
                  value={p}
                  checked={protocol === p}
                  onChange={() => handleProtocolChange(p)}
                />
                <strong>{PROTOCOL_LABELS[p]}</strong>
                <small>{PROTOCOL_DESCRIPTIONS[p]}</small>
              </label>
            ))}
          </div>

          <div className="noodle-sso-form">
            {/* OIDC fields */}
            {protocol === "oidc" && (
              <>
                <label className="login-field-label">
                  Discovery URL
                  <input
                    className="field-input"
                    type="url"
                    placeholder="https://accounts.google.com/.well-known/openid-configuration"
                    value={discoveryUrl}
                    onChange={(e) => {
                      setDiscoveryUrl(e.target.value);
                      hasChangesRef.current = true;
                    }}
                  />
                </label>
                <label className="login-field-label">
                  Client ID
                  <input
                    className="field-input"
                    type="text"
                    placeholder="OAuth 2.0 Client ID"
                    value={clientId}
                    onChange={(e) => {
                      setClientId(e.target.value);
                      hasChangesRef.current = true;
                    }}
                  />
                </label>
                <label className="login-field-label">
                  Client Secret
                  <div className="noodle-sso-secret-wrap">
                    <input
                      className="field-input"
                      type={showSecret ? "text" : "password"}
                      placeholder={
                        config?.client_secret
                          ? "Leave blank to keep current"
                          : "OAuth 2.0 Client Secret"
                      }
                      value={clientSecret}
                      onChange={(e) => {
                        setClientSecret(e.target.value);
                        hasChangesRef.current = true;
                      }}
                    />
                    <button
                      type="button"
                      className="btn btn-icon noodle-sso-toggle-vis"
                      onClick={() => setShowSecret(!showSecret)}
                      aria-label={showSecret ? "Hide secret" : "Show secret"}
                    >
                      {showSecret ? <EyeSlash size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </label>
              </>
            )}

            {/* SAML fields */}
            {protocol === "saml" && (
              <>
                <label className="login-field-label">
                  IdP Entity ID
                  <input
                    className="field-input"
                    type="text"
                    placeholder="urn:example:idp"
                    value={idpEntityId}
                    onChange={(e) => {
                      setIdpEntityId(e.target.value);
                      hasChangesRef.current = true;
                    }}
                  />
                </label>
                <label className="login-field-label">
                  IdP SSO URL
                  <input
                    className="field-input"
                    type="url"
                    placeholder="https://idp.example.com/saml/sso"
                    value={idpSsoUrl}
                    onChange={(e) => {
                      setIdpSsoUrl(e.target.value);
                      hasChangesRef.current = true;
                    }}
                  />
                </label>
                <label className="login-field-label">
                  IdP Certificate (PEM)
                  <div className="noodle-sso-secret-wrap">
                    <textarea
                      className="field-input noodle-sso-cert-input"
                      placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"
                      rows={4}
                      value={idpCertificate}
                      onChange={(e) => {
                        setIdpCertificate(e.target.value);
                        hasChangesRef.current = true;
                      }}
                    />
                    <button
                      type="button"
                      className="btn btn-icon noodle-sso-toggle-vis"
                      onClick={() => setShowCert(!showCert)}
                      aria-label={showCert ? "Hide certificate" : "Show certificate"}
                    >
                      {showCert ? <EyeSlash size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </label>
              </>
            )}

            {/* Common fields */}
            <label className="login-field-label">
              Email Domain
              <input
                className="field-input"
                type="text"
                placeholder="acme.com"
                value={emailDomain}
                onChange={(e) => {
                  setEmailDomain(e.target.value);
                  hasChangesRef.current = true;
                }}
              />
              <span className="field-help">
                Users with this email domain will see a "Sign in with SSO" option
                on the login page.
              </span>
            </label>

            <label className="login-field-checkbox">
              <input
                type="checkbox"
                checked={jitProvisioning}
                onChange={(e) => {
                  setJitProvisioning(e.target.checked);
                  hasChangesRef.current = true;
                }}
              />
              <span>
                <strong>JIT provisioning</strong>
                <br />
                <small>
                  Automatically create user accounts on first SSO login. When
                  disabled, users must be invited before they can sign in.
                </small>
              </span>
            </label>
          </div>

          {error && (
            <div className="noodle-settings-inline-error" role="alert">
              <WarningCircle size={18} aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}

          {/* Test result */}
          {testResult && (
            <div
              className={`noodle-sso-test-result ${
                testResult.status === "ok"
                  ? "noodle-sso-test-ok"
                  : "noodle-sso-test-error"
              }`}
              role="status"
            >
              {testResult.status === "ok" ? (
                <SealCheck size={18} />
              ) : (
                <XCircle size={18} />
              )}
              <span>{testResult.detail}</span>
            </div>
          )}
        </div>

        <div className="noodle-settings-card-footer">
          <div className="noodle-sso-actions">
            <button
              className="btn"
              type="button"
              onClick={() => void testConnection()}
              disabled={testing}
            >
              {testing ? (
                "Testing…"
              ) : (
                <>
                  <Plugs size={15} aria-hidden="true" />
                  Test Connection
                </>
              )}
            </button>

            {protocol === "saml" && config && (
              <a
                className="btn"
                href={api.ssoAuthorize(config.org_slug ?? "")}
                target="_blank"
                rel="noopener noreferrer"
              >
                <LinkSimpleHorizontal size={15} aria-hidden="true" />
                Download SP Metadata
              </a>
            )}

            <div className="noodle-sso-save-actions">
              {config && (
                <button
                  className="btn btn-danger-outline"
                  type="button"
                  onClick={() => void deleteConfig()}
                >
                  <Trash size={15} aria-hidden="true" />
                  Remove
                </button>
              )}
              <button
                className="btn btn-primary"
                type="button"
                onClick={() => void saveConfig()}
                disabled={saving || !formDirty}
              >
                {saving ? "Saving…" : "Save configuration"}
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
