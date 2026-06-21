import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConfirmProvider, useConfirm, usePrompt } from "./ConfirmProvider";

function ConfirmHarness() {
  const confirm = useConfirm();
  const [result, setResult] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        onClick={async () => {
          const ok = await confirm({ title: "Delete?", body: "Cannot undo." });
          setResult(ok ? "confirmed" : "cancelled");
        }}
      >
        trigger-confirm
      </button>
      {result !== null && <output>{result}</output>}
    </>
  );
}

function PromptHarness() {
  const prompt = usePrompt();
  const [result, setResult] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        onClick={async () => {
          const value = await prompt({ title: "New org", label: "Name" });
          setResult(value ?? "__null__");
        }}
      >
        trigger-prompt
      </button>
      {result !== null && <output>{result}</output>}
    </>
  );
}

describe("ConfirmProvider", () => {
  it("useConfirm resolves true when the confirm button is clicked", async () => {
    render(
      <ConfirmProvider>
        <ConfirmHarness />
      </ConfirmProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "trigger-confirm" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByText("confirmed")).toBeTruthy();
  });

  it("useConfirm resolves false when Cancel is clicked", async () => {
    render(
      <ConfirmProvider>
        <ConfirmHarness />
      </ConfirmProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "trigger-confirm" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("cancelled")).toBeTruthy();
  });

  it("usePrompt resolves with typed string when Confirm is clicked", async () => {
    render(
      <ConfirmProvider>
        <PromptHarness />
      </ConfirmProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "trigger-prompt" }));
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Acme" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Acme")).toBeTruthy();
  });

  it("usePrompt resolves with null when Cancel is clicked", async () => {
    render(
      <ConfirmProvider>
        <PromptHarness />
      </ConfirmProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "trigger-prompt" }));
    await screen.findByLabelText("Name");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("__null__")).toBeTruthy();
  });

  it("useConfirm and usePrompt work independently in the same provider", async () => {
    render(
      <ConfirmProvider>
        <ConfirmHarness />
        <PromptHarness />
      </ConfirmProvider>,
    );
    // Trigger confirm dialog
    fireEvent.click(screen.getByRole("button", { name: "trigger-confirm" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByText("confirmed")).toBeTruthy();
    // Now trigger prompt dialog
    fireEvent.click(screen.getByRole("button", { name: "trigger-prompt" }));
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Noodle" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Noodle")).toBeTruthy();
  });
});
