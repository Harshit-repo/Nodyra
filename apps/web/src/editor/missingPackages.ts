export function canonicalName(spec: string): string {
  const head = spec.trim().split(";")[0].trim();
  const match = head.match(/[A-Za-z0-9][A-Za-z0-9._-]*/);
  const name = match ? match[0] : head;
  return name.replace(/[-_.]+/g, "-").toLowerCase();
}

export function missingFor(requirements: string[], installed: string[]): string[] {
  const have = new Set(installed.filter((p) => p.trim()).map(canonicalName));
  return requirements.filter((r) => !have.has(canonicalName(r)));
}

export function parseRequirementsTxt(text: string): string[] {
  const out: string[] = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.split("#")[0].trim();
    if (!line || line.startsWith("-")) continue; // skip blanks/comments/includes
    out.push(line.split(";")[0].trim());
  }
  return out;
}

export interface PackageDiff {
  toAdd: string[];
  alreadyPresent: string[];
  installedNotInFile: string[];
}

export function diffPackages(fileSpecs: string[], installed: string[]): PackageDiff {
  const installedByCanon = new Map(installed.map((p) => [canonicalName(p), p]));
  const fileCanon = new Set(fileSpecs.map(canonicalName));
  const toAdd: string[] = [];
  const alreadyPresent: string[] = [];
  for (const spec of fileSpecs) {
    (installedByCanon.has(canonicalName(spec)) ? alreadyPresent : toAdd).push(spec);
  }
  const installedNotInFile = installed.filter((p) => !fileCanon.has(canonicalName(p)));
  return { toAdd, alreadyPresent, installedNotInFile };
}
