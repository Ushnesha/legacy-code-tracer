import { plainError } from "../api";

/** One plain sentence saying what failed and what to try, with a retry button. */
export function ErrorNote({ error, what, onRetry }: { error: unknown; what: string; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div role="alert" className="border border-writes rounded-node p-3 text-sm bg-panel">
      <p>{plainError(error, what)}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="mt-2 underline underline-offset-2 hover:no-underline">
          Try again
        </button>
      )}
    </div>
  );
}
