import { describe, expect, it } from "vitest";

import { tokenizePython, type CodeToken } from "./pythonHighlight";

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
