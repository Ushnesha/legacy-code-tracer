import { plural } from "../lib/util";
import { useImpact } from "../queries";
import { useStore } from "../store";
import { ErrorNote } from "./ErrorNote";
import { FnLink } from "./FnLink";
import { ProgressBar } from "./ProgressBar";

const stepsLabel = (n: number) => (n === 1 ? "Directly" : `${n} steps away`);

export function ImpactTab({ repo, id }: { repo: string; id: string }) {
  const q = useImpact(repo, id, 5);
  const drillTo = useStore((s) => s.drillTo);
  const setDir = useStore((s) => s.setDir);
  const setMode = useStore((s) => s.setMode);
  const d = q.data;

  const showAsTree = () => {
    setMode("calls");
    drillTo(id); // the function you are looking at becomes the root (a no-op if it already is)
    setDir("up"); // and the tree shows who calls it
  };

  return (
    <div>
      <ProgressBar active={q.isFetching} label="Working out what could break" />
      <div className="p-4">
        <h2 className="font-semibold">If you change this, these could be affected</h2>
        <p className="text-sm text-muted">Every function that reaches this one through calls, grouped by how many steps away it is.</p>
        {q.isError && <ErrorNote error={q.error} what="the impact" onRetry={() => q.refetch()} />}
        {d && d.total === 0 && <p className="mt-3">Nothing else calls this, so changing it won't affect any other function.</p>}
        {d && d.total > 0 && (
          <>
            <p className="mt-3">Up to {plural(d.total, "function")} could be affected.</p>
            <button
              type="button"
              onClick={showAsTree}
              className="mt-2 border border-rule rounded-node bg-panel px-3 py-1 hover:border-ink"
              data-testid="show-as-tree"
            >
              Show as tree
            </button>
            <div className="mt-3 space-y-2">
              {d.levels.map((lv, i) => (
                <details key={lv.distance} open={i === 0} className="border border-rule rounded-node bg-panel">
                  <summary className="cursor-pointer px-3 py-2 font-medium">
                    {stepsLabel(lv.distance)} <span className="text-muted font-normal">· {lv.functions.length}</span>
                  </summary>
                  <ul className="px-3 pb-2 space-y-1">
                    {lv.functions.map((f) => (
                      <li key={f.id}>
                        <FnLink f={f} />
                      </li>
                    ))}
                  </ul>
                </details>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
