/** Tiny, allocation-light Python tokenizer for the editor's highlight mirror.
 *
 *  It is NOT a parser — it only classifies runs of characters so the mirror can
 *  colour them. The one hard contract: the concatenation of every token's
 *  `text` MUST equal the input exactly (same characters, same order). The mirror
 *  is overlaid pixel-for-pixel on a textarea, so dropping or adding a single
 *  character would shift the highlight off the caret. */

export type CodeToken = { text: string; cls: PyClass };

export type PyClass = "kw" | "bi" | "str" | "num" | "com" | "";

const KEYWORDS = new Set([
  "and", "as", "assert", "async", "await", "break", "case", "class",
  "continue", "def", "del", "elif", "else", "except", "finally", "for",
  "from", "global", "if", "import", "in", "is", "lambda", "match",
  "nonlocal", "not", "or", "pass", "raise", "return", "try", "while",
  "with", "yield",
]);

// Constants read as keywords (distinct colour from plain identifiers).
const CONSTANTS = new Set(["True", "False", "None"]);

const BUILTINS = new Set([
  "abs", "all", "any", "bool", "bytes", "dict", "enumerate", "filter",
  "float", "format", "frozenset", "getattr", "hasattr", "hash", "id",
  "input", "int", "isinstance", "issubclass", "iter", "len", "list", "map",
  "max", "min", "next", "object", "open", "ord", "chr", "pow", "print",
  "range", "repr", "reversed", "round", "set", "setattr", "slice", "sorted",
  "str", "sum", "super", "tuple", "type", "vars", "zip",
]);

function isIdentStart(ch: string): boolean {
  return /[A-Za-z_]/.test(ch);
}
function isIdentPart(ch: string): boolean {
  return /[A-Za-z0-9_]/.test(ch);
}
function isDigit(ch: string): boolean {
  return ch >= "0" && ch <= "9";
}

/** Classify `src` into coloured tokens. Default (uncoloured) runs are merged. */
export function tokenizePython(src: string): CodeToken[] {
  const tokens: CodeToken[] = [];
  let plain = "";
  const flush = (): void => {
    if (plain) {
      tokens.push({ text: plain, cls: "" });
      plain = "";
    }
  };

  let i = 0;
  const n = src.length;
  while (i < n) {
    const ch = src[i];

    // Comment — runs to end of line.
    if (ch === "#") {
      flush();
      let j = i + 1;
      while (j < n && src[j] !== "\n") j++;
      tokens.push({ text: src.slice(i, j), cls: "com" });
      i = j;
      continue;
    }

    // String — single/double, including triple-quoted. Backslash escapes the
    // next char inside non-triple strings.
    if (ch === '"' || ch === "'") {
      flush();
      const triple = src.slice(i, i + 3);
      if (triple === '"""' || triple === "'''") {
        const quote = triple;
        let j = i + 3;
        while (j < n && src.slice(j, j + 3) !== quote) j++;
        j = Math.min(j + 3, n); // include closing quotes (or run to EOF)
        tokens.push({ text: src.slice(i, j), cls: "str" });
        i = j;
        continue;
      }
      let j = i + 1;
      while (j < n && src[j] !== ch && src[j] !== "\n") {
        if (src[j] === "\\") j++; // skip escaped char
        j++;
      }
      if (j < n && src[j] === ch) j++; // include closing quote
      tokens.push({ text: src.slice(i, j), cls: "str" });
      i = j;
      continue;
    }

    // Number — int/float, leading-dot floats, hex/bin/oct, underscores.
    if (isDigit(ch) || (ch === "." && isDigit(src[i + 1] ?? ""))) {
      flush();
      let j = i;
      while (j < n && /[0-9a-fA-FxXbBoO._]/.test(src[j])) j++;
      tokens.push({ text: src.slice(i, j), cls: "num" });
      i = j;
      continue;
    }

    // Identifier / keyword / builtin.
    if (isIdentStart(ch)) {
      let j = i + 1;
      while (j < n && isIdentPart(src[j])) j++;
      const word = src.slice(i, j);
      if (KEYWORDS.has(word) || CONSTANTS.has(word)) {
        flush();
        tokens.push({ text: word, cls: "kw" });
      } else if (BUILTINS.has(word)) {
        flush();
        tokens.push({ text: word, cls: "bi" });
      } else {
        plain += word;
      }
      i = j;
      continue;
    }

    // Anything else (whitespace, operators, punctuation) stays plain.
    plain += ch;
    i++;
  }
  flush();
  return tokens;
}
