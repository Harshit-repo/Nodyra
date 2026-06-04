import { describe, expect, it } from "vitest";

import { fromAiExpr, isFromAiExpr, paramArgType } from "./toolParam";

describe("toolParam helpers", () => {
  it("builds a positional $fromAI expression", () => {
    expect(fromAiExpr("text", "What to say", "string")).toBe(
      "{{ $fromAI('text', 'What to say', 'string') }}",
    );
  });

  it("escapes single quotes in name/description", () => {
    expect(fromAiExpr("it's", "a 'quote'", "string")).toBe(
      "{{ $fromAI('it\\'s', 'a \\'quote\\'', 'string') }}",
    );
  });

  it("detects a $fromAI expression value", () => {
    expect(isFromAiExpr("{{ $fromAI('x', '', 'string') }}")).toBe(true);
    expect(isFromAiExpr("{{ $json.x }}")).toBe(false);
    expect(isFromAiExpr("plain")).toBe(false);
    expect(isFromAiExpr(42)).toBe(false);
  });

  it("maps param spec types to JSON-schema arg types", () => {
    expect(paramArgType("number")).toBe("number");
    expect(paramArgType("integer")).toBe("number");
    expect(paramArgType("boolean")).toBe("boolean");
    expect(paramArgType("string")).toBe("string");
    expect(paramArgType("json")).toBe("string");
  });
});
