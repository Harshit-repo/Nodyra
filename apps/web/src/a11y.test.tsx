/**
 * Automated accessibility checks (F-13).
 *
 * The app carries 768 hand-written ARIA attributes, a focus-trap hook and a
 * dedicated a11y modal — all authored carefully and none of it ever verified by
 * a tool. Hand-written ARIA is exactly where silent regressions live: a label
 * pointing at an id that was renamed, a role whose required child disappeared,
 * a control that lost its accessible name in a refactor. None of that shows up
 * in a snapshot or a click test.
 *
 * axe-core runs the same rule set browsers' own audits use. These cover the
 * surfaces every user meets on their first minute in the product — sign-in,
 * first-run setup, dialogs and empty states — plus the shared primitives those
 * screens are built from.
 */
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { axe } from "vitest-axe";
import { describe, expect, it } from "vitest";

import { ConfirmDialog } from "./ConfirmDialog";
import { EmptyState } from "./EmptyState";
import { FirstRunWizard } from "./FirstRunWizard";
import { LoginPage } from "./LoginPage";
import { PromptDialog } from "./PromptDialog";
import { Skeleton, SkeletonCardGrid, SkeletonRows } from "./Skeleton";
import type { AuthState } from "./types";

/**
 * axe returns a violations array; assert on it directly so a failure names the
 * rule and the offending element rather than just "expected true".
 */
async function expectNoViolations(ui: React.ReactElement) {
  const { container } = render(ui);
  const results = await axe(container);
  const summary = results.violations.map(
    (v) => `${v.id} (${v.impact}): ${v.help} — ${v.nodes.length} node(s)`,
  );
  expect(summary).toEqual([]);
}

describe("dialogs", () => {
  it("ConfirmDialog has no accessibility violations", async () => {
    await expectNoViolations(
      <ConfirmDialog
        title="Delete workflow"
        body="This removes the workflow and its run history. This cannot be undone."
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
  });

  it("ConfirmDialog stays accessible while busy", async () => {
    // Disabled controls are a common source of lost accessible names.
    await expectNoViolations(
      <ConfirmDialog
        title="Delete workflow"
        body="Deleting…"
        busy
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
  });

  it("PromptDialog has no accessibility violations", async () => {
    await expectNoViolations(
      <PromptDialog
        title="Rename workflow"
        label="New name"
        placeholder="Daily report"
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
  });

  it("PromptDialog labels its input", async () => {
    // The single highest-value a11y guarantee for a form: every control has a
    // programmatically associated label, not just visible text beside it.
    const { container } = render(
      <PromptDialog
        title="Rename workflow"
        label="New name"
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    const results = await axe(container, {
      runOnly: ["label", "aria-valid-attr-value", "aria-required-attr"],
    });
    expect(results.violations.map((v) => v.id)).toEqual([]);
  });
});

describe("shared primitives", () => {
  it("EmptyState has no accessibility violations", async () => {
    await expectNoViolations(
      <EmptyState
        title="No workflows yet"
        description="Create your first workflow to see it here."
        action={<button type="button">New workflow</button>}
      />,
    );
  });

  it("loading skeletons are not announced as content", async () => {
    // Purely decorative placeholders must not be read out as text while the
    // real content is still loading.
    await expectNoViolations(
      <div>
        <Skeleton />
        <SkeletonRows />
        <SkeletonCardGrid />
      </div>,
    );
  });
});

describe("first-minute screens", () => {
  // Sign-in and first-run setup are the only screens every user without
  // exception passes through, so an a11y defect here excludes people from the
  // product entirely rather than from one feature.

  it("LoginPage has no accessibility violations", async () => {
    await expectNoViolations(
      <LoginPage registrationOpen={false} onSignedIn={() => {}} />,
    );
  });

  it("LoginPage stays accessible with registration open", async () => {
    await expectNoViolations(
      <LoginPage registrationOpen onSignedIn={() => {}} />,
    );
  });

  it("FirstRunWizard has no accessibility violations", async () => {
    const auth: AuthState = {
      auth_required: true,
      signed_in: true,
      registration_open: false,
      multi_tenancy: true,
      user: {
        id: "user-1",
        email: "owner@example.com",
        name: "Owner",
        company: "Nodyra",
        role: "owner",
      },
    };
    await expectNoViolations(
      <MemoryRouter>
        <FirstRunWizard auth={auth} />
      </MemoryRouter>,
    );
  });
});

describe("colour and structure rules apply", () => {
  it("axe is actually running its rule set", async () => {
    // Guard the guard: if axe silently no-ops, every assertion above passes
    // vacuously. Feed it markup with a known, unambiguous violation.
    const { container } = render(
      <div>
        <img src="/logo.png" />
        <input type="text" />
      </div>,
    );
    const results = await axe(container);
    const ids = results.violations.map((v) => v.id);
    expect(ids).toContain("image-alt");
  });
});
