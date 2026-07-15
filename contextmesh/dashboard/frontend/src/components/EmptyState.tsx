import { InboxIcon } from "./icons";

interface EmptyStateProps {
  title: string;
  body: string;
}

/** Friendly empty state for a fresh install (no traces recorded yet). */
export function EmptyState({ title, body }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      <div className="text-ink-3">
        <InboxIcon size={28} />
      </div>
      <h3 className="mt-3 text-sm font-semibold text-ink">{title}</h3>
      <p className="mt-1 max-w-sm text-xs leading-relaxed text-ink-2">{body}</p>
    </div>
  );
}

interface ErrorStateProps {
  message: string;
  onRetry?: () => void;
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      <h3 className="text-sm font-semibold text-ink">Could not reach the dashboard API</h3>
      <p className="mt-1 max-w-md break-all text-xs text-ink-2">{message}</p>
      <p className="mt-1 max-w-md text-xs text-ink-3">
        Make sure the backend is running: uvicorn dashboard.backend.main:app --port 8082
      </p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-md border border-hairline bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-accent-soft"
        >
          Retry
        </button>
      )}
    </div>
  );
}
