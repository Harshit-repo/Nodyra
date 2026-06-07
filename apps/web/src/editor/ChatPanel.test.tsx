import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatPanel } from "./ChatPanel";
import { api } from "../api";
import * as apiModule from "../api";

describe("ChatPanel", () => {
  beforeEach(() => {
    // The panel fetches the run timeline after each turn to show agent tool
    // calls. Default to an empty timeline unless a test overrides it.
    vi.spyOn(api, "runTimeline").mockResolvedValue({
      run_id: "r1",
      status: "success",
      events: [],
    });
  });

  it("sends a message and renders user + bot bubbles", async () => {
    vi.spyOn(api, "sendChatMessage").mockResolvedValue({
      run_id: "r1",
      reply: "Hi back",
      session_id: "s1",
      status: "success",
    });

    render(
      <ChatPanel
        workflowId="wf1"
        title="Chat"
        placeholder="Type…"
        initialMessage=""
        onRun={() => {}}
        onClose={() => {}}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Type…"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(await screen.findByText("hello")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Hi back")).toBeTruthy());
  });

  it("reset starts a new session id", async () => {
    const sent: string[] = [];
    vi.spyOn(api, "sendChatMessage").mockImplementation(
      async (_wf, _msg, sessionId) => {
        sent.push(sessionId);
        return { run_id: "r", reply: "ok", session_id: sessionId, status: "success" };
      },
    );

    render(
      <ChatPanel
        workflowId="wf1"
        title="Chat"
        placeholder="Type…"
        initialMessage=""
        onRun={() => {}}
        onClose={() => {}}
      />,
    );

    const input = screen.getByPlaceholderText("Type…");
    fireEvent.change(input, { target: { value: "a" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await screen.findByText("ok");

    fireEvent.click(screen.getByRole("button", { name: /reset/i }));

    fireEvent.change(input, { target: { value: "b" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(sent.length).toBe(2));

    expect(sent[0]).not.toBe(sent[1]);
  });

  it("shows a View run link on an errored turn and calls onViewRun", async () => {
    vi.spyOn(api, "sendChatMessage").mockResolvedValue({
      run_id: "rX",
      reply: "It failed",
      session_id: "s1",
      status: "error",
    });
    const onViewRun = vi.fn();

    render(
      <ChatPanel
        workflowId="wf1"
        title="Chat"
        placeholder="Type…"
        initialMessage=""
        onRun={() => {}}
        onClose={() => {}}
        onViewRun={onViewRun}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Type…"), {
      target: { value: "go" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    const link = await screen.findByRole("button", { name: /view run/i });
    fireEvent.click(link);
    expect(onViewRun).toHaveBeenCalledWith("rX");
  });

  it("shows the agent tool trace from the run timeline", async () => {
    vi.spyOn(api, "sendChatMessage").mockResolvedValue({
      run_id: "rT",
      reply: "Found it",
      session_id: "s1",
      status: "success",
    });
    vi.spyOn(api, "runTimeline").mockResolvedValue({
      run_id: "rT",
      status: "success",
      events: [
        {
          type: "agent_tool_started",
          ts: null,
          data: {
            tool_call_id: "c1",
            tool_name: "web_search",
            step: 0,
            arguments: { query: "noodle" },
          },
        },
        {
          type: "agent_tool_finished",
          ts: null,
          data: {
            tool_call_id: "c1",
            tool_name: "web_search",
            status: "success",
            duration_ms: 42,
            tool_result: {
              tool_call_id: "c1",
              name: "web_search",
              content: "search result text",
              is_error: false,
            },
          },
        },
      ],
    });

    render(
      <ChatPanel
        workflowId="wf1"
        title="Chat"
        placeholder="Type…"
        initialMessage=""
        onRun={() => {}}
        onClose={() => {}}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Type…"), {
      target: { value: "search" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    // Summary appears once the timeline resolves.
    const summary = await screen.findByRole("button", { name: /used 1 tool/i });
    fireEvent.click(summary);

    // Tool name is visible; expanding the step reveals its result.
    const stepHead = await screen.findByRole("button", { name: /web_search/i });
    fireEvent.click(stepHead);
    await waitFor(() =>
      expect(screen.getByText("search result text")).toBeTruthy(),
    );
  });

  it("streams the agent's live tool calls when live is set", async () => {
    let handlers:
      | { onMessage: (d: unknown) => void; onClosed?: () => void }
      | null = null;
    const closeSpy = vi.fn();
    vi.spyOn(apiModule, "subscribeToRunEvents").mockImplementation(
      (_runId, h) => {
        handlers = h;
        return { close: closeSpy };
      },
    );
    vi.spyOn(api, "startChatTurn").mockResolvedValue({
      run_id: "rL",
      session_id: "sL",
    });
    vi.spyOn(api, "chatTurnResult").mockResolvedValue({
      run_id: "rL",
      reply: "All done",
      session_id: "sL",
      status: "success",
    });
    const onRun = vi.fn();

    render(
      <ChatPanel
        workflowId="wf1"
        title="Chat"
        placeholder="Type…"
        initialMessage=""
        onRun={onRun}
        onClose={() => {}}
        live
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Type…"), {
      target: { value: "go" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    // The run is started and the canvas animation is triggered.
    await waitFor(() => expect(onRun).toHaveBeenCalledWith("rL"));
    await waitFor(() => expect(handlers).not.toBeNull());

    // A live tool call streams in and shows the in-progress summary.
    act(() => {
      handlers!.onMessage({
        type: "agent_tool_started",
        tool_call_id: "c1",
        tool_name: "web_search",
        step: 0,
        arguments: { query: "x" },
      });
    });
    await screen.findByRole("button", { name: /using 1 tool/i });

    // The tool finishes — summary flips to the completed state.
    act(() => {
      handlers!.onMessage({
        type: "agent_tool_finished",
        tool_call_id: "c1",
        tool_name: "web_search",
        status: "success",
        duration_ms: 5,
        tool_result: {
          tool_call_id: "c1",
          name: "web_search",
          content: "ok",
          is_error: false,
        },
      });
    });
    await screen.findByRole("button", { name: /used 1 tool/i });

    // The terminal event finalizes the turn with the fetched reply.
    act(() => {
      handlers!.onMessage({ type: "run_finished", status: "success" });
    });
    await waitFor(() => expect(screen.getByText("All done")).toBeTruthy());
    expect(closeSpy).toHaveBeenCalled();
  });
});
