import {
  CheckCircle,
  MagnifyingGlass,
  Package,
  Spinner,
  Warning,
  X,
} from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import { EmptyState } from "../EmptyState";
import { api } from "../api";
import { useToast } from "../ToastProvider";
import type { Environment, RegistryPackage } from "../types";

// ── Types ───────────────────────────────────────────────────────────────

type RegistryTab = "browse" | "installed";

// ── Component ────────────────────────────────────────────────────────────

export function NodeRegistryPage() {
  const { notify } = useToast();
  const [tab, setTab] = useState<RegistryTab>("browse");
  const [query, setQuery] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [packages, setPackages] = useState<RegistryPackage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [installedPypiPackages, setInstalledPypiPackages] = useState<
    Set<string>
  >(new Set());
  const [installModal, setInstallModal] = useState<RegistryPackage | null>(
    null,
  );
  const [installBusy, setInstallBusy] = useState(false);
  const [selectedEnvId, setSelectedEnvId] = useState("");
  const [installErrors, setInstallErrors] = useState<Record<string, string>>(
    {},
  );
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Load environments once
  useEffect(() => {
    let cancelled = false;
    api
      .listEnvironments()
      .then((data) => {
        if (!cancelled) setEnvironments(data ?? []);
      })
      .catch(() => {
        /* ignore */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Search effect: debounce 300ms
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearchQuery(query);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  // Fetch packages when search query changes
  useEffect(() => {
    if (!searchQuery && tab === "browse") {
      // Still fetch all on initial load
    }
    let cancelled = false;
    setLoading(true);
    setError("");

    api
      .searchRegistry(searchQuery || undefined)
      .then((result) => {
        if (!cancelled) {
          setPackages(result.packages ?? []);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || "Failed to search registry.");
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [searchQuery, tab]); // eslint-disable-line react-hooks/exhaustive-deps

  // Refresh installed packages list when tab switches to "installed" or after install
  useEffect(() => {
    if (tab !== "installed") return;
    api
      .listEnvironments()
      .then((data) => {
        const envs = data ?? [];
        setEnvironments(envs);
        const installed = new Set<string>();
        for (const env of envs) {
          for (const pkg of env.packages) {
            installed.add(pkg);
          }
        }
        setInstalledPypiPackages(installed);
      })
      .catch(() => {
        /* ignore */
      });
  }, [tab]);

  function handleSearch(e: React.ChangeEvent<HTMLInputElement>) {
    setQuery(e.target.value);
  }

  function handleInstallClick(pkg: RegistryPackage) {
    setInstallModal(pkg);
    setSelectedEnvId("");
    setInstallErrors({});
  }

  async function handleInstallConfirm() {
    if (!installModal || !selectedEnvId) return;
    setInstallBusy(true);
    setInstallErrors({});
    try {
      const result = await api.installRegistryPackage({
        package_id: installModal.id,
        environment_id: selectedEnvId,
      });
      notify(
        `Installing ${installModal.name}… (install ID: ${result.install_id})`,
        "info",
      );
      setInstallModal(null);
      // Refresh environments to show installed package
      const data = await api.listEnvironments();
      const envs = data ?? [];
      setEnvironments(envs);
    } catch (err) {
      setInstallErrors({
        _general: err instanceof Error ? err.message : "Install failed.",
      });
    } finally {
      setInstallBusy(false);
    }
  }

  function isPackageInstalled(pkg: RegistryPackage): boolean {
    return installedPypiPackages.has(pkg.pypi_package || pkg.id);
  }

  // Gather installed packages from environments for the "installed" tab
  const installedPackages: RegistryPackage[] = packages.filter((p) =>
    isPackageInstalled(p),
  );

  return (
    <div className="home">
      <main className="home-main">
        <div className="home-bar">
          <h1>Community Node Registry</h1>
        </div>

        {/* ── Tabs ── */}
        <div className="noodle-settings-nav" role="tablist">
          <button
            className={tab === "browse" ? "is-active" : ""}
            onClick={() => setTab("browse")}
            role="tab"
            aria-selected={tab === "browse"}
          >
            Browse
          </button>
          <button
            className={tab === "installed" ? "is-active" : ""}
            onClick={() => setTab("installed")}
            role="tab"
            aria-selected={tab === "installed"}
          >
            Installed
            {installedPackages.length > 0 && (
              <span className="home-count">{installedPackages.length}</span>
            )}
          </button>
        </div>

        {/* ── Browse tab ── */}
        {tab === "browse" && (
          <>
            {/* Search bar */}
            <div className="field-row" style={{ margin: "16px 0" }}>
              <div className="field-with-icon" style={{ flex: 1 }}>
                <MagnifyingGlass size={18} className="field-icon" />
                <input
                  ref={searchInputRef}
                  className="field-input"
                  type="search"
                  placeholder="Search community nodes…"
                  value={query}
                  onChange={handleSearch}
                  autoFocus
                />
                {query && (
                  <button
                    className="field-clear"
                    onClick={() => setQuery("")}
                    aria-label="Clear search"
                  >
                    <X size={16} />
                  </button>
                )}
              </div>
            </div>

            {/* Error state */}
            {error && (
              <p className="error-text" role="alert">
                {error}
              </p>
            )}

            {/* Loading state */}
            {loading && (
              <div className="noodle-settings-skeleton">
                <span />
                <span />
                <span />
                <p className="muted" style={{ textAlign: "center" }}>
                  Loading registry…
                </p>
              </div>
            )}

            {/* Empty state */}
            {!loading && !error && packages.length === 0 && (
              <EmptyState
                icon={<Package size={40} />}
                title="No packages found"
                description={
                  searchQuery
                    ? `No results for "${searchQuery}". Try a different search term.`
                    : "The community registry is empty or unreachable."
                }
              />
            )}

            {/* Package grid */}
            {!loading && packages.length > 0 && (
              <div className="env-grid">
                {packages.map((pkg) => (
                  <article key={pkg.id} className="env-card">
                    <div className="env-card-head">
                      <div className="env-title">
                        <h3>{pkg.name}</h3>
                        {isPackageInstalled(pkg) && (
                          <span className="badge badge--success">Installed</span>
                        )}
                      </div>
                    </div>
                    <p className="muted" style={{ margin: "8px 0" }}>
                      {pkg.description}
                    </p>
                    <div className="env-meta">
                      <span>
                        <strong>Author:</strong> {pkg.author}
                      </span>
                      <span>
                        <strong>Version:</strong> {pkg.version}
                      </span>
                    </div>
                    {pkg.nodes && pkg.nodes.length > 0 && (
                      <div className="env-packages">
                        {pkg.nodes.map((node) => (
                          <span key={node} className="chip">
                            {node}
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="env-actions">
                      {isPackageInstalled(pkg) ? (
                        <button className="btn btn-sm" disabled>
                          <CheckCircle size={16} weight="bold" /> Installed
                        </button>
                      ) : (
                        <button
                          className="btn btn-sm btn-primary"
                          onClick={() => handleInstallClick(pkg)}
                        >
                          Install
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </>
        )}

        {/* ── Installed tab ── */}
        {tab === "installed" && (
          <>
            {installedPackages.length === 0 && (
              <EmptyState
                icon={<Package size={40} />}
                title="No packages installed"
                description="Browse the registry and install packages to see them here."
                action={
                  <button
                    className="btn btn-primary"
                    onClick={() => setTab("browse")}
                  >
                    Browse Registry
                  </button>
                }
              />
            )}
            {installedPackages.length > 0 && (
              <div className="env-grid">
                {installedPackages.map((pkg) => (
                  <article key={pkg.id} className="env-card">
                    <div className="env-card-head">
                      <div className="env-title">
                        <h3>{pkg.name}</h3>
                        <span className="badge badge--success">Installed</span>
                      </div>
                    </div>
                    <p className="muted" style={{ margin: "8px 0" }}>
                      {pkg.description}
                    </p>
                    <div className="env-meta">
                      <span>
                        <strong>Author:</strong> {pkg.author}
                      </span>
                      <span>
                        <strong>Version:</strong> {pkg.version}
                      </span>
                    </div>
                    {pkg.nodes && pkg.nodes.length > 0 && (
                      <div className="env-packages">
                        {pkg.nodes.map((node) => (
                          <span key={node} className="chip">
                            {node}
                          </span>
                        ))}
                      </div>
                    )}
                  </article>
                ))}
              </div>
            )}
          </>
        )}
      </main>

      {/* ── Install Environment Selector Modal ── */}
      {installModal && (
        <div
          className="modal-overlay"
          onClick={() => !installBusy && setInstallModal(null)}
        >
          <div
            className="modal"
            ref={undefined}
            role="dialog"
            aria-modal="true"
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
          >
            <h2>Install {installModal.name}</h2>
            <p className="muted" style={{ marginBottom: 16 }}>
              Select an environment to install this package into.
            </p>

            {installErrors._general && (
              <p className="error-text" role="alert">
                <Warning size={16} /> {installErrors._general}
              </p>
            )}

            <label className="field-label">Environment</label>
            <select
              className="field-input"
              value={selectedEnvId}
              onChange={(e) => setSelectedEnvId(e.target.value)}
            >
              <option value="">-- Select environment --</option>
              {environments.map((env) => (
                <option key={env.id} value={env.id}>
                  {env.name}
                  {env.is_global ? " (global)" : ""}
                </option>
              ))}
            </select>

            {installModal.nodes && installModal.nodes.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <label className="field-label">Nodes provided</label>
                <div className="env-packages">
                  {installModal.nodes.map((node) => (
                    <span key={node} className="chip">
                      {node}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="modal-actions">
              <button
                className="btn btn-ghost"
                onClick={() => setInstallModal(null)}
                disabled={installBusy}
              >
                Cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={installBusy || !selectedEnvId}
                onClick={handleInstallConfirm}
              >
                {installBusy ? (
                  <>
                    <Spinner size={16} className="spinner" /> Installing…
                  </>
                ) : (
                  "Install"
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
