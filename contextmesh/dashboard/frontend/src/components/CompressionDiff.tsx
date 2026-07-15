import { api, type DiffChunk } from "../api";
import { formatDateTime, percent, shortId, wholeNumber } from "../format";
import { useApi } from "../hooks";
import { CheckIcon, MinusCircleIcon } from "./icons";

interface CompressionDiffProps {
  /** Trace to inspect; fetched from GET /api/traces/{trace_id}/diff. */
  traceId: string;
}

/**
 * One chunk in the diff browser. Kept chunks carry an accent left border;
 * pruned chunks are muted with a struck identifier. Identity is never color
 * alone — each column is headed by an icon + text label, and every card sits
 * under its column heading.
 */
function ChunkCard({ chunk, kept }: { chunk: DiffChunk; kept: boolean }) {
  return (
    <li
      className={
        kept
          ? "rounded-md border border-hairline bg-surface p-2.5"
          : "rounded-md border border-hairline p-2.5 opacity-75"
      }
      style={kept ? { borderLeft: "2px solid var(--cm-accent)" } : undefined}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span
          className={
            kept
              ? "rounded border border-hairline bg-accent-soft px-1.5 py-0.5 font-mono text-[11px] text-ink"
              : "rounded border border-hairline px-1.5 py-0.5 font-mono text-[11px] text-ink-2"
          }
        >
          {chunk.chunk_type}
        </span>
        <span
          className={
            kept
              ? "font-mono text-[11px] text-ink-3"
              : "font-mono text-[11px] text-ink-3 line-through"
          }
        >
          {shortId(chunk.chunk_id, 16)}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-2 text-[11px] tabular-nums text-ink-2">
          {chunk.score !== null && <span>score {chunk.score.toFixed(2)}</span>}
          <span>{wholeNumber(chunk.token_count)} tok</span>
        </span>
      </div>
      <pre
        className={
          "mt-2 max-h-36 overflow-auto whitespace-pre-wrap break-words rounded bg-page px-2 py-1.5 font-mono text-[11px] leading-relaxed " +
          (kept ? "text-ink-2" : "text-ink-3")
        }
      >
        {chunk.preview || "(no preview)"}
      </pre>
    </li>
  );
}

/**
 * Real compression diff for a single trace: what the compressor kept vs what
 * it pruned, with chunk previews. Two columns on wide viewports, stacked on
 * narrow. Fetches its own data so it can be dropped into any drill-down spot
 * (session trace rows, failure detail).
 */
export function CompressionDiff({ traceId }: CompressionDiffProps) {
  const { data, error, errorStatus, loading } = useApi(
    () => api.traceDiff(traceId),
    [traceId],
  );

  if (loading && !data) {
    return <div className="px-1 py-2 text-xs text-ink-3">Loading diff…</div>;
  }
  if (errorStatus === 404) {
    // Older traces were recorded before chunk previews were stored.
    return (
      <div className="px-1 py-3 text-xs">
        <div className="font-semibold text-ink">No diff available for this trace</div>
        <p className="mt-1 max-w-md leading-relaxed text-ink-3">
          The server has no stored chunk previews for it — traces recorded before
          preview storage was enabled can&apos;t be diffed.
        </p>
        {error && <p className="mt-1 font-mono text-[11px] text-ink-3">{error}</p>}
      </div>
    );
  }
  if (error) {
    return (
      <div className="px-1 py-2 text-xs text-ink-3">Could not load diff: {error}</div>
    );
  }
  if (!data) return null;

  const kept = data.chunks.filter((c) => c.selected);
  const pruned = data.chunks.filter((c) => !c.selected);
  const keptTokens = kept.reduce((sum, c) => sum + c.token_count, 0);
  const prunedTokens = pruned.reduce((sum, c) => sum + c.token_count, 0);

  return (
    <div className="space-y-3">
      {/* Trace summary + totals */}
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs text-ink-2">
        <span className="font-mono font-semibold text-ink">{data.tool_name}</span>
        <span className="font-mono">task {shortId(data.task_id, 18)}</span>
        <span>{formatDateTime(data.timestamp)}</span>
        <span className="tabular-nums">
          {wholeNumber(data.original_tokens)} → {wholeNumber(data.compressed_tokens)} tokens
          {" "}({percent(data.compression_ratio)} of original)
        </span>
        <span className="tabular-nums font-medium text-ink">
          kept {kept.length} / pruned {pruned.length}
        </span>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <section>
          <header className="mb-1.5 flex items-baseline gap-1.5 text-xs font-medium text-ink">
            <span className="self-center text-accent">
              <CheckIcon size={13} />
            </span>
            Kept
            <span className="font-normal tabular-nums text-ink-3">
              {kept.length} {kept.length === 1 ? "chunk" : "chunks"} ·{" "}
              {wholeNumber(keptTokens)} tokens
            </span>
          </header>
          {kept.length === 0 ? (
            <div className="rounded-md border border-hairline px-3 py-2 text-xs text-ink-3">
              Nothing kept — every chunk was pruned.
            </div>
          ) : (
            <ul className="space-y-2">
              {kept.map((c) => (
                <ChunkCard key={c.chunk_id} chunk={c} kept />
              ))}
            </ul>
          )}
        </section>

        <section>
          <header className="mb-1.5 flex items-baseline gap-1.5 text-xs font-medium text-ink">
            <span className="self-center text-ink-3">
              <MinusCircleIcon size={13} />
            </span>
            Pruned
            <span className="font-normal tabular-nums text-ink-3">
              {pruned.length} {pruned.length === 1 ? "chunk" : "chunks"} ·{" "}
              {wholeNumber(prunedTokens)} tokens
            </span>
          </header>
          {pruned.length === 0 ? (
            <div className="rounded-md border border-hairline px-3 py-2 text-xs text-ink-3">
              Nothing pruned — every chunk was kept.
            </div>
          ) : (
            <ul className="space-y-2">
              {pruned.map((c) => (
                <ChunkCard key={c.chunk_id} chunk={c} kept={false} />
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
