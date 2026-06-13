/** Combine fetched options with the current free-text value so a typed,
 * unlisted value is never lost and never duplicated.
 */
export function mergeOptions(fetched: string[], current: string): string[] {
  const list = [...fetched];
  if (current && !list.includes(current)) list.unshift(current);
  return list;
}
