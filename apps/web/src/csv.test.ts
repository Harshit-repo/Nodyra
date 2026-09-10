import { describe, expect, it } from "vitest";

import { csvTextCell } from "./csv";

describe("csvTextCell", () => {
  it.each(["=1+1", "+SUM(A1)", "-1+1", "@SUM(A1)", "  =1+1", "\tvalue", "\rvalue", "\nvalue"])(
    "exports %j as text instead of a spreadsheet expression",
    (value) => expect(csvTextCell(value)).toBe(`"'${value}"`),
  );

  it("preserves quoted, multiline, and ordinary text", () => {
    expect(csvTextCell('Created "Quarterly, report"\nVersion 2')).toBe('"Created ""Quarterly, report""\nVersion 2"');
    expect(csvTextCell("2026-09-05T01:00:00Z")).toBe('"2026-09-05T01:00:00Z"');
    expect(csvTextCell("")).toBe('""');
  });
});
