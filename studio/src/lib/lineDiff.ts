/**
 * 1-based line numbers in `next` that are new or modified relative to `prev`.
 * LCS over lines with common prefix/suffix trimmed; falls back to "all lines in
 * the differing middle" when the middle is too large to diff cheaply.
 */
export function changedLines(prev: string, next: string, maxCells = 4_000_000): Set<number> {
  const a = prev.split("\n");
  const b = next.split("\n");
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const out = new Set<number>();
  const n = endA - start;
  const m = endB - start;
  if (m === 0) return out;
  if (n === 0 || n * m > maxCells) {
    for (let j = start; j < endB; j++) out.add(j + 1);
    return out;
  }
  // dp[i][j] = LCS length of a[start+i..endA) and b[start+j..endB)
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[start + i] === b[start + j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  let i = 0;
  let j = 0;
  while (j < m) {
    if (i < n && a[start + i] === b[start + j]) {
      i++;
      j++;
    } else if (i < n && dp[i + 1][j] >= dp[i][j + 1]) {
      i++;
    } else {
      out.add(start + j + 1);
      j++;
    }
  }
  return out;
}
