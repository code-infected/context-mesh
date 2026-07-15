import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type ToolStats } from "../api";
import { Card } from "../components/Card";
import { ChartLegend } from "../components/ChartLegend";
import { ChartTooltip } from "../components/ChartTooltip";
import { EmptyState, ErrorState } from "../components/EmptyState";
import { AlertTriangleIcon } from "../components/icons";
import { percent, wholeNumber } from "../format";
import { useApi } from "../hooks";
import { useTheme } from "../theme";

/** Height that always includes the x-axis band (36px per row + axis). */
function chartHeight(rows: number): number {
  return Math.max(160, rows * 40 + 40);
}

function RatioChart({ tools }: { tools: ToolStats[] }) {
  const { palette } = useTheme();
  const data = [...tools]
    .sort((a, b) => a.avg_compression_ratio - b.avg_compression_ratio)
    .map((t) => ({ tool: t.tool_name, ratio: t.avg_compression_ratio }));

  return (
    <ResponsiveContainer width="100%" height={chartHeight(data.length)}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 4, right: 56, bottom: 4, left: 8 }}
      >
        <CartesianGrid horizontal={false} stroke={palette.grid} strokeWidth={1} />
        <XAxis
          type="number"
          domain={[0, 1]}
          ticks={[0, 0.25, 0.5, 0.75, 1]}
          tickFormatter={(v: number) => percent(v, 0)}
          tick={{ fill: palette.inkMuted, fontSize: 11 }}
          axisLine={{ stroke: palette.axisLine, strokeWidth: 1 }}
          tickLine={false}
        />
        <YAxis
          type="category"
          dataKey="tool"
          width={130}
          tick={{ fill: palette.inkSecondary, fontSize: 11 }}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip
          cursor={{ fill: palette.hoverWash }}
          content={<ChartTooltip valueFormatter={(v) => `${percent(v)} of original`} />}
        />
        <Bar
          dataKey="ratio"
          name="Avg compression ratio"
          fill={palette.series1}
          radius={[0, 3, 3, 0]}
          maxBarSize={18}
        >
          {/* Value at the bar tip, in ink — text never wears the series color. */}
          <LabelList
            dataKey="ratio"
            position="right"
            formatter={(v: unknown) => percent(Number(v))}
            style={{ fill: palette.inkSecondary, fontSize: 11 }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function ChunksChart({ tools }: { tools: ToolStats[] }) {
  const { palette } = useTheme();
  const data = [...tools]
    .sort((a, b) => b.avg_chunks_total - a.avg_chunks_total)
    .map((t) => ({
      tool: t.tool_name,
      selected: t.avg_chunks_selected,
      pruned: Math.max(0, t.avg_chunks_total - t.avg_chunks_selected),
    }));

  return (
    <div>
      <ChartLegend
        items={[
          { label: "Chunks selected", color: palette.series1 },
          { label: "Chunks pruned", color: palette.meterTrack },
        ]}
      />
      <ResponsiveContainer width="100%" height={chartHeight(data.length)}>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 4, right: 16, bottom: 4, left: 8 }}
        >
          <CartesianGrid horizontal={false} stroke={palette.grid} strokeWidth={1} />
          <XAxis
            type="number"
            tick={{ fill: palette.inkMuted, fontSize: 11 }}
            axisLine={{ stroke: palette.axisLine, strokeWidth: 1 }}
            tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="tool"
            width={130}
            tick={{ fill: palette.inkSecondary, fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            cursor={{ fill: palette.hoverWash }}
            content={<ChartTooltip valueFormatter={(v) => `${v.toFixed(1)} chunks`} />}
          />
          {/* Surface-colored stroke = the 2px gap between stacked segments. */}
          <Bar
            dataKey="selected"
            name="Selected"
            stackId="chunks"
            fill={palette.series1}
            stroke={palette.surface}
            strokeWidth={1}
            maxBarSize={18}
          />
          <Bar
            dataKey="pruned"
            name="Pruned"
            stackId="chunks"
            fill={palette.meterTrack}
            stroke={palette.surface}
            strokeWidth={1}
            radius={[0, 3, 3, 0]}
            maxBarSize={18}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ToolBreakdown() {
  const { data, error, loading, reload } = useApi(api.toolStats, [], 15_000);
  const { palette } = useTheme();

  if (error) return <ErrorState message={error} onRetry={reload} />;

  const tools = data?.tools ?? [];
  const dimmed = loading && tools.length > 0;

  if (!loading && tools.length === 0) {
    return (
      <Card title="Tool breakdown">
        <EmptyState
          title="No tool statistics yet"
          body="Per-tool compression ratios appear after the first tool calls flow through ContextMesh."
        />
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card
          title="Avg compression ratio by tool"
          subtitle="Compressed size as a share of the original — lower is better"
          dimmed={dimmed}
        >
          {tools.length === 0 ? (
            <div className="py-8 text-center text-xs text-ink-3">Loading…</div>
          ) : (
            <RatioChart tools={tools} />
          )}
        </Card>
        <Card
          title="Avg chunks selected vs total"
          subtitle="How much of each tool's output survives extraction"
          dimmed={dimmed}
        >
          {tools.length === 0 ? (
            <div className="py-8 text-center text-xs text-ink-3">Loading…</div>
          ) : (
            <ChunksChart tools={tools} />
          )}
        </Card>
      </div>

      <Card title="Tool statistics" dimmed={dimmed}>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-hairline text-ink-3">
                <th className="px-3 py-2 font-medium">Tool</th>
                <th className="px-3 py-2 text-right font-medium">Calls</th>
                <th className="px-3 py-2 text-right font-medium">Avg ratio</th>
                <th className="px-3 py-2 text-right font-medium">Original</th>
                <th className="px-3 py-2 text-right font-medium">Compressed</th>
                <th className="px-3 py-2 text-right font-medium">Saved</th>
                <th className="px-3 py-2 text-right font-medium">Avg chunks</th>
                <th className="px-3 py-2 text-right font-medium">Failures</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {tools.map((t) => (
                <tr key={t.tool_name} className="border-b border-hairline last:border-b-0">
                  <td className="px-3 py-2 font-mono text-ink">{t.tool_name}</td>
                  <td className="px-3 py-2 text-right text-ink-2">{wholeNumber(t.call_count)}</td>
                  <td className="px-3 py-2 text-right text-ink-2">
                    {percent(t.avg_compression_ratio)}
                  </td>
                  <td className="px-3 py-2 text-right text-ink-2">
                    {wholeNumber(t.original_tokens)}
                  </td>
                  <td className="px-3 py-2 text-right text-ink-2">
                    {wholeNumber(t.compressed_tokens)}
                  </td>
                  <td className="px-3 py-2 text-right font-medium text-ink">
                    {wholeNumber(t.tokens_saved)}
                  </td>
                  <td className="px-3 py-2 text-right text-ink-2">
                    {t.avg_chunks_selected.toFixed(1)}/{t.avg_chunks_total.toFixed(1)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {t.failure_count > 0 ? (
                      <span className="inline-flex items-center gap-1 text-ink">
                        <span style={{ color: palette.critical }}>
                          <AlertTriangleIcon size={11} />
                        </span>
                        {t.failure_count}
                      </span>
                    ) : (
                      <span className="text-ink-3">0</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
