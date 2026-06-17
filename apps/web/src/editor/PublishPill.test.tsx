import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PublishPill } from "./PublishPill";
import { deriveBarStatus } from "./barStatus";

describe("PublishPill", () => {
  it("renders the label and a green dot when published", () => {
    const status = deriveBarStatus({ publishedVersion: 2, active: true, hasUnpublishedChanges: false, dirty: false });
    const { container } = render(<PublishPill status={status} onClick={() => {}} />);
    expect(screen.getByRole("button", { name: /published/i })).toBeInTheDocument();
    expect(container.querySelector(".publish-dot.is-green")).toBeTruthy();
  });

  it("renders an amber dot for draft changes", () => {
    const status = deriveBarStatus({ publishedVersion: 2, active: true, hasUnpublishedChanges: true, dirty: false });
    const { container } = render(<PublishPill status={status} onClick={() => {}} />);
    expect(container.querySelector(".publish-dot.is-amber")).toBeTruthy();
  });

  it("renders no dot when never published", () => {
    const status = deriveBarStatus({ publishedVersion: null, active: false, hasUnpublishedChanges: false, dirty: false });
    const { container } = render(<PublishPill status={status} onClick={() => {}} />);
    expect(container.querySelector(".publish-dot")).toBeNull();
  });

  it("fires onClick", async () => {
    const onClick = vi.fn();
    const status = deriveBarStatus({ publishedVersion: 1, active: true, hasUnpublishedChanges: false, dirty: false });
    render(<PublishPill status={status} onClick={onClick} />);
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
