import type { AffectedFn, ImpactResponse } from "../api";
import { hours, SEVERITY_STYLE, SEVERITY_WORD, type Severity } from "../lib/severity";
import { plural } from "../lib/util";
import { useImpact } from "../queries";
import { useStore } from "../store";
import { ErrorNote } from "./ErrorNote";
import { FnLink } from "./FnLink";
import { ProgressBar } from "./ProgressBar";

const RANK: Record<Severity, number> = { high: 0, medium: 1, low: 2 };

function Badge({ level, children, testId }: { level: Severity; children: React.ReactNode; testId?: string }) {
  return (
    <span data-testid={testId} className={`inline-block rounded-node border px-2 py-0.5 text-sm whitespace-nowrap ${SEVERITY_STYLE[level]}`}>
      {children}
    </span>
  );
}

/** The top of the tab: how serious a change is, what it would mean for the program, and roughly how long it takes. */
function Overview({ d }: { d: ImpactResponse }) {
  const direct = d.affected.filter((a) => a.distance === 1).length;
  const unchecked = d.business_impact_checked !== true;
  return (
    <section className="border border-rule rounded-node bg-panel p-3" data-testid="impact-overview">
      <p className="flex items-center gap-2 flex-wrap">
        <span className="font-semibold">Severity</span>
        <Badge level={d.severity} testId="impact-severity">
          {SEVERITY_WORD[d.severity]}
        </Badge>
      </p>
      <p className="text-sm mt-1">{d.severity_reason}</p>

      <h3 className="font-semibold text-sm text-muted mt-3 mb-1">Business impact</h3>
      {d.business_impact ? (
        <p className={unchecked ? "text-unverified" : ""} data-testid="business-impact">
          {d.business_impact}
        </p>
      ) : (
        <p className="text-sm text-muted">
          Not written yet. Run <code>make explain</code> for this repo to have the AI describe what users of the program would notice.
        </p>
      )}

      <h3 className="font-semibold text-sm text-muted mt-3 mb-1">Rough effort</h3>
      <p data-testid="impact-effort">
        About {d.effort.low}–{hours(d.effort.high)} to make the change and retest what it affects.
      </p>
      <details className="text-sm text-muted mt-1">
        <summary className="cursor-pointer">How this is worked out</summary>
        <p className="mt-1">
          Making the change: {hours(d.effort.change)}. Updating and retesting the {plural(direct, "function")} that use it directly
          {d.affected.length > direct ? ", plus one full test run for everything further away" : ""}: {hours(d.effort.checks)}. Each
          direct user counts about an hour, more for big functions. The range allows for surprises. It is worked out from the code
          graph, so treat it as a planning guide, not a quote.
        </p>
      </details>
    </section>
  );
}

function AffectedRow({ a }: { a: AffectedFn }) {
  return (
    <li className="py-2 border-t border-rule first:border-t-0" data-testid="affected-fn">
      <div className="flex items-start justify-between gap-2">
        <FnLink f={a} />
        <Badge level={a.severity}>{SEVERITY_WORD[a.severity]} risk</Badge>
      </div>
      <ul className="text-sm mt-1 space-y-0.5">
        {a.how.map((h) => (
          <li key={h}>{h}.</li>
        ))}
      </ul>
      {a.summary && (
        <p className="text-sm text-muted mt-1">
          <span className="font-medium">What it does:</span> {a.summary}
        </p>
      )}
      <p className="text-xs text-muted mt-1">
        {a.severity_reason} {a.effort_hours > 0 ? `About ${hours(a.effort_hours)} to update and retest.` : "Covered by the full test run."}
      </p>
    </li>
  );
}

/** Affected functions, one group per file, the riskiest files first. */
function ByFile({ affected }: { affected: AffectedFn[] }) {
  const files = new Map<string, AffectedFn[]>();
  for (const a of affected) files.set(a.file, [...(files.get(a.file) ?? []), a]);
  const groups = [...files.entries()]
    .map(([file, fns]) => ({
      file,
      fns,
      worst: fns.reduce<Severity>((w, a) => (RANK[a.severity] < RANK[w] ? a.severity : w), "low"),
      hours: fns.reduce((s, a) => s + a.effort_hours, 0),
    }))
    .sort((x, y) => RANK[x.worst] - RANK[y.worst] || y.fns.length - x.fns.length || x.file.localeCompare(y.file));
  return (
    <div className="mt-3 space-y-2">
      {groups.map((g, i) => (
        <details key={g.file} open={i === 0} className="border border-rule rounded-node bg-panel" data-testid="impact-file">
          <summary className="cursor-pointer px-3 py-2 flex items-center gap-2 flex-wrap">
            <span className="mono text-sm break-all">{g.file}</span>
            <span className="text-muted text-sm">
              · {plural(g.fns.length, "function")}
              {g.hours > 0 ? ` · about ${hours(g.hours)}` : ""}
            </span>
            <Badge level={g.worst}>{SEVERITY_WORD[g.worst]}</Badge>
          </summary>
          <ul className="px-3 pb-1">
            {g.fns.map((a) => (
              <AffectedRow key={a.id} a={a} />
            ))}
          </ul>
        </details>
      ))}
    </div>
  );
}

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
        <h2 className="font-semibold">If you change this</h2>
        <p className="text-sm text-muted mb-3">
          What could be affected, how badly, and roughly how long it would take to handle. Everything except the business
          impact is worked out from the code itself.
        </p>
        {q.isError && <ErrorNote error={q.error} what="the impact" onRetry={() => q.refetch()} />}
        {d && <Overview d={d} />}
        {d && d.affected.length === 0 && <p className="mt-3">Nothing else calls this or reads the data it changes, so changing it won't affect any other function.</p>}
        {d && d.affected.length > 0 && (
          <>
            <p className="mt-4">
              Up to {plural(d.affected.length, "function")} could be affected, in{" "}
              {plural(new Set(d.affected.map((a) => a.file)).size, "file")}.
            </p>
            <button
              type="button"
              onClick={showAsTree}
              className="mt-2 border border-rule rounded-node bg-panel px-3 py-1 hover:border-ink"
              data-testid="show-as-tree"
            >
              Show as tree
            </button>
            <ByFile affected={d.affected} />
          </>
        )}
      </div>
    </div>
  );
}
