import type { GuidelineUpdate } from "../api";
import { formatDateTime, multiplier, shortId } from "../format";
import { useTheme } from "../theme";
import { ArrowDownIcon, ArrowUpIcon } from "./icons";

/**
 * Vertical timeline of ACON guideline multiplier updates, newest first.
 * Direction is carried by an icon plus the old→new values in ink tokens —
 * never by color alone (and a multiplier change is change, not good/bad).
 */
export function GuidelineTimeline({ history }: { history: GuidelineUpdate[] }) {
  const { palette } = useTheme();

  const sorted = [...history].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
  );

  return (
    <ol className="relative ml-1.5 space-y-5 border-l border-grid pl-5">
      {sorted.map((event, i) => {
        const increased = event.new_multiplier >= event.old_multiplier;
        return (
          <li key={`${event.tool_name}-${event.chunk_type}-${event.timestamp}-${i}`} className="relative">
            <span
              className="absolute -left-[26px] top-1 h-2.5 w-2.5 rounded-full ring-4 ring-surface"
              style={{ backgroundColor: palette.series1 }}
            />
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="rounded border border-hairline bg-page px-1.5 py-0.5 font-mono text-xs text-ink">
                {event.tool_name}
              </span>
              <span className="rounded border border-hairline bg-page px-1.5 py-0.5 font-mono text-xs text-ink-2">
                {event.chunk_type}
              </span>
              <span className="ml-auto text-xs text-ink-3">
                {formatDateTime(event.timestamp)}
              </span>
            </div>
            <div className="mt-1.5 flex items-center gap-1.5 text-sm">
              <span className="text-ink-2">{multiplier(event.old_multiplier)}</span>
              <span className="text-ink-3">→</span>
              <span className="font-semibold text-ink">
                {multiplier(event.new_multiplier)}
              </span>
              <span className="text-ink-3">
                {increased ? <ArrowUpIcon size={12} /> : <ArrowDownIcon size={12} />}
              </span>
              <span className="text-xs text-ink-3">
                {increased ? "boosted" : "decayed"}
              </span>
            </div>
            <div className="mt-1 text-xs text-ink-3">
              Evidence: <span className="font-mono">{shortId(event.task_id, 24)}</span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
