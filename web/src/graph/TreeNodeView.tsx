import { Handle, Position, useStore as useFlowStore, type NodeProps, type Node } from "@xyflow/react";
import { memo } from "react";
import type { Direction } from "../api";
import { classColor } from "../lib/classColor";
import { NODE_H, NODE_W } from "./layout";
import type { LensRole } from "./lens";
import type { TNode } from "./treeModel";

export type TreeNodeData = {
  n: TNode;
  selected: boolean;
  expanded: boolean;
  canToggle: boolean;
  busy: boolean;
  lens: LensRole | null; // the data lens: ring in red-orange (changes it) or teal (reads it); everything else fades
  dir: Direction;
  onToggle: (pathId: string) => void;
};
const LOD_ZOOM = 0.3;

/** The data lens ring: red-orange where the function changes the picked field, teal where it reads it. */
export function LensRing({ role }: { role: "write" | "read" }) {
  return (
    <span
      aria-hidden="true"
      data-testid="lens-ring"
      className="pointer-events-none absolute -inset-[5px] rounded-[7px]"
      style={{ border: `3px solid ${role === "write" ? "var(--writes)" : "var(--reads)"}` }}
    />
  );
}
export type FlowNode = Node<TreeNodeData, "fn">;

const badge = "inline-flex items-center rounded-node border px-1 leading-4 text-[10px] whitespace-nowrap";

function TreeNodeViewImpl({ data }: NodeProps<FlowNode>) {
  const { n, selected, expanded, canToggle, busy, lens, dir, onToggle } = data;
  const lensStyle = { opacity: lens === "dim" ? 0.3 : undefined } as const;
  const ring = lens === "write" || lens === "read" ? <LensRing role={lens} /> : null;
  // Zoomed far out nobody can read the text, and hundreds of full nodes make the page slow: draw a light version.
  const far = useFlowStore((s) => s.transform[2] < LOD_ZOOM);
  const aiUnchecked = !!n.summary && !!n.summary_model && n.summary_model !== "template" && n.summary_verified !== true;
  const showsDown = dir === "down";
  const label = showsDown ? "functions it calls" : "functions that call it";
  const toggleText = busy ? "…" : expanded ? "−" : `+${n.childCount}`;
  const toggleLabel = expanded
    ? `Hide the ${label}`
    : `Show ${n.childCount} ${label}${n.hiddenCount ? ` (${n.hiddenCount} more are not shown)` : ""}`;

  if (far) {
    return (
      <div
        data-testid="tree-node"
        data-path-id={n.pathId}
        data-fn-name={n.qualified_name}
        data-lens={lens ?? undefined}
        className={`relative flex items-center gap-2 bg-panel border border-rule rounded-node px-2 ${n.is_noise ? "opacity-60" : ""}`}
        style={{
          width: NODE_W,
          height: NODE_H,
          outline: selected ? "2px solid var(--ink)" : undefined,
          ...lensStyle,
          contain: "layout style", // hundreds of these appear at once when you fit a big tree: keep each one cheap
          borderLeft: `8px solid ${classColor(n.container)}`,
        }}
      >
        {ring}
        <Handle type="target" position={showsDown ? Position.Left : Position.Right} isConnectable={false} className="!opacity-0 !w-px !h-px !min-w-0 !min-h-0 !border-0" />
        <Handle type="source" position={showsDown ? Position.Right : Position.Left} isConnectable={false} className="!opacity-0 !w-px !h-px !min-w-0 !min-h-0 !border-0" />
        <span className="mono text-[20px] truncate flex-1">{n.name}</span>
        {canToggle && (
          <button
            type="button"
            className="nodrag nopan rounded-node border border-rule bg-paper px-2 text-[18px]"
            aria-label={toggleLabel}
            aria-expanded={expanded}
            data-testid="tree-toggle"
            onClick={(e) => {
              e.stopPropagation();
              onToggle(n.pathId);
            }}
          >
            {toggleText}
          </button>
        )}
      </div>
    );
  }

  return (
    <div
      data-testid="tree-node"
      data-path-id={n.pathId}
      data-fn-name={n.qualified_name}
      data-lens={lens ?? undefined}
      className={`group relative flex gap-2 bg-panel border border-rule rounded-node px-2 py-1.5 ${
        showsDown ? "" : "flex-row-reverse"
      } ${n.is_noise ? "opacity-60" : ""} ${n.cycle ? "border-dashed" : ""}`}
      style={{
        width: NODE_W,
        height: NODE_H,
        outline: selected ? "2px solid var(--ink)" : undefined,
        outlineOffset: 0,
        ...lensStyle,
        [showsDown ? "borderLeftWidth" : "borderRightWidth"]: n.is_entry_point ? 4 : 1,
        [showsDown ? "borderLeftColor" : "borderRightColor"]: n.is_entry_point ? "var(--ink)" : undefined,
      }}
    >
      {ring}
      <Handle type="target" position={showsDown ? Position.Left : Position.Right} isConnectable={false} className="!opacity-0 !w-px !h-px !min-w-0 !min-h-0 !border-0" />
      <Handle type="source" position={showsDown ? Position.Right : Position.Left} isConnectable={false} className="!opacity-0 !w-px !h-px !min-w-0 !min-h-0 !border-0" />

      <div className="flex-1 min-w-0 flex flex-col justify-center text-left">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className={`mono font-medium truncate ${n.is_noise ? "text-xs" : "text-[13px]"}`}>{n.name}</span>
          {n.container && (
            <span
              className="shrink-0 max-w-[84px] truncate rounded-node border px-1 text-[10px] leading-4"
              style={{
                borderColor: classColor(n.container),
                background: `color-mix(in srgb, ${classColor(n.container)} 16%, transparent)`,
              }}
            >
              {n.container}
            </span>
          )}
        </div>
        <div
          className={`truncate ${n.is_noise ? "text-[11px]" : "text-xs"} ${n.summary ? "" : "text-muted"} ${
            aiUnchecked ? "underline decoration-dotted decoration-unverified underline-offset-2" : ""
          }`}
        >
          {n.summary ?? "No explanation yet."}
        </div>
      </div>

      <div className={`w-[60px] shrink-0 flex flex-col justify-between ${showsDown ? "items-end" : "items-start"}`}>
        <div className={`flex flex-wrap gap-0.5 ${showsDown ? "justify-end" : "justify-start"}`}>
          {n.is_virtual && (
            <span className={`${badge} border-may-call text-may-call`} title="A virtual method: subclasses can replace it">
              virtual
            </span>
          )}
          {n.fields_written_count > 0 && (
            <span className={`${badge} border-writes text-writes`} title={`Changes ${n.fields_written_count} pieces of data`} aria-label={`Changes ${n.fields_written_count} pieces of data`}>
              ✎{n.fields_written_count}
            </span>
          )}
          {n.cycle && (
            <span className={`${badge} border-muted text-muted`} title="Already above in this chain, so it is not opened again" aria-label="Already above in this chain">
              ↻
            </span>
          )}
          {n.indirect_calls > 0 && (
            <span className={`${badge} border-muted text-muted`} title="Calls something through a pointer: what it calls is unknown" aria-label="Calls something through a pointer">
              ?
            </span>
          )}
        </div>
        {canToggle && (
          <button
            type="button"
            className="nodrag nopan self-stretch rounded-node border border-rule bg-paper px-1 text-[11px] leading-4 hover:border-ink"
            aria-label={toggleLabel}
            aria-expanded={expanded}
            data-testid="tree-toggle"
            onClick={(e) => {
              e.stopPropagation();
              onToggle(n.pathId);
            }}
            onDoubleClick={(e) => e.stopPropagation()}
          >
            {toggleText}
          </button>
        )}
      </div>

      <div
        role="tooltip"
        className="node-tip pointer-events-none absolute left-0 bottom-full mb-1 z-50 w-80 border border-rule bg-panel p-2 text-xs text-left"
      >
        <div className="mono break-all">{n.qualified_name}</div>
        <div className="text-muted mono">
          {n.file}:{n.line_start}
        </div>
        <div className="mt-1">{n.summary ?? "No explanation yet."}</div>
        {n.hiddenCount > 0 && <div className="mt-1 text-muted">Showing the first {n.childCount - n.hiddenCount} of {n.childCount}.</div>}
      </div>
    </div>
  );
}

export const TreeNodeView = memo(TreeNodeViewImpl);
