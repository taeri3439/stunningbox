// Pure helpers for the Style Library batch upload (no React, no network).

/** Derive a readable reference name from a filename: prefer the first [bracketed] segment
 *  (these reference files embed an English product name in brackets), else the stem. */
export function deriveName(filename: string): string {
  const base = filename.split(/[\\/]/).pop() ?? filename;   // drop any path prefix
  const stem = base.replace(/\.[^.]+$/, "");
  const bracket = stem.match(/\[([^\]]+)\]/);
  return (bracket ? bracket[1] : stem).trim().slice(0, 80);
}

/** Run `fn` over `items` with at most `limit` (>= 1) in flight; resolves to results in input
 *  order. Fail-fast: if `fn` rejects, this rejects too — callers that want all-settled behaviour
 *  should catch inside `fn` and return a result value (the batch-upload callers do exactly that). */
export async function mapLimit<T, R>(
  items: T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  if (limit < 1) throw new RangeError(`mapLimit: limit must be >= 1, got ${limit}`);
  const results = new Array<R>(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i], i);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}
