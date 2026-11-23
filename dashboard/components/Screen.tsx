"use client";
import type { ApiError } from "@/lib/api";

/** Every screen shows one of: loading, error, no permission, empty, or its content. */
export function Screen({
  loading, error, empty, emptyTitle = "Nothing here yet", emptyHint, onRetry, children,
}: {
  loading?: boolean; error?: ApiError | null; empty?: boolean; emptyTitle?: string; emptyHint?: string; onRetry?: () => void; children?: React.ReactNode;
}) {
  if (loading) {
    return (
      <div className="card" aria-busy="true" aria-label="Loading">
        {[60, 90, 75, 85].map((w, i) => <div key={i} className="skel" style={{ width: `${w}%` }} />)}
      </div>
    );
  }
  if (error?.status === 403) {
    return <div className="card state" role="alert"><b>You do not have access to this</b>Ask an owner if you need it.</div>;
  }
  if (error) {
    return (
      <div className="card state" role="alert">
        <b>Something went wrong</b>{error.message}
        {onRetry && <div style={{ marginTop: 12 }}><button onClick={onRetry}>Try again</button></div>}
      </div>
    );
  }
  if (empty) return <div className="card state"><b>{emptyTitle}</b>{emptyHint}</div>;
  return <>{children}</>;
}
