/** Quote text and prevent spreadsheet applications from evaluating audit data. */
export function csvTextCell(value: string): string {
  const safe = /^[\s]*[=+\-@]|^[\t\r\n]/u.test(value) ? `'${value}` : value;
  return `"${safe.replaceAll('"', '""')}"`;
}
