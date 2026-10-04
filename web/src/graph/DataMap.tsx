import {
  BaseEdge,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getSmoothStepPath,
  useReactFlow,
  useStore as useFlowStore,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { memo, useEffect, useMemo, useState } from "react";
import type { DataMapField, FnBrief } from "../api";
import { ErrorNote } from "../components/ErrorNote";
import { ProgressBar } from "../components/ProgressBar";
import { classColor } from "../lib/classColor";
import { plural } from "../lib/util";
import { useDatamap } from "../queries";
import { useStore } from "../store";
import { GraphDefs } from "./GraphDefs";
import { layoutGraph, type XY } from "./layout";
import { lensRole, useLens, type LensRole } from "./lens";
import { LensRing } from "./TreeNodeView";
import { LensCard } from "./LensCard";
import { lastPart } from "../lib/names";

const FN_W = 248;
const FN_H = 40;
const FIELD_W = 248;
const FIELD_H = 52;
const LOD_ZOOM = 0.3;

type FnData = { fn: FnBrief; selected: boolean; lens: LensRole | null; onPick: (id: string) => void };
type FieldData = { f: DataMapField; picked: boolean; shared: boolean; onPick: (id: string) => void };
type LinkData = { kind: "reads" | "writes"; count: number };
type FnNode = Node<FnData, "dfn">;
type FieldNode = Node<FieldData, "dfield">;
type LinkEdge = Edge<LinkData, "dlink">;

const hiddenHandle = "!opacity-0 !w-px !h-px !min-w-0 !min-h-0 !border-0";

const FnNodeView = memo(function FnNodeView({ data }: NodeProps<FnNode>) {
  const { fn, selected, lens, onPick } = data;
  const far = useFlowStore((s) => s.transform[2] < LOD_ZOOM);
  return (
    <div
      data-testid="map-fn"
      data-fn-name={fn.qualified_name}
      data-lens={lens ?? undefined}
      onClick={() => onPick(fn.id)}
      className={`flex items-center gap-2 bg-panel border border-rule rounded-node px-2 cursor-pointer ${fn.is_noise ? "opacity-60" : ""}`}
      style={{
        pointerEvents: "auto", // React Flow switches clicks off on nodes unless it handles them itself
        width: FN_W,
        height: FN_H,
        outline: selected ? "2px solid var(--ink)" : undefined,
        opacity: lens === "dim" ? 0.3 : undefined,
        borderLeft: `6px solid ${classColor(fn.container)}`,
      }}
      title={`${fn.qualified_name}\n${fn.summary ?? "No explanation yet."}`}
    >
      {(lens === "write" || lens === "read") && <LensRing role={lens} />}
      <Handle type="source" position={Position.Right} isConnectable={false} className={hiddenHandle} />
      <span className={`mono truncate flex-1 ${far ? "text-[20px]" : "text-[13px]"}`}>{fn.name}</span>
      {!far && fn.fields_written_count > 0 && (
        <span className="rounded-node border border-writes text-writes px-1 text-[10px] leading-4" title={`Changes ${fn.fields_written_count} pieces of data`}>
          ✎{fn.fields_written_count}
        </span>
      )}
    </div>
  );
});

const FieldNodeView = memo(function FieldNodeView({ data }: NodeProps<FieldNode>) {
  const { f, picked, shared, onPick } = data;
  const far = useFlowStore((s) => s.transform[2] < LOD_ZOOM);
  return (
    <div
      data-testid="map-field"
      data-field-name={f.qualified_name}
      onClick={() => onPick(f.id)}
      className="relative flex flex-col justify-center bg-panel border border-rule rounded-node px-2 cursor-pointer"
      style={{ pointerEvents: "auto", width: FIELD_W, height: FIELD_H, outline: picked ? "2px solid var(--ink)" : undefined }}
      title={`${f.qualified_name} (${f.type})\nChanged by ${f.writers}, read by ${f.readers} functions`}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} className={hiddenHandle} />
      <span className={`mono truncate ${far ? "text-[20px]" : "text-[13px]"}`}>{f.name}</span>
      {!far && (
        <span className="text-[11px] text-muted truncate">
          {f.type} · changed by {f.writers}, read by {f.readers}
        </span>
      )}
      {shared && (
        <span
          data-testid="shared-dot"
          role="img"
          aria-label={`Shared state: ${f.writers} different functions change this`}
          title={`Shared state: ${f.writers} different functions change this. Coupling bugs hide here.`}
          className="absolute -top-1.5 -right-1.5 size-3.5 rounded-full bg-writes border-2 border-panel"
        />
      )}
    </div>
  );
});

const LinkEdgeView = memo(function LinkEdgeView({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data }: EdgeProps<LinkEdge>) {
  if (!data) return null;
  const [path] = getSmoothStepPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition, borderRadius: 4 });
  const writes = data.kind === "writes";
  return (
    <BaseEdge
      id={id}
      path={path}
      className={writes ? "edge-writes" : "edge-reads"}
      // a write points at the data; a read points at the function that takes it in
      markerEnd={writes ? "url(#aero-arrow-writes)" : undefined}
      markerStart={writes ? undefined : "url(#aero-arrow-reads)"}
      style={{ stroke: writes ? "var(--writes)" : "var(--reads)", strokeWidth: writes ? 2 : 1.25, opacity: writes ? 1 : 0.75 }}
    />
  );
});

const nodeTypes = { dfn: FnNodeView, dfield: FieldNodeView };
const edgeTypes = { dlink: LinkEdgeView };

function Inner() {
  const rf = useReactFlow();
  const { repo, rootId, fieldId } = useStore();
  const setRoot = useStore((s) => s.setRoot);
  const setField = useStore((s) => s.setField);
  const [scope, setScope] = useState<"class" | "file" | undefined>(undefined);
  const q = useDatamap(repo, rootId, scope);
  const lens = useLens();
  const d = q.data;

  const [positions, setPositions] = useState<Record<string, XY> | null>(null);
  const layoutKey = d ? `${d.scope.kind}:${d.scope.key}:${d.functions.length}:${d.fields.length}:${d.links.length}` : "";
  useEffect(() => {
    if (!d) return;
    let live = true;
    const nodes = [
      ...d.functions.map((f) => ({ id: `f:${f.id}`, width: FN_W, height: FN_H })),
      ...d.fields.map((f) => ({ id: `d:${f.id}`, width: FIELD_W, height: FIELD_H })),
    ];
    const edges = d.links.map((l, i) => ({ id: `l${i}`, source: `f:${l.function_id}`, target: `d:${l.field_id}` }));
    void layoutGraph(nodes, edges, "down", "graph").then((p) => {
      if (!live) return;
      setPositions(p);
      // Start at a size you can read, centred on the function you picked: a big class is too tall to fit on
      // screen at any readable size.
      setTimeout(
        () =>
          requestAnimationFrame(() => {
            const xs = Object.values(p).map((q) => q.x);
            const width = Math.max(...xs) + FIELD_W - Math.min(...xs);
            const el = document.querySelector('[data-testid="data-map"]');
            const w = el?.getBoundingClientRect().width ?? 800;
            const zoom = Math.max(0.5, Math.min(1, (w - 80) / width));
            const h = el?.getBoundingClientRect().height ?? 600;
            const sel = p[`f:${useStore.getState().rootId}`];
            const y = sel ? h / 2 - (sel.y + FN_H / 2) * zoom : 90;
            void rf.setViewport({ x: 40 - Math.min(...xs) * zoom, y, zoom }, { duration: 0 });
          }),
        0,
      );
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey]);

  const onPickFn = (id: string) => setRoot(id);
  const onPickField = (id: string) => setField(id === useStore.getState().fieldId ? null : id);

  const nodes = useMemo(() => {
    if (!d || !positions) return [];
    const out: (FnNode | FieldNode)[] = [];
    for (const f of d.functions)
      out.push({ id: `f:${f.id}`, type: "dfn", position: positions[`f:${f.id}`] ?? { x: 0, y: 0 }, data: { fn: f, selected: f.id === rootId, lens: lensRole(lens, f.id), onPick: onPickFn }, width: FN_W, height: FN_H, draggable: false, selectable: false, focusable: false });
    for (const f of d.fields)
      out.push({ id: `d:${f.id}`, type: "dfield", position: positions[`d:${f.id}`] ?? { x: 0, y: 0 }, data: { f, picked: f.id === fieldId, shared: f.writers > 1, onPick: onPickField }, width: FIELD_W, height: FIELD_H, draggable: false, selectable: false, focusable: false });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d, positions, rootId, fieldId, lens]);

  const edges: LinkEdge[] = useMemo(
    () =>
      (d?.links ?? []).map((l, i) => ({
        id: `l${i}`,
        source: `f:${l.function_id}`,
        target: `d:${l.field_id}`,
        type: "dlink" as const,
        focusable: false,
        selectable: false,
        data: { kind: l.kind as "reads" | "writes", count: l.count },
      })),
    [d],
  );

  const shared = d?.fields.filter((f) => f.writers > 1).length ?? 0;
  const showShared = () => {
    // the shared fields and the functions that change them: that is the coupling worth looking at
    const sharedIds = new Set((d?.fields ?? []).filter((f) => f.writers > 1).map((f) => f.id));
    const writers = (d?.links ?? []).filter((l) => l.kind === "writes" && sharedIds.has(l.field_id)).map((l) => `f:${l.function_id}`);
    const ids = [...[...sharedIds].map((id) => `d:${id}`), ...new Set(writers)].map((id) => ({ id }));
    if (ids.length) void rf.fitView({ nodes: ids, padding: 0.1, maxZoom: 1, duration: 0 });
  };
  const shownFns = useMemo(() => new Set(d?.functions.map((f) => f.id) ?? []), [d]);

  return (
    <div className="h-full flex flex-col min-h-0">
      <ProgressBar active={q.isFetching} label="Loading the data map" />
      {q.isError && (
        <div className="p-4">
          <ErrorNote error={q.error} what="the data map" onRetry={() => q.refetch()} />
        </div>
      )}
      <div className="flex-1 min-h-0 relative" data-testid="data-map" data-fields={d?.fields.length ?? 0}>
        <GraphDefs />
        {lens && <LensCard lens={lens} mode="map" shownFnIds={shownFns} />}
        {d && (
          <div className="absolute top-3 left-3 z-10 border border-rule bg-panel p-2 text-sm max-w-[320px]">
            <div role="radiogroup" aria-label="What to group by" className="inline-flex">
              {(
                [
                  ["class", d.available.class_name ? `Class ${lastPart(d.available.class_name)}` : "Class", !d.available.class_name],
                  ["file", `File ${d.available.file.split("/").pop()}`, false],
                ] as const
              ).map(([value, label, disabled]) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={d.scope.kind === value}
                  disabled={disabled}
                  onClick={() => setScope(value)}
                  className={`px-2 py-1 border border-rule first:rounded-l-node last:rounded-r-node -ml-px first:ml-0 truncate max-w-[150px] ${d.scope.kind === value ? "bg-ink text-paper border-ink" : "bg-panel hover:border-ink"} disabled:opacity-50`}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="mt-2 text-muted">
              {plural(d.functions.length, "function")} on the left, {plural(d.fields.length, "piece of data", "pieces of data")} they touch on the right.
              {d.truncated ? " Showing the busiest ones." : ""}
            </p>
            <p className="mt-1">
              <span aria-hidden="true" className="inline-block size-3 rounded-full bg-writes align-middle mr-1" />
              {shared > 0 ? `${plural(shared, "item")} changed by more than one function: shared state.` : "Nothing here is changed by more than one function."}
            </p>
            {shared > 0 && (
              <button type="button" onClick={showShared} className="mt-1 underline underline-offset-2 hover:no-underline" data-testid="show-shared">
                Show shared state
              </button>
            )}
          </div>
        )}
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onlyRenderVisibleElements
          nodesDraggable={false}
          nodesConnectable={false}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          disableKeyboardA11y
          zoomOnDoubleClick={false}
          minZoom={0.05}
          maxZoom={1.5}
          attributionPosition="bottom-left"
        >
          <Controls position="bottom-right" showInteractive={false} />
          <MiniMap position="bottom-right" style={{ marginBottom: 108, width: 160, height: 100 }} pannable zoomable nodeStrokeWidth={0} nodeColor={(n) => (n.type === "dfield" ? "var(--muted)" : "var(--rule)")} />
        </ReactFlow>
      </div>
    </div>
  );
}

export function DataMap() {
  return (
    <ReactFlowProvider>
      <Inner />
    </ReactFlowProvider>
  );
}
