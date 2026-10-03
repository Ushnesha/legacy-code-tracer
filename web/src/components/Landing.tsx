import { plural, shortName, shortSha } from "../lib/util";
import { useEntryPoints, useRepos } from "../queries";
import { useStore } from "../store";
import { ErrorNote } from "./ErrorNote";
import { ProgressBar } from "./ProgressBar";

function Count({ n, label }: { n: number; label: string }) {
  return (
    <div>
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="text-2xl font-semibold tabular-nums">{n.toLocaleString("en-US")}</dd>
    </div>
  );
}

/** The first five entry points, counting overloads of the same function once. */
function top5<T extends { qualified_name: string }>(list: T[] | undefined): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const e of list ?? []) {
    if (seen.has(e.qualified_name)) continue;
    seen.add(e.qualified_name);
    out.push(e);
    if (out.length === 5) break;
  }
  return out;
}

/** What you see before you pick a function: the repo in numbers, and five places to start. */
export function Landing({ repo }: { repo: string }) {
  const repos = useRepos();
  const eps = useEntryPoints(repo);
  const setRoot = useStore((s) => s.setRoot);
  const info = repos.data?.find((r) => r.name === repo);
  const edges = info?.stats?.edges ?? {};
  const sha = shortSha(info?.commit_sha);

  return (
    <div className="h-full overflow-auto" data-testid="landing">
      <ProgressBar active={repos.isFetching || eps.isFetching} label="Loading" />
      <div className="max-w-3xl p-8">
        <h1 className="text-2xl font-semibold">{repo}</h1>
        <p className="text-sm text-muted">
          {sha && (
            <>
              commit <span className="mono">{sha}</span>
            </>
          )}
          {info?.repo_url && (
            <>
              {sha ? " · " : ""}
              <a className="underline underline-offset-2 hover:no-underline" href={info.repo_url} target="_blank" rel="noreferrer">
                {info.repo_url.replace(/^https?:\/\//, "").replace(/\.git$/, "")}
              </a>
            </>
          )}
        </p>

        {repos.isError && <div className="mt-4"><ErrorNote error={repos.error} what="the repo details" onRetry={() => repos.refetch()} /></div>}
        {info?.stats && (
          <dl className="mt-6 grid grid-cols-2 sm:grid-cols-4 gap-6" data-testid="landing-counts">
            <Count n={info.stats.nodes.function ?? 0} label="functions" />
            <Count n={edges.calls ?? 0} label="call links" />
            <Count n={(edges.reads ?? 0) + (edges.writes ?? 0)} label="data links" />
            <Count n={edges.may_call ?? 0} label="virtual links" />
          </dl>
        )}

        <h2 className="mt-10 font-semibold">Top entry points</h2>
        <p className="text-sm text-muted">The functions that reach the most code and that nothing else calls.</p>
        {eps.isError && <div className="mt-3"><ErrorNote error={eps.error} what="the starting points" onRetry={() => eps.refetch()} /></div>}
        <ul className="mt-3 grid gap-3 sm:grid-cols-2" data-testid="landing-cards">
          {top5(eps.data).map((e) => (
            <li key={e.id} className="border border-rule bg-panel p-3 flex flex-col">
              <span className="mono text-sm font-medium break-all">{shortName(e)}</span>
              <span className="text-xs text-muted mono">{e.file}</span>
              <span className={`mt-2 text-sm line-clamp-2 flex-1 ${e.summary ? "" : "text-muted"}`}>{e.summary ?? "No explanation yet."}</span>
              <span className="mt-2 flex items-center justify-between gap-2">
                <span className="text-xs text-muted">Reaches {plural(e.reach, "function")}</span>
                <button
                  type="button"
                  onClick={() => setRoot(e.id)}
                  className="border border-rule rounded-node bg-paper px-3 py-1 text-sm hover:border-ink"
                  aria-label={`Trace ${shortName(e)}`}
                >
                  Trace it
                </button>
              </span>
            </li>
          ))}
        </ul>
        {eps.data && eps.data.length === 0 && <p className="mt-3 text-sm text-muted">No starting points were found in this repo.</p>}
      </div>
    </div>
  );
}
