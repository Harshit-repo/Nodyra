import { afterEach, describe, expect, it, vi } from "vitest";

import { onUnauthorized, subscribeToRunEvents } from "./api";

// FE-4: when the run-events socket closes with 1008 (auth refused / session
// expired mid-run), the client must run the same unauthorized path REST uses
// (clear session + notify) instead of leaving the UI in a stale signed-in state.

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static OPEN = 1;
  readyState = FakeWebSocket.OPEN;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  close() {}
}

describe("subscribeToRunEvents 1008 close (FE-4)", () => {
  afterEach(() => {
    onUnauthorized(null);
    FakeWebSocket.instances = [];
    vi.restoreAllMocks();
  });

  it("invokes the unauthorized handler on a 1008 close", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
    const onAuth = vi.fn();
    onUnauthorized(onAuth);
    const onClosed = vi.fn();

    subscribeToRunEvents("run-1", { onMessage: () => {}, onClosed });
    const sock = FakeWebSocket.instances[0];
    sock.onclose?.({ code: 1008 });

    expect(onAuth).toHaveBeenCalledTimes(1);
    expect(onClosed).toHaveBeenCalledTimes(1);
  });

  it("does NOT invoke the unauthorized handler on a normal 1000 close", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
    const onAuth = vi.fn();
    onUnauthorized(onAuth);

    subscribeToRunEvents("run-2", { onMessage: () => {} });
    const sock = FakeWebSocket.instances[0];
    sock.onclose?.({ code: 1000 });

    expect(onAuth).not.toHaveBeenCalled();
  });
});
