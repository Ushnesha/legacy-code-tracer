import type { FunctionDetail } from "../api";
import { useFunction } from "../queries";
import { useStore } from "../store";
import { ErrorNote } from "./ErrorNote";
import { ProgressBar } from "./ProgressBar";

type Flag = FunctionDetail["flags"][number];

/** Places the code graph is incomplete for this function. Worked out from the graph, never by AI. */
export function ReviewTab({ repo, id }: { repo: string; id: string }) {
  const q = useFunction(repo, id);
  const setTab = useStore((s) => s.setTab);
  const flags: Flag[] = q.data?.flags ?? [];
  const look = flags.filter((f) => f.severity === "review");
  const know = flags.filter((f) => f.severity === "note");
  return (
    <div>
      <ProgressBar active={q.isFetching} label="Loading the review notes" />
      <div className="p-4">
        <h2 className="text-lg font-semibold">Needs a human look</h2>
        <p className="text-muted">
          Places where we could not see the whole picture for this function. They come from the code itself, not from AI.
        </p>
        {q.isError && <ErrorNote error={q.error} what="this function" onRetry={() => q.refetch()} />}
        {q.data && flags.length === 0 && (
          <p className="mt-4" data-testid="review-none">
            Nothing to flag. Every call here was followed. That doesn't prove the explanation is right, so still check the Code tab.
          </p>
        )}
        <FlagList title="Worth a human look" flags={look} tone="text-unverified" testId="review-flags" />
        <FlagList title="Good to know" flags={know} tone="" testId="review-notes" />
        {flags.length > 0 && (
          <button type="button" className="text-sm underline underline-offset-2 hover:no-underline mt-4" onClick={() => setTab("code")}>
            Open the code
          </button>
        )}
      </div>
    </div>
  );
}

function FlagList({ title, flags, tone, testId }: { title: string; flags: Flag[]; tone: string; testId: string }) {
  if (!flags.length) return null;
  return (
    <section className="mt-4 border border-rule rounded-node bg-panel p-3" data-testid={testId}>
      <h3 className={`font-semibold ${tone}`}>{title}</h3>
      <ul className="list-disc pl-5 mt-1">
        {flags.map((f) => (
          <li key={f.code} data-testid={`flag-${f.code}`}>
            {f.message}
            {f.lines.length > 0 && (
              <span className="text-muted"> (line{f.lines.length > 1 ? "s" : ""} {f.lines.join(", ")})</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
