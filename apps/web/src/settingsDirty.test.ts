import { afterEach, describe, expect, it } from "vitest";

import {
  discardDirtyInstanceSettings,
  hasDirtyInstanceSettings,
  setInstanceSettingsDirty,
} from "./settingsDirty";

afterEach(() => discardDirtyInstanceSettings());

describe("instance settings dirty transaction", () => {
  it("suppresses unload guards only after discard is committed", () => {
    setInstanceSettingsDirty(true);
    expect(hasDirtyInstanceSettings()).toBe(true);
    expect(discardDirtyInstanceSettings()).toBe(true);
    expect(hasDirtyInstanceSettings()).toBe(false);
  });

  it("tracks independent settings drafts without one clearing another", () => {
    setInstanceSettingsDirty(true, "instance");
    setInstanceSettingsDirty(true, "license");
    setInstanceSettingsDirty(false, "instance");
    expect(hasDirtyInstanceSettings()).toBe(true);
    setInstanceSettingsDirty(false, "license");
    expect(hasDirtyInstanceSettings()).toBe(false);
  });
});
