import { useState } from "react";

import { api, errorMessage } from "./api";
import { useConfirm } from "./ConfirmProvider";
import {
  useCreateGithubRepoMutation,
  useDeleteGithubSyncConfigMutation,
  useGithubSyncConfig,
  useUpsertGithubSyncConfigMutation,
  useValidateGithubRepoMutation,
} from "./queries";
import { useToast } from "./ToastProvider";
import type { GithubRepoValidation } from "./types";

export function GitHubSyncSettings() {
  const { data: config, isLoading } = useGithubSyncConfig();
  const upsert = useUpsertGithubSyncConfigMutation();
  const remove = useDeleteGithubSyncConfigMutation();
  const validateRepo = useValidateGithubRepoMutation();
  const createRepo = useCreateGithubRepoMutation();
  const { notify } = useToast();
  const confirm = useConfirm();

  const [repo, setRepo] = useState("");
  const [basePath, setBasePath] = useState("workflows/");
  const [mainBranch, setMainBranch] = useState("main");
  const [secret, setSecret] = useState<string | null>(null);
  const [validation, setValidation] = useState<GithubRepoValidation | null>(null);

  if (isLoading) return <div className="settings-loading">Loading…</div>;

  async function handleConnect(e: React.FormEvent) {
    e.preventDefault();
    setValidation(null);
    try {
      await upsert.mutateAsync({ repo, base_path: basePath, main_branch: mainBranch });
      // Auto-validate immediately after saving
      const result = await validateRepo.mutateAsync();
      setValidation(result);
      if (result.accessible) {
        notify("GitHub sync configured and repository verified.", "success");
      } else {
        notify("GitHub sync saved — repository not yet accessible.", "info");
      }
    } catch (err) {
      notify(`Failed to save GitHub sync config. ${errorMessage(err)}`, "error");
    }
  }

  async function handleRevalidate() {
    try {
      const result = await validateRepo.mutateAsync();
      setValidation(result);
    } catch (err) {
      notify(`Validation failed. ${errorMessage(err)}`, "error");
    }
  }

  async function handleCreateRepo() {
    try {
      const result = await createRepo.mutateAsync({ private: true });
      notify(`Repository created: ${result.url}`, "success");
      // Re-validate to confirm it's now accessible
      const v = await validateRepo.mutateAsync();
      setValidation(v);
    } catch (err) {
      notify(`Failed to create repository. ${errorMessage(err)}`, "error");
    }
  }

  async function handleRevealSecret() {
    try {
      const { webhook_secret } = await api.getGithubWebhookSecret();
      setSecret(webhook_secret);
    } catch (err) {
      notify(`Failed to retrieve webhook secret. ${errorMessage(err)}`, "error");
    }
  }

  async function handleDisconnect() {
    const ok = await confirm({
      title: "Disconnect GitHub sync?",
      body: "Synced files in GitHub will not be deleted.",
      confirmLabel: "Disconnect",
    });
    if (!ok) return;
    try {
      await remove.mutateAsync();
      setValidation(null);
      notify("GitHub sync disconnected.", "success");
    } catch (err) {
      notify(`Failed to disconnect. ${errorMessage(err)}`, "error");
    }
  }

  if (config) {
    return (
      <section className="security-create" style={{ marginTop: 24 }}>
        <div>
          <h2>GitHub Sync</h2>
          <p className="muted">
            Connected to <strong>{config.repo}</strong>. Workflows sync to{" "}
            <code>{config.base_path}</code> on branch <code>{config.main_branch}</code>.
          </p>
        </div>

        <RepoValidationBadge
          validation={validation}
          isValidating={validateRepo.isPending}
          onRevalidate={() => void handleRevalidate()}
          onCreateRepo={() => void handleCreateRepo()}
          isCreating={createRepo.isPending}
        />

        <div className="field-label">
          <span className="muted">Webhook URL</span>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <code style={{ flex: 1, wordBreak: "break-all" }}>{config.webhook_url}</code>
            <button
              type="button"
              className="btn"
              onClick={() => void navigator.clipboard.writeText(config.webhook_url)}
            >
              Copy
            </button>
          </div>
          <p className="muted" style={{ fontSize: "0.8em", marginTop: 4 }}>
            Paste this into your GitHub repo → Settings → Webhooks.
          </p>
        </div>

        <div className="field-label">
          <span className="muted">Webhook Secret</span>
          {secret ? (
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <code style={{ flex: 1, wordBreak: "break-all" }}>{secret}</code>
              <button
                type="button"
                className="btn"
                onClick={() => void navigator.clipboard.writeText(secret)}
              >
                Copy
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="btn"
              onClick={() => void handleRevealSecret()}
            >
              Reveal secret
            </button>
          )}
        </div>

        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => void handleDisconnect()}
          disabled={remove.isPending}
        >
          {remove.isPending ? "Disconnecting…" : "Disconnect GitHub"}
        </button>
      </section>
    );
  }

  return (
    <section className="security-create" style={{ marginTop: 24 }}>
      <div>
        <h2>GitHub Sync</h2>
        <p className="muted">
          Connect a GitHub repository to sync your workflows as Python code.
        </p>
      </div>
      <form onSubmit={(e) => void handleConnect(e)}>
        <label className="field-label">
          <span className="muted">Repository</span>
          <input
            className="field-input"
            placeholder="owner/repo"
            value={repo}
            onChange={(e) => {
              setRepo(e.target.value);
              setValidation(null);
            }}
            required
          />
        </label>
        <label className="field-label">
          <span className="muted">Base path</span>
          <input
            className="field-input"
            value={basePath}
            onChange={(e) => setBasePath(e.target.value)}
          />
        </label>
        <label className="field-label">
          <span className="muted">Main branch</span>
          <input
            className="field-input"
            value={mainBranch}
            onChange={(e) => setMainBranch(e.target.value)}
          />
        </label>

        {validation && (
          <RepoValidationBadge
            validation={validation}
            isValidating={validateRepo.isPending}
            onRevalidate={() => void handleRevalidate()}
            onCreateRepo={() => void handleCreateRepo()}
            isCreating={createRepo.isPending}
          />
        )}

        <button
          type="submit"
          className="btn btn-primary"
          disabled={upsert.isPending || validateRepo.isPending}
        >
          {upsert.isPending ? "Saving…" : validateRepo.isPending ? "Verifying…" : "Connect GitHub"}
        </button>
      </form>
    </section>
  );
}

interface RepoValidationBadgeProps {
  validation: GithubRepoValidation | null;
  isValidating: boolean;
  onRevalidate: () => void;
  onCreateRepo: () => void;
  isCreating: boolean;
}

function RepoValidationBadge({
  validation,
  isValidating,
  onRevalidate,
  onCreateRepo,
  isCreating,
}: RepoValidationBadgeProps) {
  if (isValidating) {
    return (
      <p className="muted" style={{ fontSize: "0.9em" }}>
        Checking repository access…
      </p>
    );
  }
  if (!validation) return null;

  if (validation.accessible) {
    return (
      <p style={{ color: "var(--color-success, green)", fontSize: "0.9em" }}>
        ✓ Repository accessible
        {validation.private != null && (
          <span className="muted"> — {validation.private ? "private" : "public"}</span>
        )}
      </p>
    );
  }

  return (
    <div style={{ fontSize: "0.9em" }}>
      <p style={{ color: "var(--color-error, red)", marginBottom: 8 }}>
        ✗ {validation.error ?? "Repository not accessible"}
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          className="btn btn-primary"
          onClick={onCreateRepo}
          disabled={isCreating}
        >
          {isCreating ? "Creating…" : "Create this repo for me"}
        </button>
        <button
          type="button"
          className="btn"
          onClick={onRevalidate}
          disabled={isCreating}
        >
          Re-check
        </button>
      </div>
    </div>
  );
}
