import { plural } from "../lib/util";
import { useRepos } from "../queries";
import { useStore } from "../store";
import { ErrorNote } from "./ErrorNote";
import { ProgressBar } from "./ProgressBar";

export function RepoPicker() {
  const q = useRepos();
  const setRepo = useStore((s) => s.setRepo);
  return (
    <section aria-labelledby="repos-title" className="flex-1 min-h-0 overflow-auto p-4">
      <h2 id="repos-title" className="font-semibold mb-1">
        Pick a repo
      </h2>
      <ProgressBar active={q.isFetching} label="Loading the repos" />
      {q.isError && <ErrorNote error={q.error} what="the list of repos" onRetry={() => q.refetch()} />}
      {q.data && q.data.length === 0 && (
        <p className="text-sm text-muted">
          No repos are loaded yet. Run <code>make load REPO=repos/&lt;name&gt;</code>, then reload this page.
        </p>
      )}
      <ul className="mt-2 space-y-2">
        {q.data?.map((r) => (
          <li key={r.name}>
            <button
              type="button"
              onClick={() => setRepo(r.name)}
              disabled={!!r.error}
              className="w-full text-left border border-rule rounded-node bg-panel px-3 py-2 hover:border-ink disabled:opacity-60"
            >
              <span className="font-semibold block">{r.name}</span>
              <span className="text-sm text-muted block">
                {r.error
                  ? r.error
                  : `${plural(r.stats?.nodes.function ?? 0, "function")} · ${plural(r.stats?.nodes.field ?? 0, "piece of data", "pieces of data")}`}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
