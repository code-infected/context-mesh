import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useTheme } from "../theme";
import { compactNumber } from "../format";
import { ChartLegend } from "./ChartLegend";
import { ChartTooltip } from "./ChartTooltip";

export interface TokenChartDatum {
  /** Category label on the x-axis (session id or time bucket). */
  label: string;
  original: number;
  compressed: number;
}

/**
 * Original vs compressed tokens as grouped columns. Before/after pair, so it
 * uses one hue in two shades (validated ordinal ramp) rather than two
 * categorical hues: the pale shade is "before", the saturated shade "after".
 */
export function TokenChart({ data }: { data: TokenChartDatum[] }) {
  const { palette } = useTheme();

  return (
    <div>
      <ChartLegend
        items={[
          { label: "Original tokens", color: palette.tokenBefore },
          { label: "Compressed tokens", color: palette.tokenAfter },
        ]}
      />
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }} barGap={2}>
          <CartesianGrid vertical={false} stroke={palette.grid} strokeWidth={1} />
          <XAxis
            dataKey="label"
            tick={{ fill: palette.inkMuted, fontSize: 11 }}
            axisLine={{ stroke: palette.axisLine, strokeWidth: 1 }}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fill: palette.inkMuted, fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={52}
            tickFormatter={(v: number) => compactNumber(v)}
          />
          <Tooltip
            cursor={{ fill: palette.hoverWash }}
            content={
              <ChartTooltip valueFormatter={(v) => `${compactNumber(v)} tokens`} />
            }
          />
          <Bar
            dataKey="original"
            name="Original"
            fill={palette.tokenBefore}
            radius={[3, 3, 0, 0]}
            maxBarSize={20}
          />
          <Bar
            dataKey="compressed"
            name="Compressed"
            fill={palette.tokenAfter}
            radius={[3, 3, 0, 0]}
            maxBarSize={20}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
