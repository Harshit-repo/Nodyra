import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatPanel } from "./ChatPanel";
import { api } from "../api";

describe("ChatPanel", () => {
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
});
