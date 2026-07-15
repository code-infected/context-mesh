import { Fragment, useState } from "react";
import { api, type SessionSummary } from "../api";
import { Card } from "../components/Card";
import { CompressionDiff } from "../components/CompressionDiff";
import { EmptyState, ErrorState } from "../components/EmptyState";
import { StatTile } from "../components/StatTile";
import { TokenChart } from "../components/TokenChart";
import { AlertTriangleIcon, ChevronRightIcon } from "../components/icons";
import {
  compactNumber,
  formatDateTime,
  percent,
  shortId,
  wholeNumber,
  withinMinutes,
} from "../format";
import { useApi } from "../hooks";
import { useTheme } from "../theme";

/** Expanded per-session trace list (uses GET /api/sessions/{id}/traces). */
function SessionTraces({ sessionId }: { sessionId: string }) {
  const { palette } = useTheme();
  const { data, error, loading } = useApi(() => api.sessionTraces(sessionId), [sessionId]);
  const [onlyLowSignal, setOnlyLowSignal] = useState(false);
  const [openTraceId, setOpenTraceId] = useState<string | null>(null);

  if (error) {
    return <div className="px-3 py-2 text-xs text-ink-3">Could not load traces: {error}</div>;
  }
  if (loading && !data) {
    return <div className="px-3 py-2 text-xs text-ink-3">Loading traces…</div>;
  }
  const traces = data?.traces ?? [];
  if (traces.length === 0) {
    return <div className="px-3 py-2 text-xs text-ink-3">No traces recorded for this session.</div>;
  }

  const lowSignalCount = traces.filter((t) => t.low_signal).length;
  const visible = onlyLowSignal ? traces.filter((t) => t.low_signal) : traces;

  return (
    <div>
      {/* Filter row — one row, above the listing it scopes. */}
      <div className="flex flex-wrap items-center gap-2 px-3 pt-2">
        <button
          type="button"
          aria-pressed={onlyLowSignal}
          onClick={() => setOnlyLowSignal((v) => !v)}
          className={
            onlyLowSignal
              ? "rounded-md border border-hairline bg-accent-soft px-2.5 py-1 text-[11px] font-semibold text-ink"
              : "rounded-md border border-hairline bg-surface px-2.5 py-1 text-[11px] font-medium text-ink-2 hover:bg-accent-soft"
          }
        >
          Only low-signal ({lowSignalCount})
        </button>
        <span className="text-[11px] text-ink-3">
          Click a trace to see what was kept vs pruned
        </span>
      </div>
      {visible.length === 0 ? (
        <div className="px-3 py-2 text-xs text-ink-3">
          No low-signal traces in this session.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="text-ink-3">
                <th className="px-3 py-1.5 font-medium" />
                <th className="px-3 py-1.5 font-medium">Tool</th>
                <th className="px-3 py-1.5 font-medium">Time</th>
                <th className="px-3 py-1.5 text-right font-medium">Original</th>
                <th className="px-3 py-1.5 text-right font-medium">Compressed</th>
                <th className="px-3 py-1.5 text-right font-medium">Ratio</th>
                <th className="px-3 py-1.5 text-right font-medium">Chunks</th>
                <th className="px-3 py-1.5 font-medium">Flags</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {visible.map((t) => {
                const isOpen = openTraceId === t.trace_id;
                return (
                  <Fragment key={t.trace_id}>
                    <tr
                      className="cursor-pointer border-t border-hairline hover:bg-accent-soft"
                      onClick={() => setOpenTraceId(isOpen ? null : t.trace_id)}
                    >
                      <td className="w-6 px-3 py-1.5 text-ink-3">
                        <span
                          className={
                            isOpen
                              ? "inline-block rotate-90 transition-transform"
                              : "inline-block transition-transform"
                          }
                        >
                          <ChevronRightIcon size={11} />
                        </span>
                      </td>
                      <td className="px-3 py-1.5 font-mono text-ink">{t.tool_name}</td>
                      <td className="px-3 py-1.5 text-ink-2">{formatDateTime(t.timestamp)}</td>
                      <td className="px-3 py-1.5 text-right text-ink-2">
                        {wholeNumber(t.original_tokens)}
                      </td>
                      <td className="px-3 py-1.5 text-right text-ink-2">
                        {wholeNumber(t.compressed_tokens)}
                      </td>
                      <td className="px-3 py-1.5 text-right text-ink-2">
                        {percent(t.compression_ratio)}
                      </td>
                      <td className="px-3 py-1.5 text-right text-ink-2">
                        {t.chunks_selected}/{t.chunks_total}
                      </td>
                      <td className="px-3 py-1.5">
                        {t.low_signal && (
                          <span className="flex w-fit items-center gap-1 rounded-full border border-hairline px-1.5 py-0.5 text-[11px] font-medium text-ink-2">
                            <span style={{ color: palette.warning }}>
                              <AlertTriangleIcon size={11} />
                            </span>
                            low signal
                          </span>
                        )}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="border-t border-hairline">
                        <td colSpan={8} className="px-3 py-2">
                          <div className="rounded-md border border-hairline bg-surface p-3">
                            <CompressionDiff key={t.trace_id} traceId={t.trace_id} />
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function SessionOverview() {
  const { data, error, loading, reload } = useApi(api.sessions, [], 15_000);
  // All KPI aggregates come from the single overview endpoint — no per-session
  // trace fan-out (per-session traces are only fetched for the expanded rows).
  const overview = useApi(api.statsOverview, [], 15_000);
  const { palette } = useTheme();
  const [expanded, setExpanded] = useState<string | null>(null);

  if (error) return <ErrorState message={error} onRetry={reload} />;

  const sessions: SessionSummary[] = data?.sessions ?? [];
  const activeCount = sessions.filter((s) => withinMinutes(s.last_seen, 60)).length;
  const ov = overview.data;

  const chartData = [...sessions]
    .sort((a, b) => new Date(a.last_seen).getTime() - new Date(b.last_seen).getTime())
    .map((s) => ({
      label: shortId(s.session_id, 10),
      original: s.original_tokens,
      compressed: s.compressed_tokens,
    }));

  const dimmed = loading && sessions.length > 0;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile
          label="Total tokens saved"
          value={ov ? compactNumber(ov.tokens_saved) : "—"}
          hint={ov ? `of ${compactNumber(ov.original_tokens)} original` : "across all sessions"}
        />
        <StatTile
          label="Avg compression ratio"
          value={ov ? percent(ov.avg_compression_ratio) : "—"}
          hint="compressed / original"
        />
        <StatTile
          label="Sessions"
          value={ov ? compactNumber(ov.sessions) : "—"}
          hint={
            ov
              ? `${activeCount} active · ${compactNumber(ov.tasks)} tasks`
              : `${activeCount} active in the last hour`
          }
        />
        <StatTile
          label="Low-signal traces"
          value={ov ? compactNumber(ov.low_signal_traces) : "—"}
          hint="vague task descriptions"
        />
        <StatTile
          label="Failures"
          value={ov ? compactNumber(ov.failures) : "—"}
          hint={
            ov
              ? `${compactNumber(ov.compression_implicated_failures)} compression-implicated`
              : "flagged tasks"
          }
        />
        <StatTile
          label="Active guidelines"
          value={ov ? compactNumber(ov.guidelines_active) : "—"}
          hint="learned multipliers"
        />
      </div>

      {/* Ops badges: trace backend + scorer mode (icon + text, never color alone). */}
      <div className="flex flex-wrap items-center gap-2">
        {ov && (
          <span className="rounded-full border border-hairline bg-surface px-2.5 py-1 text-[11px] text-ink-2">
            trace backend: <span className="font-mono font-medium text-ink">{ov.trace_backend}</span>
          </span>
        )}
        {ov?.scorer_fallback === true && (
          <span className="flex items-center gap-1.5 rounded-full border border-hairline bg-surface px-2.5 py-1 text-[11px] font-medium text-ink-2">
            <span style={{ color: palette.warning }}>
              <AlertTriangleIcon size={11} />
            </span>
            lexical fallback scorer
          </span>
        )}
        {overview.error && (
          <span className="rounded-full border border-hairline bg-surface px-2.5 py-1 text-[11px] text-ink-3">
            overview stats unavailable: {overview.error}
          </span>
        )}
      </div>

      <Card
        title="Tokens per session"
        subtitle="Original vs compressed tokens, ordered by last activity"
        dimmed={dimmed}
      >
        {sessions.length === 0 ? (
          <EmptyState
            title="No sessions yet"
            body="Once an agent runs through the ContextMesh proxy or SDK, its sessions and token savings will show up here."
          />
        ) : (
          <TokenChart data={chartData} />
        )}
      </Card>

      <Card
        title="Sessions"
        subtitle="Click a row to inspect its compression traces"
        dimmed={dimmed}
      >
        {sessions.length === 0 ? (
          <EmptyState
            title="Nothing to list"
            body="A fresh install has no traces. Point an agent at the proxy (port 8081) and this table will populate."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-hairline text-ink-3">
                  <th className="px-3 py-2 font-medium" />
                  <th className="px-3 py-2 font-medium">Session</th>
                  <th className="px-3 py-2 text-right font-medium">Tasks</th>
                  <th className="px-3 py-2 text-right font-medium">Traces</th>
                  <th className="px-3 py-2 text-right font-medium">Original</th>
                  <th className="px-3 py-2 text-right font-medium">Compressed</th>
                  <th className="px-3 py-2 text-right font-medium">Saved</th>
                  <th className="px-3 py-2 text-right font-medium">Avg ratio</th>
                  <th className="px-3 py-2 font-medium">Last seen</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {sessions.map((s) => {
                  const isOpen = expanded === s.session_id;
                  return (
                    <Fragment key={s.session_id}>
                      <tr
                        className="cursor-pointer border-b border-hairline hover:bg-accent-soft"
                        onClick={() => setExpanded(isOpen ? null : s.session_id)}
                      >
                        <td className="w-6 px-3 py-2 text-ink-3">
                          <span className={isOpen ? "inline-block rotate-90 transition-transform" : "inline-block transition-transform"}>
                            <ChevronRightIcon size={12} />
                          </span>
                        </td>
                        <td className="px-3 py-2 font-mono text-ink">
                          {shortId(s.session_id, 20)}
                        </td>
                        <td className="px-3 py-2 text-right text-ink-2">{s.task_count}</td>
                        <td className="px-3 py-2 text-right text-ink-2">{s.trace_count}</td>
                        <td className="px-3 py-2 text-right text-ink-2">
                          {wholeNumber(s.original_tokens)}
                        </td>
                        <td className="px-3 py-2 text-right text-ink-2">
                          {wholeNumber(s.compressed_tokens)}
                        </td>
                        <td className="px-3 py-2 text-right font-medium text-ink">
                          {wholeNumber(s.tokens_saved)}
                        </td>
                        <td className="px-3 py-2 text-right text-ink-2">
                          {percent(s.avg_compression_ratio)}
                        </td>
                        <td className="px-3 py-2 text-ink-2">{formatDateTime(s.last_seen)}</td>
                      </tr>
                      {isOpen && (
                        <tr className="border-b border-hairline bg-page">
                          <td colSpan={9} className="px-2 py-1">
                            <SessionTraces sessionId={s.session_id} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
