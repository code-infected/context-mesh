/** Shared number/date formatting helpers. */

function trimZero(v: number): string {
  return v.toFixed(1).replace(/\.0$/, "");
}

/** Auto-compact figures: 1,284 / 12.9K / 4.2M. */
export function compactNumber(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${trimZero(n / 1_000_000)}M`;
  if (abs >= 10_000) return `${trimZero(n / 1_000)}K`;
  return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

/** Full thousands-comma'd integer, for table cells. */
export function wholeNumber(n: number): string {
  return Math.round(n).toLocaleString("en-US");
}

/** Compression ratio (compressed/original) rendered as a percentage of the original. */
export function percent(ratio: number, digits = 1): string {
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** Guideline score multiplier, e.g. "1.80x". */
export function multiplier(v: number): string {
  return `${v.toFixed(2)}×`;
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function shortId(id: string, max = 12): string {
  return id.length <= max ? id : `${id.slice(0, max)}…`;
}

/** True when an ISO timestamp is within the last `minutes` minutes. */
export function withinMinutes(iso: string, minutes: number): boolean {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return false;
  return Date.now() - t <= minutes * 60_000;
}
