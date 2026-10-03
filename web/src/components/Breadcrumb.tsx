import { useTreeStore } from "../graph/treeStore";
import { pathToRoot, ROOT } from "../graph/treeModel";
import { shortName } from "../lib/util";
import { useFunction } from "../queries";
import { useStore } from "../store";

function Name({ id }: { id: string }) {
  const repo = useStore((s) => s.repo);
  const q = useFunction(repo, id);
  const n = q.data?.node as { name: string; container: string | null } | undefined;
  return <>{n ? shortName(n) : "…"}</>;
}

/** repo › roots you drilled through › the root › the nodes down to the one you selected */
export function Breadcrumb() {
  const { repo, rootId, trail, selPath, dir } = useStore();
  const goTrail = useStore((s) => s.goTrail);
  const setSel = useStore((s) => s.setSel);
  const model = useTreeStore((s) => s.model);

  if (!repo) return <span className="flex-1 text-sm text-muted">No repo picked yet</span>;

  const selectedPath = model && selPath && model.nodes[selPath] && model.nodes[ROOT]?.id === rootId ? pathToRoot(model, selPath).slice(1) : [];
  const btn = "mono hover:underline underline-offset-2 truncate";

  return (
    <nav aria-label="Where you are" className="min-w-0 flex-1 text-sm text-muted">
      <ol className="flex items-center gap-2 min-w-0 overflow-hidden">
        <li className="shrink-0">{repo}</li>
        {trail.map((id, i) => (
          <li key={`${id}-${i}`} className="flex items-center gap-2 min-w-0">
            <span aria-hidden="true">›</span>
            <button type="button" className={btn} onClick={() => goTrail(i)} title="Go back to this function">
              <Name id={id} />
            </button>
          </li>
        ))}
        {rootId && (
          <li className="flex items-center gap-2 min-w-0">
            <span aria-hidden="true">›</span>
            <button
              type="button"
              className={`${btn} ${selectedPath.length ? "" : "text-ink"}`}
              data-testid="breadcrumb-root"
              aria-current={selectedPath.length ? undefined : "page"}
              onClick={() => setSel(null)}
            >
              <Name id={rootId} />
            </button>
            <span className="shrink-0">({dir === "down" ? "what it calls" : "who calls it"})</span>
          </li>
        )}
        {selectedPath.map((p, i) => (
          <li key={p} className="flex items-center gap-2 min-w-0">
            <span aria-hidden="true">›</span>
            <button
              type="button"
              className={`${btn} ${i === selectedPath.length - 1 ? "text-ink" : ""}`}
              aria-current={i === selectedPath.length - 1 ? "page" : undefined}
              onClick={() => setSel(p)}
            >
              {model!.nodes[p]!.name}
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}
