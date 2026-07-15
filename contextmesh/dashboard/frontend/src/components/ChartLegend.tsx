export interface LegendItem {
  label: string;
  color: string;
}

/**
 * Legend row for charts with two or more series (a single series needs no
 * legend — the card title names it). Swatches mirror the mark (rect for
 * bars/areas); label text stays in ink tokens, never the series color.
 */
export function ChartLegend({ items }: { items: LegendItem[] }) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
      {items.map((item) => (
        <span key={item.label} className="flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ backgroundColor: item.color }}
          />
          <span className="text-xs text-ink-2">{item.label}</span>
        </span>
      ))}
    </div>
  );
}
