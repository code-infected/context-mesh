import { api } from "../api";
import { Card } from "../components/Card";
import { EmptyState, ErrorState } from "../components/EmptyState";
import { GuidelineTimeline } from "../components/GuidelineTimeline";
import { formatDateTime, multiplier } from "../format";
import { useApi } from "../hooks";
import { useTheme } from "../theme";

const MULTIPLIER_CAP = 3.0; // guideline engine caps score multipliers at 3.0

/**
 * Meter for a score multiplier against the 3.0 cap. The unfilled track is a
 * lighter step of the same ramp; the fill shifts to warning/serious severity
 * as the multiplier approaches the overcorrection cap. The numeric value is
 * always printed beside it, so color never carries the value alone.
 */
function MultiplierMeter({ value }: { value: number }) {
  const { palette } = useTheme();
  const frac = Math.max(0, Math.min(1, value / MULTIPLIER_CAP));
  const fill =
    value >= 2.75 ? palette.serious : value >= 2.0 ? palette.warning : palette.series1;
  return (
    <div className="flex items-center gap-2">
      <div
        className="h-1.5 w-24 overflow-hidden rounded-full"
        style={{ backgroundColor: palette.meterTrack }}
      >
        <div
          className="h-full rounded-full"
          style={{ width: `${frac * 100}%`, backgroundColor: fill }}
        />
      </div>
      <span className="text-xs font-medium tabular-nums text-ink">{multiplier(value)}</span>
    </div>
  );
}

export function GuidelineHistory() {
  const guidelines = useApi(api.guidelines, [], 15_000);
  const history = useApi(api.guidelineHistory, [], 15_000);

  if (guidelines.error) {
    return <ErrorState message={guidelines.error} onRetry={guidelines.reload} />;
  }

  const rows = guidelines.data?.guidelines ?? [];
  const events = history.data?.history ?? [];

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
      <Card
        title="Guideline update timeline"
        subtitle="ACON failure loop: multiplier updates with task evidence, newest first"
        className="xl:col-span-3"
        dimmed={history.loading && events.length > 0}
      >
        {history.error ? (
          <div className="py-6 text-center text-xs text-ink-3">
            Could not load history: {history.error}
          </div>
        ) : events.length === 0 && !history.loading ? (
          <EmptyState
            title="No guideline updates yet"
            body="The ACON loop updates extraction guidelines after repeated compression-implicated failures. A quiet timeline means compression has not caused failures."
          />
        ) : (
          <GuidelineTimeline history={events} />
        )}
      </Card>

      <Card
        title="Current guidelines"
        subtitle="Learned (tool, chunk type) score multipliers — capped at 3.00x"
        className="xl:col-span-2"
        dimmed={guidelines.loading && rows.length > 0}
      >
        {rows.length === 0 && !guidelines.loading ? (
          <EmptyState
            title="No learned guidelines"
            body="All chunk types are scored at their base relevance. Multipliers appear here once the failure loop learns from evidence."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-hairline text-ink-3">
                  <th className="px-3 py-2 font-medium">Tool</th>
                  <th className="px-3 py-2 font-medium">Chunk type</th>
                  <th className="px-3 py-2 font-medium">Multiplier</th>
                  <th className="px-3 py-2 text-right font-medium">Updates</th>
                  <th className="px-3 py-2 font-medium">Last updated</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((g) => (
                  <tr
                    key={`${g.tool_name}-${g.chunk_type}`}
                    className="border-b border-hairline last:border-b-0"
                  >
                    <td className="px-3 py-2 font-mono text-ink">{g.tool_name}</td>
                    <td className="px-3 py-2 font-mono text-ink-2">{g.chunk_type}</td>
                    <td className="px-3 py-2">
                      <MultiplierMeter value={g.score_multiplier} />
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-ink-2">
                      {g.update_count}
                    </td>
                    <td className="px-3 py-2 text-ink-2">{formatDateTime(g.last_updated)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
