import { describe, expect, it } from "vitest";

import {
  computeCodeSuggestions,
  getIdentPrefix,
  tokenizePython,
  type CodeToken,
} from "./pythonHighlight";

/** The mirror is overlaid on the textarea char-for-char, so the joined token
 *  text must always reproduce the input exactly. */
function roundtrips(src: string): boolean {
  return tokenizePython(src).map((t) => t.text).join("") === src;
}

function classOf(tokens: CodeToken[], text: string): string | undefined {
  return tokens.find((t) => t.text === text)?.cls;
}

describe("tokenizePython", () => {
  it("preserves the exact source for arbitrary input", () => {
    for (const src of [
      "",
      "x = 1",
      "def f(a, b):\n    return a + b  # sum",
      "s = 'it\\'s'\nt = \"hi\"",
      'doc = """multi\nline"""\nn = 0xFF',
      "output = {'k': $node['n1'].main.Key}",
    ]) {
      expect(roundtrips(src)).toBe(true);
    }
  });

  it("classifies keywords, builtins, numbers, strings and comments", () => {
    const tokens = tokenizePython("def go():\n    print(len(x))  # note\n");
    expect(classOf(tokens, "def")).toBe("kw");
    expect(classOf(tokens, "print")).toBe("bi");
    expect(classOf(tokens, "len")).toBe("bi");
    expect(classOf(tokens, "# note")).toBe("com");

    const lit = tokenizePython("n = 42\ns = 'hi'");
    expect(classOf(lit, "42")).toBe("num");
    expect(classOf(lit, "'hi'")).toBe("str");
  });

  it("treats True/False/None as keywords and plain names as default", () => {
    const tokens = tokenizePython("ok = True\nname = value");
    expect(classOf(tokens, "True")).toBe("kw");
    // `value` is an ordinary identifier — merged into a default run.
    expect(tokens.some((t) => t.cls === "kw" && t.text === "value")).toBe(false);
  });

  it("keeps a comment glued to its content and ends it at the newline", () => {
    const tokens = tokenizePython("a # c\nb");
    const comment = tokens.find((t) => t.cls === "com");
    expect(comment?.text).toBe("# c");
  });
});

describe("getIdentPrefix", () => {
  it("returns the identifier fragment left of the cursor", () => {
    expect(getIdentPrefix("retur", 5)).toBe("retur");
    expect(getIdentPrefix("x = pri", 7)).toBe("pri");
  });

  it("is empty when the caret is not after a word char", () => {
    expect(getIdentPrefix("x = 1 ", 6)).toBe("");
    expect(getIdentPrefix("", 0)).toBe("");
  });

  it("stops at a dot so attribute access does not merge", () => {
    expect(getIdentPrefix("obj.fie", 7)).toBe("fie");
  });
});

describe("computeCodeSuggestions", () => {
  it("completes keywords and builtins from a prefix", () => {
    const out = computeCodeSuggestions("ret", 3);
    expect(out).toContain("return");
    const builtins = computeCodeSuggestions("le", 2);
    expect(builtins).toContain("len");
  });

  it("surfaces the code-node API names", () => {
    expect(computeCodeSuggestions("inp", 3)).toContain("inputs");
    expect(computeCodeSuggestions("cont", 4)).toContain("context");
  });

  it("includes identifiers defined in the buffer", () => {
    const src = "total_amount = 0\ntot";
    expect(computeCodeSuggestions(src, src.length)).toContain("total_amount");
  });

  it("excludes the exact word being typed and caps the list", () => {
    expect(computeCodeSuggestions("return", 6)).not.toContain("return");
    expect(computeCodeSuggestions("e", 1).length).toBeLessThanOrEqual(8);
  });

  it("returns nothing without a prefix", () => {
    expect(computeCodeSuggestions("", 0)).toEqual([]);
    expect(computeCodeSuggestions("x = ", 4)).toEqual([]);
  });
});
