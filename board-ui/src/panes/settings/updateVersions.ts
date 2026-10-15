// Numeric release compare, enough for the X.Y.Z tags both feeds publish.
// Anything after '+' or '-' is ignored, matching the engine's parse_version.
export function newer(a: string | undefined | null, b: string | undefined | null): boolean {
  if (!a || !b) return false;
  const parts = (v: string) => (v.split(/[+-]/)[0] ?? '').split('.').map((n) => parseInt(n, 10) || 0);
  const x = parts(a);
  const y = parts(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if ((x[i] ?? 0) !== (y[i] ?? 0)) return (x[i] ?? 0) > (y[i] ?? 0);
  }
  return false;
}

// Which packages the next apply would actually move, e.g. "pro 0.1.7".
// Empty when nothing is behind.
export function pendingUpdates(
  core: string | null | undefined,
  latestCore: string | undefined,
  pro: string | null | undefined,
  latestPro: string | undefined,
): string {
  const parts: string[] = [];
  if (newer(latestCore, core)) parts.push(`kompany ${latestCore}`);
  if (pro && newer(latestPro, pro)) parts.push(`pro ${latestPro}`);
  return parts.join(' · ');
}
