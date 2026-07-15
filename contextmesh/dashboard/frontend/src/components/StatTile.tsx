interface StatTileProps {
  /** Sentence case, no trailing colon. */
  label: string;
  /** Pre-formatted display value (auto-compact upstream). */
  value: string;
  /** Optional secondary line under the value. */
  hint?: string;
}

/**
 * Stat tile per the figure contract: label / value (sans semibold,
 * proportional figures — no tabular-nums at display size) / optional hint.
 */
export function StatTile({ label, value, hint }: StatTileProps) {
  return (
    <div className="rounded-lg border border-hairline bg-surface p-4">
      <div className="text-xs text-ink-2">{label}</div>
      <div className="mt-1 text-3xl font-semibold text-ink">{value}</div>
      {hint && <div className="mt-1 text-xs text-ink-3">{hint}</div>}
    </div>
  );
}
