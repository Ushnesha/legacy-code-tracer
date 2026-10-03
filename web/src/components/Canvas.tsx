import { DataMap } from "../graph/DataMap";
import { Landing } from "./Landing";
import { TourCard } from "./TourCard";
import { TreeCanvas } from "../graph/TreeCanvas";
import { shortName } from "../lib/util";
import { useFunction } from "../queries";
import { useStore } from "../store";
import { ErrorNote } from "./ErrorNote";

type Node = { name: string; container: string | null };

export function Canvas() {
  const { repo, rootId, mode } = useStore();
  const root = useFunction(repo, rootId);

  if (!repo) return <Empty>Pick a repo on the left to get started.</Empty>;
  if (!rootId)
    return (
      <div className="h-full relative">
        <Landing repo={repo} />
        <TourCard />
      </div>
    );
  if (root.isError)
    return (
      <div className="p-8 max-w-xl mx-auto">
        <ErrorNote error={root.error} what="this function" onRetry={() => root.refetch()} />
      </div>
    );
  return (
    <div className="h-full min-h-0 relative" data-testid="canvas">
      <h1 className="sr-only" data-testid="canvas-root-name">
        {root.data ? shortName(root.data.node as Node) : "Loading the function"}
      </h1>
      {mode === "data" ? <DataMap /> : <TreeCanvas />}
      <TourCard />
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-full grid place-items-center p-8" data-testid="canvas">
      <p className="text-muted">{children}</p>
    </div>
  );
}
