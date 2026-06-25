import { useSyncExternalStore } from "react";

const dirtySources = new Set<string>();
let discardRevision = 0;
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

export function setInstanceSettingsDirty(value: boolean, source = "instance"): void {
  const wasDirty = hasDirtyInstanceSettings();
  if (value) dirtySources.add(source);
  else dirtySources.delete(source);
  if (wasDirty === hasDirtyInstanceSettings()) return;
  emit();
}

export function hasDirtyInstanceSettings(): boolean {
  return dirtySources.size > 0;
}

export function discardDirtyInstanceSettings(): boolean {
  const wasDirty = hasDirtyInstanceSettings();
  dirtySources.clear();
  if (wasDirty) {
    discardRevision += 1;
    emit();
  }
  return wasDirty;
}

export function useInstanceSettingsDirty(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    hasDirtyInstanceSettings,
    hasDirtyInstanceSettings,
  );
}

export function useInstanceSettingsDiscardRevision(): number {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => discardRevision,
    () => discardRevision,
  );
}
