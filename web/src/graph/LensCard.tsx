import { useState } from "react";
import type { FnWithEdge } from "../api";
import { ErrorNote } from "../components/ErrorNote";
import { plural, shortName } from "../lib/util";
import { useStore } from "../store";
import type { Lens } from "./lens";
import { useTreeStore } from "./treeStore";

/**
 * The floating card for the data lens. In the call tree it can also bring the writers that are missing
 * from the tree into it ("Add them").
 */
export function LensCard({ lens, mode, shownFnIds }: { lens: Lens; mode: "tree" | "map"; shownFnIds?: Set<string> }) {
  const setField = useStore((s) => s.setField);
  const drillTo = useStore((s) => s.drillTo);
  const model = useTreeStore((s) => s.model);
  const addFunction = useTreeStore((s) => s.addFunction);
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState<{ unreachable: FnWithEdge[]; hidden: FnWithEdge[] } | null>(null);

  const d = lens.data;
  const name = d ? String(d.field.name) : "…";
  const writers = d?.writers ?? [];
  const readers = d?.readers ?? [];

  const inView =
    mode === "tree" ? new Set(Object.values(model?.nodes ?? {}).map((n) => n.id)) : (shownFnIds ?? new Set<string>());
  const missing = writers.filter((w) => !inView.has(w.id));
  const where = mode === "tree" ? "this tree" : "this map";

  const addThem = async () => {
    setBusy(true);
    const unreachable: FnWithEdge[] = [];
    const hidden: FnWithEdge[] = [];
    for (const w of missing) {
      const r = await addFunction(w.id); // one at a time: each one changes the tree the next one starts from
      if (r === "unreachable") unreachable.push(w);
      else if (r === "hidden") hidden.push(w);
    }
    setLeft(unreachable.length || hidden.length ? { unreachable, hidden } : null);
    setBusy(false);
  };

  const list = (items: FnWithEdge[]) => (
    <ul className="mt-1 space-y-0.5">
      {items.map((w) => (
        <li key={w.id}>
          <button type="button" className="mono text-sm underline underline-offset-2 hover:no-underline text-left" onClick={() => drillTo(w.id)} title="Look at this function on its own">
            {shortName(w)}
          </button>
        </li>
      ))}
    </ul>
  );

  return (
    <div
      role="status"
      data-testid="lens-card"
      className={`absolute top-3 z-20 border border-rule bg-panel p-3 text-sm ${
        mode === "map"
          ? "right-3 w-[min(420px,calc(100%-360px))]" // beside the map's own key, not on top of it
          : "left-1/2 -translate-x-1/2 w-[min(560px,calc(100%-2rem))]"
      }`}
    >
      {lens.query.isError ? (
        <ErrorNote error={lens.query.error} what="this piece of data" onRetry={() => lens.query.refetch()} />
      ) : (
        <div className="flex items-start gap-3">
          <p className="flex-1">
            <span className="mono font-medium">{name}</span> — <span className="text-writes font-medium">written by {writers.length}</span>,{" "}
            <span className="text-reads font-medium">read by {readers.length}</span>.
            {missing.length > 0 && (
              <>
                {" "}
                {missing.length} {missing.length === 1 ? "writer is" : "writers are"} not in {where}.
              </>
            )}
          </p>
          {mode === "tree" && missing.length > 0 && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void addThem()}
              className="shrink-0 border border-rule rounded-node bg-paper px-3 py-1 hover:border-ink disabled:opacity-60"
            >
              {busy ? "Adding…" : "Add them"}
            </button>
          )}
          <button
            type="button"
            onClick={() => setField(null)}
            aria-label="Clear the data lens"
            title="Clear the data lens (Esc)"
            className="shrink-0 border border-rule rounded-node bg-paper px-2 py-1 hover:border-ink"
          >
            Close
          </button>
        </div>
      )}
      {left && left.unreachable.length > 0 && (
        <div className="mt-2 border-t border-rule pt-2">
          <p>
            {plural(left.unreachable.length, "writer")} can't be reached from here by following calls
            {left.unreachable.length === 1 ? "" : ""}. Click one to look at it on its own:
          </p>
          {list(left.unreachable)}
        </div>
      )}
      {left && left.hidden.length > 0 && (
        <div className="mt-2 border-t border-rule pt-2">
          <p>
            {plural(left.hidden.length, "writer")} could not be shown: {left.hidden.length === 1 ? "it sits" : "they sit"} behind helpers that are hidden, or past the 40-per-node limit. Click one to look at it on its own:
          </p>
          {list(left.hidden)}
        </div>
      )}
    </div>
  );
}
