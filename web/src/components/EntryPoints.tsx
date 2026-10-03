import { classColor } from "../lib/classColor";
import { plural, shortName } from "../lib/util";
import { useEntryPoints } from "../queries";
import { useStore } from "../store";
import { ErrorNote } from "./ErrorNote";
import { ProgressBar } from "./ProgressBar";

export function EntryPoints({ repo }: { repo: string }) {
  const q = useEntryPoints(repo);
  const rootId = useStore((s) => s.rootId);
  const setRoot = useStore((s) => s.setRoot);

  return (
    <section aria-labelledby="entry-title" className="flex-1 min-h-0 flex flex-col border-t border-rule">
      <div className="px-4 pt-3">
        <h2 id="entry-title" className="font-semibold">
          Where to start
        </h2>
        <p className="text-sm text-muted">Functions that nothing else in the project calls, biggest first.</p>
      </div>
      <ProgressBar active={q.isFetching} label="Loading the starting points" />
      <div className="flex-1 min-h-0 overflow-auto px-2 pb-2">
        {q.isError && <ErrorNote error={q.error} what="the starting points" onRetry={() => q.refetch()} />}
        {q.data && q.data.length === 0 && <p className="px-2 text-sm text-muted">No starting points were found in this repo.</p>}
        <ul>
          {q.data?.map((e) => (
            <li key={e.id}>
              <button
                type="button"
                onClick={() => setRoot(e.id)}
                aria-current={rootId === e.id ? "true" : undefined}
                className={`w-full text-left px-2 py-2 border-l-4 rounded-node hover:bg-paper ${rootId === e.id ? "bg-paper" : ""}`}
                style={{ borderLeftColor: classColor(e.container) }}
              >
                <span className="mono text-sm break-all block">{shortName(e)}</span>
                <span className="text-sm block line-clamp-2">
                  {e.summary ?? <span className="text-muted">No explanation yet.</span>}
                </span>
                <span className="text-xs text-muted">
                  Reaches {plural(e.reach, "function")} · {e.file}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
