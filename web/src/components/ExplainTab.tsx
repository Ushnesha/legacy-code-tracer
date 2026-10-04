import { useMemo } from "react";
import type { FnWithEdge, SummaryInfo } from "../api";
import { buildNameIndex, linkifyStep, plainReason } from "../lib/inspect";
import { useTreeStore } from "../graph/treeStore";
import { classColor } from "../lib/classColor";
import { SEVERITY_STYLE, SEVERITY_WORD } from "../lib/severity";
import { plural, shortName } from "../lib/util";
import { useFunction, useStory } from "../queries";
import { useStore } from "../store";
import { ErrorNote } from "./ErrorNote";
import { FnLink } from "./FnLink";
import { ProgressBar } from "./ProgressBar";
import { lastPart } from "../lib/names";

type Node = { name: string; container: string | null; file: string; line_start: number; line_end: number; is_entry_point?: boolean };

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-4">
      <h3 className="font-semibold text-sm text-muted mb-1">{title}</h3>
      {children}
    </section>
  );
}

function FnList({ items, empty }: { items: FnWithEdge[]; empty: string }) {
  if (!items.length) return <p className="text-sm text-muted">{empty}</p>;
  const shown = items.slice(0, 8);
  return (
    <ul className="space-y-1">
      {shown.map((f) => (
        <li key={f.id}>
          <FnLink f={f} note={f.kinds.includes("may_call") && !f.kinds.includes("calls") ? "could be a subclass" : undefined} />
        </li>
      ))}
      {items.length > shown.length && <li className="text-sm text-muted">and {items.length - shown.length} more</li>}
    </ul>
  );
}

/** Green-light or violet warning: did the checker agree with the code? */
function TrustBadge({ s }: { s: SummaryInfo }) {
  const byAi = !!s.model && s.model !== "template";
  if (!byAi) return <p className="text-sm text-muted mt-1">Written from the code's structure, not by AI.</p>;
  if (s.verified === true)
    return (
      <p className="mt-2">
        <span data-testid="badge-verified" className="inline-block rounded-node border border-ink px-2 py-0.5 text-sm">
          Checked against the code
        </span>
      </p>
    );
  const reasons = s.reasons.map(plainReason);
  return (
    <div className="mt-2" data-testid="badge-unverified">
      <p className="inline-block rounded-node border border-unverified px-2 py-0.5 text-sm text-unverified">
        {s.verified === false && reasons.length ? `Not fully checked — ${reasons[0]}` : s.verified === false ? "Not fully checked" : "Not checked against the code yet"}
      </p>
      {reasons.length > 1 && (
        <ul className="text-sm text-unverified list-disc pl-5 mt-1">
          {reasons.slice(1).map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Explanation({ s }: { s: SummaryInfo }) {
  const unchecked = !!s.model && s.model !== "template" && s.verified !== true;
  const tone = unchecked ? "text-unverified" : ""; // violet always means: AI wrote it and nobody has checked it
  return (
    <>
      {s.short && <p className={`text-lg font-semibold leading-snug ${tone}`}>{s.short}</p>}
      <TrustBadge s={s} />
      {s.detailed && <p className={`mt-3 ${tone}`}>{s.detailed}</p>}
      <Block title="Takes in">
        <p className={s.inputs ? tone : "text-sm text-muted"}>{s.inputs || "Nothing stated."}</p>
      </Block>
      <Block title="Gives back">
        <p className={s.outputs ? tone : "text-sm text-muted"}>{s.outputs || "Nothing stated."}</p>
      </Block>
      <Block title="Changes">
        {s.side_effects.length ? (
          <ul className={`list-disc pl-5 ${tone}`}>
            {s.side_effects.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">Nothing that we found.</p>
        )}
      </Block>
    </>
  );
}


const andMore = (shown: string[], total: number) =>
  shown.join(", ") + (total > shown.length ? `, and ${total - shown.length} more` : "");

/** Its job in the program, and what a change to it could reach. The level and facts come from the graph;
 *  only the role and the warnings are AI text (violet when unchecked, like the rest). */
function ChangeImpact({ s }: { s: SummaryInfo }) {
  const setTab = useStore((st) => st.setTab);
  const imp = s.impact;
  const unchecked = !!s.model && s.model !== "template" && s.verified !== true;
  const tone = unchecked ? "text-unverified" : "";
  const risks = s.change_risks ?? [];
  if (!imp && !s.role && !risks.length) return null;
  return (
    <>
      {s.role && (
        <Block title="Its role">
          <p className={tone}>{s.role}</p>
        </Block>
      )}
      <section className="mt-4" data-testid="change-impact">
        <h3 className="font-semibold text-sm text-muted mb-1">If you change it</h3>
        {imp && (
          <>
            <p>
              <span data-testid="impact-level" className={`inline-block rounded-node border px-2 py-0.5 text-sm ${SEVERITY_STYLE[imp.level]}`}>
                {SEVERITY_WORD[imp.level]} impact
              </span>
            </p>
            <p className="text-sm mt-1">{imp.reason}</p>
          </>
        )}
        {risks.length > 0 && (
          <ul className={`list-disc pl-5 mt-2 ${tone}`}>
            {risks.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        {imp && (
          <ul className="text-sm text-muted mt-2 space-y-1">
            {imp.is_entry_point && <li>The program starts here.</li>}
            {imp.entry_points.length > 0 && <li>Reached from: {andMore(imp.entry_points.map((q) => lastPart(q)), imp.entry_points_total)}</li>}
            {imp.files_total > 0 && <li>Other files that could be affected: {andMore(imp.files, imp.files_total)}</li>}
            {imp.data_users.map((d) => (
              <li key={d.field}>
                It changes <span className="mono">{lastPart(d.field)}</span>, which {andMore(d.readers.map((q) => lastPart(q)), d.readers_total)}{" "}
                {d.readers_total === 1 ? "also reads" : "also read"}.
              </li>
            ))}
          </ul>
        )}
        <button type="button" className="text-sm underline underline-offset-2 hover:no-underline mt-2" onClick={() => setTab("impact")}>
          See everything that could be affected
        </button>
      </section>
    </>
  );
}

/** "What happens when this runs": the story's steps, with names that are in the tree turned into links. */
function Story({ repo, id }: { repo: string; id: string }) {
  const q = useStory(repo, id);
  const model = useTreeStore((s) => s.model);
  const select = useTreeStore((s) => s.selectPath);
  const names = useMemo(() => buildNameIndex(model), [model]);
  if (!q.data) return null;
  return (
    <section className="mt-6 border-t border-rule pt-4" aria-labelledby="story-title">
      <h3 id="story-title" className="font-semibold">
        What happens when this runs
      </h3>
      <ol className="mt-2 list-decimal pl-5 space-y-2" data-testid="story">
        {q.data.steps.map((step, i) => (
          <li key={i}>
            {linkifyStep(step, names).map((seg, j) =>
              seg.pathId ? (
                <button
                  key={j}
                  type="button"
                  className="mono text-sm underline underline-offset-2 hover:no-underline"
                  title="Select this in the tree"
                  onClick={() => void select(seg.pathId!)}
                >
                  {seg.text}
                </button>
              ) : (
                <span key={j}>{seg.text}</span>
              ),
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ExplainTab({ repo, id }: { repo: string; id: string }) {
  const q = useFunction(repo, id);
  const d = q.data;
  const node = d?.node as Node | undefined;
  const byAi = !!d?.summary?.model && d.summary.model !== "template";
  return (
    <div>
      <ProgressBar active={q.isFetching} label="Loading the explanation" />
      <div className="p-4">
        {q.isError && <ErrorNote error={q.error} what="this function" onRetry={() => q.refetch()} />}
        {d && node && (
          <>
            <h2 className="mono text-base font-medium break-all flex items-baseline gap-2">
              <span aria-hidden="true" className="inline-block size-3 rounded-node shrink-0" style={{ background: classColor(node.container) }} />
              {shortName(node)}
            </h2>
            <p className="text-sm text-muted mono">
              {node.file}:{node.line_start}
            </p>

            <div className="mt-3">
              {d.summary ? (
                <>
                  <Explanation s={d.summary} />
                  <ChangeImpact s={d.summary} />
                </>
              ) : (
                <p className="text-muted">
                  There's no explanation for this function yet. Run <code>make explain REPO=repos/{repo}</code> to write one.
                </p>
              )}
            </div>

            {node.is_entry_point && <Story repo={repo} id={id} />}

            <Block title="Who calls this?">
              <FnList items={d.callers} empty="Nothing in this project calls it." />
            </Block>
            <Block title="What it calls">
              <FnList items={[...d.callees.calls, ...d.callees.may_call]} empty="It doesn't call anything else in this project." />
              {d.indirect_calls.count > 0 && (
                <p className="text-sm mt-1">
                  Calls something through a pointer {plural(d.indirect_calls.count, "time")}. What it calls is unknown.
                </p>
              )}
            </Block>

            {d.summary && (
              <p className="mt-6 pt-3 border-t border-rule text-sm text-muted" data-testid="explain-footer">
                {byAi ? (
                  <>
                    Explained by {d.summary.model}. AI text can be wrong — check the Code tab.
                  </>
                ) : (
                  <>Written without AI. Check the Code tab for the details.</>
                )}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
