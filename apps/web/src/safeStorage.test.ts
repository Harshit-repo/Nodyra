import { afterEach, describe, expect, it, vi } from "vitest";

import { safeGetItem, safeRemoveItem, safeSetItem } from "./safeStorage";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("safeStorage", () => {
  it("reads, writes, and removes values when storage is available", () => {
    expect(safeSetItem("key", "value")).toBe(true);
    expect(safeGetItem("key")).toBe("value");
    expect(safeRemoveItem("key")).toBe(true);
    expect(safeGetItem("key")).toBeNull();
  });

  it("falls back without throwing when browser storage is blocked", () => {
    const blocked = () => {
      throw new DOMException("Storage is blocked", "SecurityError");
    };
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(blocked);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(blocked);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(blocked);

    expect(safeGetItem("key")).toBeNull();
    expect(safeSetItem("key", "value")).toBe(false);
    expect(safeRemoveItem("key")).toBe(false);
  });
});
