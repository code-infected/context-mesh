import { useMemo, useState } from "react";
import { api, type FailureRecord } from "../api";
import { Card } from "../components/Card";
import { CompressionDiff } from "../components/CompressionDiff";
import { EmptyState, ErrorState } from "../components/EmptyState";
import { AlertTriangleIcon, CheckIcon, MinusCircleIcon } from "../components/icons";
import { formatDateTime, shortId } from "../format";
import { useApi } from "../hooks";
import { useTheme } from "../theme";

type Filter = "all" | "implicated";

/**
 * Right-hand detail for a flagged failure: header + reason + the chunk types
 * the detector implicated, then the *real* compression diff (chunk previews
 * from GET /api/traces/{id}/diff) for each linked trace.
 */
function FailureDetail({ failure }: { failure: FailureRecord }) {
  const { palette } = useTheme();
  const [traceId, setTraceId] = useState<string | null>(failure.trace_ids[0] ?? null);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-mono text-sm font-semibold text-ink">{failure.task_id}</h3>
          <p className="mt-0.5 text-xs text-ink-3">
            Session <span className="font-mono">{shortId(failure.session_id, 16)}</span>
            {" · "}
            {formatDateTime(failure.timestamp)}
          </p>
        </div>
        {failure.compression_implicated ? (
          <span
            className="flex items-center gap-1.5 rounded-full border border-hairline px-2.5 py-1 text-xs font-medium text-ink"
            style={{ backgroundColor: "rgba(208, 59, 59, 0.10)" }}
          >
            <span style={{ color: palette.critical }}>
              <AlertTriangleIcon size={13} />
            </span>
            Compression implicated
          </span>
        ) : (
          <span className="flex items-center gap-1.5 rounded-full border border-hairline px-2.5 py-1 text-xs font-medium text-ink-2">
            <span style={{ color: palette.good }}>
              <CheckIcon size={13} />
            </span>
            Not implicated
          </span>
        )}
      </header>

      <div
        className="rounded-md px-3 py-2"
        style={{
          backgroundColor: "rgba(208, 59, 59, 0.06)",
          borderLeft: `2px solid ${palette.critical}`,
        }}
      >
        <div className="text-xs font-medium text-ink-2">Failure reason</div>
        <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-ink">
          {failure.failure_reason}
        </pre>
      </div>

      <div className="rounded-md border border-hairline p-3">
        <div className="flex items-center gap-1.5 text-xs font-medium text-ink">
          <span style={{ color: palette.critical }}>
            <MinusCircleIcon size={13} />
          </span>
          Pruned chunk types (implicated)
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {failure.pruned_chunk_types.length === 0 ? (
            <span className="text-xs text-ink-3">None recorded</span>
          ) : (
            failure.pruned_chunk_types.map((t) => (
              <span
                key={t}
                className="rounded border border-hairline px-1.5 py-0.5 font-mono text-xs text-ink"
                style={{ backgroundColor: "rgba(208, 59, 59, 0.08)" }}
              >
                {t}
              </span>
            ))
          )}
        </div>
      </div>

      <div>
        <div className="mb-1.5 text-xs font-medium text-ink-2">
          Compression diff per trace
        </div>
        {failure.trace_ids.length === 0 ? (
          <EmptyState
            title="No traces linked"
            body="This failure has no associated compression traces, so there is no diff to inspect."
          />
        ) : (
          <>
            {failure.trace_ids.length > 1 && (
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                {failure.trace_ids.map((id) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setTraceId(id)}
                    className={
                      traceId === id
                        ? "rounded-md border border-accent bg-accent-soft px-2.5 py-1 font-mono text-[11px] font-semibold text-ink"
                        : "rounded-md border border-hairline bg-surface px-2.5 py-1 font-mono text-[11px] font-medium text-ink-2 hover:bg-accent-soft"
                    }
                  >
                    {shortId(id, 14)}
                  </button>
                ))}
              </div>
            )}
            <div className="rounded-md border border-hairline p-3">
              {traceId && <CompressionDiff key={traceId} traceId={traceId} />}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function FailureAnalysis() {
  const { data, error, loading, reload } = useApi(api.failures, [], 15_000);
  const { palette } = useTheme();
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const failures = useMemo(() => {
    const all = data?.failures ?? [];
    const filtered = filter === "implicated" ? all.filter((f) => f.compression_implicated) : all;
    return [...filtered].sort(
      (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
    );
  }, [data, filter]);

  const selected: FailureRecord | null =
    failures.find((f) => f.task_id === selectedId) ?? failures[0] ?? null;

  if (error) return <ErrorState message={error} onRetry={reload} />;

  const total = data?.failures.length ?? 0;
  const implicatedCount = data?.failures.filter((f) => f.compression_implicated).length ?? 0;

  if (!loading && total === 0) {
    return (
      <Card title="Failure analysis">
        <EmptyState
          title="No failures flagged"
          body="When the failure detector links a task failure to compression, it will appear here with a diff of what was pruned vs kept."
        />
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      {/* Filter row — one row, above everything it scopes. */}
      <div className="flex items-center gap-2">
        {(
          [
            { id: "all", label: `All (${total})` },
            { id: "implicated", label: `Compression implicated (${implicatedCount})` },
          ] as Array<{ id: Filter; label: string }>
        ).map((opt) => (
          <button
            key={opt.id}
            type="button"
            onClick={() => setFilter(opt.id)}
            className={
              filter === opt.id
                ? "rounded-md border border-hairline bg-accent-soft px-3 py-1.5 text-xs font-semibold text-ink"
                : "rounded-md border border-hairline bg-surface px-3 py-1.5 text-xs font-medium text-ink-2 hover:bg-accent-soft"
            }
          >
            {opt.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <Card title="Flagged tasks" className="lg:col-span-2" dimmed={loading && total > 0}>
          {failures.length === 0 ? (
            <EmptyState
              title="Nothing matches this filter"
              body="No failures where compression was implicated. That is the state you want."
            />
          ) : (
            <ul className="space-y-2">
              {failures.map((f) => {
                const isSelected = selected?.task_id === f.task_id;
                return (
                  <li key={f.task_id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(f.task_id)}
                      className={
                        isSelected
                          ? "w-full rounded-md border border-accent bg-accent-soft px-3 py-2 text-left"
                          : "w-full rounded-md border border-hairline px-3 py-2 text-left hover:bg-accent-soft"
                      }
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-xs font-semibold text-ink">
                          {shortId(f.task_id, 22)}
                        </span>
                        <span className="shrink-0 text-[11px] text-ink-3">
                          {formatDateTime(f.timestamp)}
                        </span>
                      </div>
                      <p className="mt-1 line-clamp-2 text-xs text-ink-2">
                        {f.failure_reason}
                      </p>
                      {f.compression_implicated && (
                        <span className="mt-1.5 flex w-fit items-center gap-1 rounded-full border border-hairline px-1.5 py-0.5 text-[11px] font-medium text-ink-2">
                          <span style={{ color: palette.critical }}>
                            <AlertTriangleIcon size={11} />
                          </span>
                          compression implicated
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card
          title="Compression diff"
          subtitle="What the compressor kept vs pruned, with chunk previews"
          className="lg:col-span-3"
        >
          {selected ? (
            <FailureDetail key={selected.task_id} failure={selected} />
          ) : (
            <EmptyState
              title="Select a task"
              body="Pick a flagged task on the left to inspect its compression decisions."
            />
          )}
        </Card>
      </div>
    </div>
  );
}
