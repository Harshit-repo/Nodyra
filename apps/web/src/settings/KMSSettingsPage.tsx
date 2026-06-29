import { Key, ShieldCheck, Wrench } from "@phosphor-icons/react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { useToast } from "../ToastProvider";

type KmsProvider = "env" | "vault" | "aws" | "gcp";

const PROVIDER_LABELS: Record<KmsProvider, string> = {
  env: "Local (Environment)",
  vault: "HashiCorp Vault",
  aws: "AWS KMS",
  gcp: "Google Cloud KMS",
};

const PROVIDER_DESCRIPTIONS: Record<KmsProvider, string> = {
  env: "Master KEK derived from SECRET_KEY. No external dependencies.",
  vault: "HashiCorp Vault Transit Engine with a static token.",
  aws: "AWS Key Management Service. Requires IAM credentials in the environment.",
  gcp: "Google Cloud KMS. Requires Application Default Credentials.",
};

export function KMSSettingsPage() {
  const { notify } = useToast();
  const [provider, setProvider] = useState<KmsProvider>("env");
  const [vaultUrl, setVaultUrl] = useState("");
  const [vaultToken, setVaultToken] = useState("");
  const [vaultMount, setVaultMount] = useState("transit");
  const [vaultKey, setVaultKey] = useState("noodle-master");
  const [awsKeyId, setAwsKeyId] = useState("");
  const [awsRegion, setAwsRegion] = useState("us-east-1");
  const [gcpKeyName, setGcpKeyName] = useState("");
  const [busy, setBusy] = useState(false);
  const [healthStatus, setHealthStatus] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);

  async function testConnection() {
    setTesting(true);
    setHealthStatus(null);
    try {
      const data = await api.kmsHealth();
      if (data.status === "ok") {
        setHealthStatus("ok");
        notify("KMS provider is healthy.", "success");
      } else {
        setHealthStatus("error");
        notify("KMS provider health check failed.", "error");
      }
    } catch (err: unknown) {
      setHealthStatus("error");
      const msg = err instanceof Error ? err.message : "Unknown error";
      notify(`KMS health check: ${msg}`, "error");
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="home noodle-settings-page">
      <main className="home-main">
        <div className="home-bar noodle-settings-heading">
          <div>
            <p className="noodle-settings-eyebrow">Enterprise</p>
            <h1>External KMS</h1>
            <p className="muted">
              Configure an external key management service for master KEK
              encryption. Credential data is encrypted with per-org KEKs, which
              are themselves wrapped by the master KEK managed by the configured
              provider.{" "}
              <Link to="/settings">Back to settings</Link>
            </p>
          </div>
        </div>

        <div className="noodle-settings-card">
          <div className="noodle-settings-card-head">
            <div className="noodle-settings-card-icon">
              <Key size={18} weight="bold" />
            </div>
            <div>
              <h2>Provider</h2>
              <p>
                Choose which KMS provider to use for master KEK encryption.
                Changing the provider requires running the migration script
                outside this UI before updating the config.
              </p>
            </div>
          </div>

          <div className="noodle-settings-card-body">
            <fieldset className="noodle-settings-fieldset">
              <legend>Select provider</legend>
              <div className="noodle-theme-grid">
                {(Object.keys(PROVIDER_LABELS) as KmsProvider[]).map((p) => (
                  <label
                    key={p}
                    className={`noodle-theme-option ${provider === p ? "is-selected" : ""}`}
                  >
                    <input
                      type="radio"
                      name="kms-provider"
                      value={p}
                      checked={provider === p}
                      onChange={() => setProvider(p)}
                    />
                    <strong>{PROVIDER_LABELS[p]}</strong>
                    <small>{PROVIDER_DESCRIPTIONS[p]}</small>
                  </label>
                ))}
              </div>
            </fieldset>

            {/* Vault config */}
            {provider === "vault" && (
              <fieldset className="noodle-settings-fieldset">
                <legend>HashiCorp Vault configuration</legend>
                <div className="noodle-settings-form-grid">
                  <div className="noodle-settings-field noodle-settings-field-wide">
                    <label className="noodle-settings-label">Vault URL</label>
                    <input
                      className="field-input"
                      type="text"
                      placeholder="http://vault:8200"
                      value={vaultUrl}
                      onChange={(e) => setVaultUrl(e.target.value)}
                    />
                    <small>
                      Base URL of the Vault server (e.g.
                      http://vault:8200)
                    </small>
                  </div>
                  <div className="noodle-settings-field noodle-settings-field-wide">
                    <label className="noodle-settings-label">
                      Vault Token
                    </label>
                    <div className="noodle-settings-input-wrap">
                      <input
                        className="field-input"
                        type="password"
                        placeholder="hvs.…"
                        value={vaultToken}
                        onChange={(e) => setVaultToken(e.target.value)}
                      />
                    </div>
                    <small>
                      Static Vault token with write capability on the Transit
                      mount. For production, use AppRole or Kubernetes auth.
                    </small>
                  </div>
                  <div className="noodle-settings-field">
                    <label className="noodle-settings-label">
                      Transit Mount
                    </label>
                    <input
                      className="field-input"
                      type="text"
                      value={vaultMount}
                      onChange={(e) => setVaultMount(e.target.value)}
                    />
                  </div>
                  <div className="noodle-settings-field">
                    <label className="noodle-settings-label">Key Name</label>
                    <input
                      className="field-input"
                      type="text"
                      value={vaultKey}
                      onChange={(e) => setVaultKey(e.target.value)}
                    />
                  </div>
                </div>
              </fieldset>
            )}

            {/* AWS KMS config */}
            {provider === "aws" && (
              <fieldset className="noodle-settings-fieldset">
                <legend>AWS KMS configuration</legend>
                <div className="noodle-settings-form-grid">
                  <div className="noodle-settings-field noodle-settings-field-wide">
                    <label className="noodle-settings-label">Key ID / ARN</label>
                    <input
                      className="field-input"
                      type="text"
                      placeholder="arn:aws:kms:us-east-1:…:key/… or alias/…"
                      value={awsKeyId}
                      onChange={(e) => setAwsKeyId(e.target.value)}
                    />
                    <small>
                      KMS key identifier: key ID, full ARN, alias name, or alias
                      ARN.
                    </small>
                  </div>
                  <div className="noodle-settings-field">
                    <label className="noodle-settings-label">Region</label>
                    <input
                      className="field-input"
                      type="text"
                      value={awsRegion}
                      onChange={(e) => setAwsRegion(e.target.value)}
                    />
                    <small>AWS region (default us-east-1).</small>
                  </div>
                </div>
                <div className="noodle-settings-restart-note">
                  <Wrench size={16} />
                  <span>
                    AWS credentials are picked up from the environment (env
                    vars, IAM role, or credential file). No secrets need to be
                    stored in Noodle settings.
                  </span>
                </div>
              </fieldset>
            )}

            {/* GCP KMS config */}
            {provider === "gcp" && (
              <fieldset className="noodle-settings-fieldset">
                <legend>Google Cloud KMS configuration</legend>
                <div className="noodle-settings-form-grid">
                  <div className="noodle-settings-field noodle-settings-field-wide">
                    <label className="noodle-settings-label">
                      Key Resource Name
                    </label>
                    <input
                      className="field-input"
                      type="text"
                      placeholder="projects/my-project/locations/global/keyRings/my-ring/cryptoKeys/my-key"
                      value={gcpKeyName}
                      onChange={(e) => setGcpKeyName(e.target.value)}
                    />
                    <small>
                      Full resource name of a symmetric CryptoKey. Do NOT
                      include a version suffix — let GCP manage automatic key
                      rotation.
                    </small>
                  </div>
                </div>
                <div className="noodle-settings-restart-note">
                  <Wrench size={16} />
                  <span>
                    GCP credentials are picked up from Application Default
                    Credentials (ADC). No secrets need to be stored in Noodle
                    settings.
                  </span>
                </div>
              </fieldset>
            )}

            {/* Env — no extra config */}
            {provider === "env" && (
              <div className="noodle-settings-restart-note" style={{ marginTop: 12 }}>
                <ShieldCheck size={16} />
                <span>
                  The local environment provider uses SECRET_KEY as the root of
                  trust. No additional configuration is needed. This is the
                  default for existing deployments.
                </span>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="noodle-settings-card-footer">
            <div className="noodle-settings-footer-status">
              {healthStatus === "ok" && (
                <span className="noodle-settings-saved">
                  <ShieldCheck size={14} /> Connection successful
                </span>
              )}
              {healthStatus === "error" && (
                <span className="noodle-settings-inline-error">
                  <span>Health check failed</span>
                </span>
              )}
            </div>
            <button
              className="btn"
              disabled={testing || busy}
              onClick={testConnection}
            >
              {testing ? "Testing…" : "Test Connection"}
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
