/**
 * A modal's action buttons must stay reachable.
 *
 * `.modal` declared no max-height and no overflow handling. A modal taller than
 * the viewport therefore rendered its footer below the fold, and because
 * `.modal-overlay` is `position: fixed` with `overflow-y: visible`, neither the
 * overlay nor the document scrolled. The buttons could not be reached at all.
 *
 * Measured in the shipped build on a 1920x895 viewport, with the New-workflow
 * dialog open (it lists every template):
 *
 *   overlay scrollHeight 1298 vs clientHeight 895   (403px of overflow)
 *   Create button top: 1240px  -> 345px below the fold
 *   setting scrollTop = 600 left it at 0
 *
 * So on an ordinary laptop screen a user could not create a workflow — the
 * product's primary action, with no way to scroll to it.
 *
 * jsdom does not do layout, so this asserts the CSS contract rather than
 * simulating the overflow: the rule must bound its height and provide its own
 * scrolling.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "index.css"), "utf8");

function ruleBody(selector: string): string {
  // Match the rule whose selector list starts at a line beginning with it.
  const pattern = new RegExp(`^\\${selector}\\s*\\{([^}]*)\\}`, "m");
  const match = css.match(pattern);
  if (!match) throw new Error(`no CSS rule found for ${selector}`);
  return match[1];
}

describe(".modal stays usable when its content is tall", () => {
  const body = ruleBody(".modal");

  it("bounds its height to the viewport", () => {
    expect(body).toMatch(/max-height:\s*(calc\(100vh|min\(|\d+vh)/);
  });

  it("scrolls its own content rather than overflowing off-screen", () => {
    expect(body).toMatch(/overflow-y:\s*(auto|scroll)/);
  });

  it("guard the guard: the rule is the one that was broken", () => {
    // If .modal stops being a flex column with padding, this suite is pinned to
    // a rule that no longer governs dialog layout and should be revisited.
    expect(body).toMatch(/display:\s*flex/);
    expect(body).toMatch(/flex-direction:\s*column/);
  });
});

describe(".modal-overlay is why the page could not scroll instead", () => {
  const body = ruleBody(".modal-overlay");

  it("is a fixed full-viewport layer", () => {
    // Documents the reason the fix belongs on .modal: a fixed, inset-0 overlay
    // takes the dialog out of document flow, so the page has nothing to scroll.
    expect(body).toMatch(/position:\s*fixed/);
    expect(body).toMatch(/inset:\s*0/);
  });
});

describe("the New-workflow dialog caps its template list", () => {
  const source = readFileSync(join(__dirname, "WorkflowsPage.tsx"), "utf8");

  it("shows a first screenful rather than the whole catalogue", () => {
    // Sixteen templates made the dialog taller than a laptop viewport, which is
    // what put Create below the fold in the first place. Bounding .modal made
    // the button reachable; capping the list keeps it near the fold.
    expect(source).toMatch(/TEMPLATE_PREVIEW_COUNT\s*=\s*10/);
    expect(source).toMatch(/visibleTemplates/);
  });

  it("offers a way to see the rest", () => {
    expect(source).toMatch(/setShowAllTemplates\(true\)/);
    expect(source).toMatch(/more templates/);
  });

  it("expands automatically when a template is preselected", () => {
    // The activation checklist deep-links to a specific starter. If that one
    // sits past the cut it must still show as chosen rather than vanish.
    expect(source).toMatch(/useState\(\s*\n?\s*initialTemplateId !== "blank",?\s*\n?\s*\)/);
  });
});

describe("the template list scrolls instead of the dialog", () => {
  const body = ruleBody(".template-picker");

  it("bounds its own height", () => {
    // Otherwise ten template cards are still taller than a laptop viewport and
    // Create sits below the fold even with .modal bounded.
    expect(body).toMatch(/max-height:\s*\d+vh/);
  });

  it("scrolls itself", () => {
    expect(body).toMatch(/overflow-y:\s*auto/);
  });
});
