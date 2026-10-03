import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type Edge, type EdgeProps } from "@xyflow/react";
import { memo, useState } from "react";

export type TreeEdgeData = {
  kind: "calls" | "may_call";
  count: number;
  /** "who calls it": the call goes from the child to the parent, so the arrow is at the parent end */
  reversed: boolean;
  onPath: boolean;
};
export type FlowEdge = Edge<TreeEdgeData, "tree">;

function TreeEdgeViewImpl({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data }: EdgeProps<FlowEdge>) {
  const [hover, setHover] = useState(false);
  if (!data) return null;
  const [path, labelX, labelY] = getSmoothStepPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition, borderRadius: 4 });
  const may = data.kind === "may_call";
  const marker = `url(#${may ? "aero-arrow-may" : "aero-arrow-ink"})`;

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        className={may ? "edge-may" : "edge-calls"}
        markerEnd={data.reversed ? undefined : marker}
        markerStart={data.reversed ? marker : undefined}
        interactionWidth={14}
        style={{
          stroke: may ? "var(--may-call)" : "var(--ink)",
          strokeWidth: data.onPath ? 2.5 : 1.25,
          strokeDasharray: may ? "6 4" : undefined,
          opacity: data.onPath ? 1 : 0.6,
        }}
      />
      {/* a wider invisible line so the hover label is easy to trigger */}
      <path d={path} fill="none" stroke="transparent" strokeWidth={12} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)} />
      {(data.count > 1 || (may && hover)) && (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none absolute text-[10px] leading-3 bg-panel border border-rule rounded-node px-1 mono"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
            data-testid={may && hover ? "edge-virtual-label" : "edge-count"}
          >
            {may && hover && <span className="text-may-call">virtual </span>}
            {data.count > 1 && <span>×{data.count}</span>}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const TreeEdgeView = memo(TreeEdgeViewImpl);
