import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type CompressResponse } from "../api";
import { Card } from "../components/Card";
import { CompressionDiff } from "../components/CompressionDiff";
import { EmptyState } from "../components/EmptyState";
import { StatTile } from "../components/StatTile";
import { CheckIcon, CopyIcon } from "../components/icons";
import { compactNumber, percent } from "../format";
import { useTheme } from "../theme";

const fieldClass =
  "w-full rounded-md border border-hairline bg-page px-2.5 py-1.5 text-xs text-ink " +
  "placeholder:text-ink-3 focus:border-accent focus:outline-none";

function FieldLabel({ children, htmlFor }: { children: string; htmlFor: string }) {
  return (
    <label htmlFor={htmlFor} className="mb-1 block text-xs font-medium text-ink-2">
      {children}
    </label>
  );
}

/** Copy-to-clipboard button with a transient "Copied" confirmation. */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (permissions / insecure context) — no-op */
    }
  };

  return (
    <button
      type="button"
      onClick={copy}
      className="flex shrink-0 items-center gap-1.5 rounded-md border border-hairline bg-surface px-2.5 py-1 text-[11px] font-medium text-ink-2 hover:bg-accent-soft hover:text-ink"
    >
      {copied ? (
        <>
          <span className="text-accent">
            <CheckIcon size={12} />
          </span>
          Copied
        </>
      ) : (
        <>
          <CopyIcon size={12} />
          Copy
        </>
      )}
    </button>
  );
}

/** Stat tiles + compressed output + kept-vs-pruned diff for one run. */
function ResultPanel({ result, dimmed }: { result: CompressResponse; dimmed: boolean }) {
  const saved = Math.max(0, 1 - result.compression_ratio);

  return (
    <div className={dimmed ? "space-y-4 opacity-60 transition-opacity" : "space-y-4 transition-opacity"}>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatTile
          label="Tokens"
          value={`${compactNumber(result.original_tokens)} → ${compactNumber(result.compressed_tokens)}`}
          hint="original → compressed"
        />
        <StatTile
          label="Saved"
          value={percent(saved)}
          hint={`compressed to ${percent(result.compression_ratio)} of original`}
        />
        <StatTile
          label="Chunks kept"
          value={`${result.chunks_selected}/${result.chunks_total}`}
          hint="selected / total"
        />
      </div>

      <Card
        title="Compressed output"
        subtitle="What the model would receive in place of the raw tool output"
      >
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          {result.chunk_types_selected.length > 0 && (
            <>
              <span className="text-[11px] text-ink-3">Chunk types kept:</span>
              {result.chunk_types_selected.map((t) => (
                <span
                  key={t}
                  className="rounded border border-hairline bg-accent-soft px-1.5 py-0.5 font-mono text-[11px] text-ink"
                >
                  {t}
                </span>
              ))}
            </>
          )}
          <span className="ml-auto">
            <CopyButton text={result.compressed_output} />
          </span>
        </div>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border border-hairline bg-page px-3 py-2 font-mono text-xs leading-relaxed text-ink-2">
          {result.compressed_output || "(empty output)"}
        </pre>
      </Card>

      <Card
        title="Kept vs pruned"
        subtitle="Chunk-level decisions for this run, with previews"
      >
        {result.trace_id !== null ? (
          <CompressionDiff key={result.trace_id} traceId={result.trace_id} />
        ) : (
          <div className="px-1 py-2 text-xs text-ink-3">
            No trace recorded (output too small or compression skipped), so there is no
            kept-vs-pruned diff for this run.
          </div>
        )}
      </Card>
    </div>
  );
}

/** Interactive compression playground: POST /api/compress on arbitrary input. */
export function Playground() {
  const { palette } = useTheme();
  const [taskDescription, setTaskDescription] = useState("");
  const [toolName, setToolName] = useState("read_file");
  const [budgetTokens, setBudgetTokens] = useState(4000);
  const [rawOutput, setRawOutput] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CompressResponse | null>(null);

  const canSubmit =
    !running &&
    rawOutput.trim().length > 0 &&
    toolName.trim().length > 0 &&
    budgetTokens > 0;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setRunning(true);
    setError(null);
    try {
      const res = await api.compress({
        session_id: "playground",
        task_id: `playground-${Date.now()}`,
        tool_name: toolName.trim(),
        tool_args: {},
        raw_output: rawOutput,
        task_description: taskDescription,
        recent_steps: [],
        budget_tokens: budgetTokens,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card
        title="Try compression"
        subtitle="Paste any raw tool output and see what ContextMesh keeps for the given task"
      >
        <form onSubmit={submit} className="space-y-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,1fr)_200px_140px]">
            <div>
              <FieldLabel htmlFor="pg-task">Task description</FieldLabel>
              <input
                id="pg-task"
                type="text"
                value={taskDescription}
                onChange={(e) => setTaskDescription(e.target.value)}
                placeholder="e.g. Find where the retry limit is configured"
                className={fieldClass}
              />
            </div>
            <div>
              <FieldLabel htmlFor="pg-tool">Tool name</FieldLabel>
              <input
                id="pg-tool"
                type="text"
                value={toolName}
                onChange={(e) => setToolName(e.target.value)}
                placeholder="read_file"
                className={`${fieldClass} font-mono`}
              />
            </div>
            <div>
              <FieldLabel htmlFor="pg-budget">Budget tokens</FieldLabel>
              <input
                id="pg-budget"
                type="number"
                min={1}
                step={100}
                value={budgetTokens}
                onChange={(e) => {
                  const n = e.target.valueAsNumber;
                  setBudgetTokens(Number.isFinite(n) ? n : 0);
                }}
                className={`${fieldClass} tabular-nums`}
              />
            </div>
          </div>

          <div>
            <FieldLabel htmlFor="pg-raw">Raw tool output</FieldLabel>
            <textarea
              id="pg-raw"
              value={rawOutput}
              onChange={(e) => setRawOutput(e.target.value)}
              rows={12}
              spellCheck={false}
              placeholder="Paste the raw tool output to compress…"
              className={`${fieldClass} resize-y font-mono leading-relaxed`}
            />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="submit"
              disabled={!canSubmit}
              className={
                canSubmit
                  ? "rounded-md bg-accent px-4 py-1.5 text-xs font-semibold text-white hover:opacity-90"
                  : "cursor-not-allowed rounded-md bg-accent px-4 py-1.5 text-xs font-semibold text-white opacity-40"
              }
            >
              {running ? "Compressing…" : "Compress"}
            </button>
            {running && (
              <span className="flex items-center gap-1.5 text-xs text-ink-3" role="status">
                <span
                  className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"
                  aria-hidden
                />
                Running — a cold start can take several seconds.
              </span>
            )}
          </div>

          {error && (
            <div
              className="rounded-md px-3 py-2"
              style={{
                backgroundColor: "rgba(208, 59, 59, 0.06)",
                borderLeft: `2px solid ${palette.critical}`,
              }}
            >
              <div className="text-xs font-medium text-ink-2">Compression request failed</div>
              <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-ink">
                {error}
              </pre>
            </div>
          )}
        </form>
      </Card>

      {result ? (
        <ResultPanel result={result} dimmed={running} />
      ) : (
        !running && (
          <Card>
            <EmptyState
              title="Nothing compressed yet"
              body="Fill in a task description, paste a raw tool output above, and press Compress to see what ContextMesh would keep, prune, and hand to the model."
            />
          </Card>
        )
      )}
    </div>
  );
}
