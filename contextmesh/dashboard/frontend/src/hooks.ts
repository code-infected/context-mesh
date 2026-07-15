import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";
import { ApiError } from "./api";

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  /** HTTP status when the failure was an API response (e.g. 404), else null. */
  errorStatus: number | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Minimal data-fetching hook. On refetch the previous data is kept (consumers
 * dim it with reduced opacity) so there is no skeleton flash or layout jump.
 *
 * Pass `pollMs` to refetch on an interval. Polls run in the background: they
 * never toggle `loading` (no dim pulse), they skip a tick if a fetch is
 * already in flight (no overlapping requests), and the interval is cleared on
 * unmount. A stale response (superseded by a newer fetch or unmount) is
 * discarded via a generation counter.
 */
export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: DependencyList = [],
  pollMs?: number,
): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  /** Bumped whenever a fetch is superseded; stale settles are ignored. */
  const generationRef = useRef(0);
  const inFlightRef = useRef(false);

  const runFetch = useCallback((background: boolean) => {
    const gen = ++generationRef.current;
    inFlightRef.current = true;
    if (!background) {
      setLoading(true);
      setError(null);
      setErrorStatus(null);
    }
    fetcherRef
      .current()
      .then((result) => {
        if (gen !== generationRef.current) return;
        setData(result);
        setError(null);
        setErrorStatus(null);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (gen !== generationRef.current) return;
        setError(err instanceof Error ? err.message : String(err));
        setErrorStatus(err instanceof ApiError ? err.status : null);
        setLoading(false);
      })
      .finally(() => {
        if (gen === generationRef.current) inFlightRef.current = false;
      });
  }, []);

  // Foreground fetch on mount / deps change / manual reload.
  useEffect(() => {
    runFetch(false);
    return () => {
      // Invalidate any in-flight fetch (deps changed or unmounted).
      generationRef.current += 1;
      inFlightRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  // Background polling.
  useEffect(() => {
    if (!pollMs || pollMs <= 0) return;
    const id = window.setInterval(() => {
      if (!inFlightRef.current) runFetch(true);
    }, pollMs);
    return () => window.clearInterval(id);
  }, [pollMs, runFetch]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return { data, error, errorStatus, loading, reload };
}

/** Re-runs `reload` every `ms` milliseconds (health polling). */
export function useInterval(callback: () => void, ms: number): void {
  const ref = useRef(callback);
  ref.current = callback;
  useEffect(() => {
    const id = window.setInterval(() => ref.current(), ms);
    return () => window.clearInterval(id);
  }, [ms]);
}
