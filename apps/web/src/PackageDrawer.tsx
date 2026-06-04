import { useEffect, useMemo, useState } from "react";

import { api } from "./api";
import {
  canonicalName,
  diffPackages,
  parseRequirementsTxt,
} from "./editor/missingPackages";
import type { Environment, PackageUsagePackage } from "./types";

export function PackageDrawer({
  env,
  onClose,
  onChanged,
}: {
  env: Environment;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [usage, setUsage] = useState<PackageUsagePackage[]>([]);
  const [entry, setEntry] = useState("");
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingImport, setPendingImport] = useState<string[] | null>(null);
  const [removeStale, setRemoveStale] = useState(false);

  useEffect(() => {
    api
      .packageUsage(env.id)
      .then((u) => setUsage(u.packages))
      .catch(() => setUsage([]));
  }, [env.id]);

  const usageByCanon = useMemo(() => {
    const m = new Map<string, PackageUsagePackage>();
    for (const p of usage) m.set(p.package, p);
    return m;
  }, [usage]);

  async function commit(packages: string[]) {
    setBusy(true);
    try {
      await api.setPackages(env.id, packages);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function addEntry() {
    const additions = entry
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (additions.length === 0) return;
    const have = new Set(env.packages.map(canonicalName));
    const merged = [...env.packages];
    for (const a of additions) {
      if (!have.has(canonicalName(a))) merged.push(a);
    }
    setEntry("");
    await commit(merged);
  }

  async function onFile(file: File) {
    const specs = parseRequirementsTxt(await file.text());
    setPendingImport(specs);
    setRemoveStale(false);
  }

  const importDiff = pendingImport ? diffPackages(pendingImport, env.packages) : null;

  async function applyImport() {
    if (!importDiff) return;
    let result = [...env.packages, ...importDiff.toAdd];
    if (removeStale) {
      const removeSet = new Set(importDiff.installedNotInFile.map(canonicalName));
      result = result.filter((p) => !removeSet.has(canonicalName(p)));
    }
    setPendingImport(null);
    await commit(result);
  }

  async function removeOne(pkg: string) {
    const used = usageByCanon.get(canonicalName(pkg));
    if (used && used.used_by.length > 0) {
      const ok = window.confirm(
        `${pkg} is required by ${used.used_by.length} node(s). Remove anyway?`,
      );
      if (!ok) return;
    }
    await commit(env.packages.filter((p) => p !== pkg));
  }

  const visible = env.packages.filter(
    (p) => !filter.trim() || canonicalName(p).includes(canonicalName(filter)),
  );

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="pkg-drawer" onClick={(e) => e.stopPropagation()}>
        <header className="pkg-drawer-head">
          <div>
            <h3>{env.name} · packages</h3>
            <p className="muted">
              {env.packages.length} installed · rebuild runs on save
            </p>
          </div>
          <button className="btn btn-ghost" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <label className="field-label">Add packages</label>
        <div className="env-add">
          <input
            className="field-input"
            placeholder="pandas, numpy==2.1, httpx>=0.27 …"
            value={entry}
            onChange={(e) => setEntry(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void addEntry()}
          />
          <button className="btn btn-sm" disabled={busy} onClick={() => void addEntry()}>
            Add
          </button>
        </div>

        <label className="field-label">Import requirements.txt</label>
        <div
          className="pkg-dropzone"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const f = e.dataTransfer.files[0];
            if (f) void onFile(f);
          }}
        >
          <input
            type="file"
            accept=".txt"
            onChange={(e) => e.target.files?.[0] && void onFile(e.target.files[0])}
          />
          Drop a requirements.txt or browse
        </div>

        {importDiff && (
          <div className="pkg-import-diff">
            <p>
              +{importDiff.toAdd.length} to add · {importDiff.alreadyPresent.length}{" "}
              present · {importDiff.installedNotInFile.length} installed but not in file
            </p>
            {importDiff.installedNotInFile.length > 0 && (
              <label>
                <input
                  type="checkbox"
                  checked={removeStale}
                  onChange={(e) => setRemoveStale(e.target.checked)}
                />
                Also remove packages not in the file
                {removeStale &&
                  importDiff.installedNotInFile.some(
                    (p) => (usageByCanon.get(canonicalName(p))?.used_by.length ?? 0) > 0,
                  ) && <span className="warn-text"> ⚠ some are required by nodes</span>}
              </label>
            )}
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setPendingImport(null)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() => void applyImport()}
              >
                Apply
              </button>
            </div>
          </div>
        )}

        <label className="field-label">Installed</label>
        <input
          className="field-input"
          placeholder="filter"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <ul className="pkg-list">
          {visible.length === 0 && <li className="muted">No packages</li>}
          {visible.map((p) => {
            const used = usageByCanon.get(canonicalName(p));
            return (
              <li key={p} className="pkg-list-row">
                <span>
                  {p}
                  {used?.used_by.map((u) => (
                    <span className="pkg-tag-node" key={u.workflow_id + u.node_id}>
                      node: {u.node_label}
                    </span>
                  ))}
                </span>
                <button aria-label={`remove ${p}`} onClick={() => void removeOne(p)}>
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      </aside>
    </div>
  );
}
