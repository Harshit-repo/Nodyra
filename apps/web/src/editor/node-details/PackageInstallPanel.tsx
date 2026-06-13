import type { SystemRequirement } from "../../types";

type EnvironmentOption = {
  id: string;
  name: string;
  backend?: string;
};

export function PackageInstallPanel({
  missingPkgs,
  envId,
  envName,
  pkgBusy,
  pkgDone,
  pkgElapsed,
  satisfyingEnvs,
  applyEnvSwitch,
  onInstall,
}: {
  missingPkgs: string[];
  envId: string | null;
  envName: string | null;
  pkgBusy: boolean;
  pkgDone: boolean;
  pkgElapsed: number;
  satisfyingEnvs: EnvironmentOption[];
  applyEnvSwitch: ((id: string) => void) | null;
  onInstall: () => void;
}) {
  if (missingPkgs.length === 0 || !envId) return null;
  return (
    <div className="ndv-missing-pkgs warn-text">
      <p>
        This node needs <strong>{missingPkgs.join(", ")}</strong>, not
        installed in <strong>{envName ?? "this environment"}</strong>.
      </p>
      <div className="ndv-missing-actions">
        <button
          type="button"
          className={`btn btn-sm${pkgDone ? " btn-success" : " btn-primary"}`}
          disabled={pkgBusy}
          onClick={onInstall}
        >
          {pkgDone ? (
            "✅ Installed!"
          ) : pkgBusy ? (
              <span className="pkg-installing">
                <span className="pkg-spinner" />
              Installing… {pkgElapsed}s
            </span>
          ) : (
            `Add to ${envName ?? "env"}`
          )}
        </button>
        {satisfyingEnvs.length > 0 && applyEnvSwitch && (
          <select
            className="field-input"
            value=""
            onChange={(event) =>
              event.target.value && applyEnvSwitch(event.target.value)
            }
          >
            <option value="">Switch environment…</option>
            {satisfyingEnvs.map((env) => (
              <option key={env.id} value={env.id}>
                {env.name}
              </option>
            ))}
          </select>
        )}
      </div>
    </div>
  );
}

export function SystemRequirementsPanel({
  requirements,
  activeEnv,
}: {
  requirements: SystemRequirement[] | undefined;
  activeEnv: EnvironmentOption | undefined;
}) {
  if (!requirements?.length) return null;
  return (
    <div className="node-details-section ndv-sysreq">
      <div className="node-details-section-title inspector-section-head">
        System dependencies
      </div>
      {requirements.map((requirement) => (
        <div key={requirement.name} className="sysreq-item">
          <div className="sysreq-name">{requirement.name}</div>
          {activeEnv?.backend === "docker" ? (
            requirement.dockerfile_hint ? (
              <div className="sysreq-hint">
                <span className="sysreq-label">Add to Dockerfile:</span>
                <code className="sysreq-code">
                  {requirement.dockerfile_hint}
                </code>
              </div>
            ) : (
              <div className="sysreq-hint">
                Must be included in your Docker image.
              </div>
            )
          ) : (
            <div className="sysreq-hint">
              <span className="sysreq-label">
                Must be installed on the server.
              </span>
              {requirement.apt && (
                <div>
                  <strong>Linux:</strong>{" "}
                  <code>apt install {requirement.apt}</code>
                </div>
              )}
              {requirement.brew && (
                <div>
                  <strong>macOS:</strong>{" "}
                  <code>brew install {requirement.brew}</code>
                </div>
              )}
              {requirement.windows && (
                <div>
                  <strong>Windows:</strong>{" "}
                  {requirement.windows.startsWith("http") ? (
                    <a
                      href={requirement.windows}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {requirement.windows}
                    </a>
                  ) : (
                    <span>{requirement.windows}</span>
                  )}
                </div>
              )}
              {requirement.note && (
                <div className="sysreq-note">{requirement.note}</div>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
