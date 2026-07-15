import { useTheme } from "../theme";

export interface TooltipPayloadItem {
  name?: string | number;
  value?: number | string | Array<number | string>;
  color?: string;
  dataKey?: string | number;
}

export interface ChartTooltipProps {
  /* Injected by Recharts */
  active?: boolean;
  label?: unknown;
  payload?: TooltipPayloadItem[];
  /* Ours */
  valueFormatter?: (value: number) => string;
  labelFormatter?: (label: string) => string;
}

/**
 * Shared Recharts tooltip. Values lead (strong, primary ink), series names
 * follow (secondary ink); each row is keyed with a short line of the series
 * color, never a filled box. All content rendered via JSX text (no innerHTML).
 */
export function ChartTooltip({
  active,
  label,
  payload,
  valueFormatter,
  labelFormatter,
}: ChartTooltipProps) {
  const { palette } = useTheme();
  if (!active || !payload || payload.length === 0) return null;

  const heading =
    label === undefined || label === null
      ? null
      : labelFormatter
        ? labelFormatter(String(label))
        : String(label);

  return (
    <div
      className="rounded-md border border-hairline px-3 py-2 shadow-sm"
      style={{ backgroundColor: palette.surface }}
    >
      {heading !== null && heading !== "" && (
        <div className="mb-1 text-xs text-ink-2">{heading}</div>
      )}
      <div className="space-y-1">
        {payload.map((entry, i) => {
          const raw = Array.isArray(entry.value) ? entry.value[0] : entry.value;
          const num = typeof raw === "number" ? raw : Number(raw);
          const display =
            valueFormatter && Number.isFinite(num) ? valueFormatter(num) : String(raw ?? "");
          return (
            <div key={i} className="flex items-center gap-2">
              <span
                className="inline-block h-0.5 w-3 rounded-full"
                style={{ backgroundColor: entry.color ?? palette.series1 }}
              />
              <span className="text-sm font-semibold text-ink">{display}</span>
              <span className="text-xs text-ink-2">{String(entry.name ?? "")}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
