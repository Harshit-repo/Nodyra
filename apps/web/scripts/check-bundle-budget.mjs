import { gzipSync } from "node:zlib";
import { readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const assetDir = join(root, "dist", "assets");
const budgets = JSON.parse(await readFile(join(root, "bundle-budgets.json"), "utf8"));
const names = await readdir(assetDir);
const assets = [];

for (const name of names.sort()) {
  if (!/\.(?:css|js)$/.test(name)) continue;
  const bytes = await readFile(join(assetDir, name));
  assets.push({ name, rawBytes: bytes.length, gzipBytes: gzipSync(bytes).length });
}

function budgetFor(name) {
  if (name.startsWith("vendor-plotly-")) return ["plotlyChunkGzipBytes", budgets.plotlyChunkGzipBytes];
  if (name.startsWith("vendor-monaco-")) return ["monacoChunkGzipBytes", budgets.monacoChunkGzipBytes];
  if (name.startsWith("vendor-icons-")) return ["iconChunkGzipBytes", budgets.iconChunkGzipBytes];
  if (/^index-[^.]+\.js$/.test(name)) return ["entryJavaScriptGzipBytes", budgets.entryJavaScriptGzipBytes];
  if (/^index-[^.]+\.css$/.test(name)) return ["globalCssGzipBytes", budgets.globalCssGzipBytes];
  return ["routeChunkGzipBytes", budgets.routeChunkGzipBytes];
}

const failures = [];
for (const asset of assets) {
  const [budgetName, budgetBytes] = budgetFor(asset.name);
  asset.budget = budgetName;
  asset.budgetBytes = budgetBytes;
  if (asset.gzipBytes > budgetBytes) {
    failures.push(`${asset.name}: ${asset.gzipBytes} > ${budgetBytes} gzip bytes (${budgetName})`);
  }
}

const totalGzipBytes = assets.reduce((sum, asset) => sum + asset.gzipBytes, 0);
if (totalGzipBytes > budgets.totalGzipBytes) {
  failures.push(`total: ${totalGzipBytes} > ${budgets.totalGzipBytes} gzip bytes`);
}

const report = {
  generatedAt: new Date().toISOString(),
  budgets,
  totalGzipBytes,
  assets,
};
await writeFile(join(root, "dist", "bundle-budget-report.json"), `${JSON.stringify(report, null, 2)}\n`);

for (const asset of [...assets].sort((a, b) => b.gzipBytes - a.gzipBytes)) {
  process.stdout.write(`${asset.name.padEnd(48)} ${String(asset.gzipBytes).padStart(9)} gzip bytes\n`);
}
process.stdout.write(`${"TOTAL".padEnd(48)} ${String(totalGzipBytes).padStart(9)} gzip bytes\n`);

if (failures.length > 0) {
  process.stderr.write(`\nBundle budget exceeded:\n- ${failures.join("\n- ")}\n`);
  process.exitCode = 1;
}
