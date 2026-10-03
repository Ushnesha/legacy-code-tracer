import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { classColor } from "../lib/classColor";
import { ErrorNote } from "../components/ErrorNote";
import { ProgressBar } from "../components/ProgressBar";
import { useStore } from "../store";
import { GraphDefs } from "./GraphDefs";
import { lensRole, useLens } from "./lens";
import { LensCard } from "./LensCard";
import { layoutTree, NODE_H, NODE_W, type XY } from "./layout";
import { TreeEdgeView, type FlowEdge } from "./TreeEdgeView";
import { TreeNodeView, type FlowNode } from "./TreeNodeView";
import { canToggle, neighbor, pathToRoot, ROOT, visibleLinks, visibleNodes, type Move } from "./treeModel";
import { useTreeStore } from "./treeStore";

// Stable objects: React Flow re-renders everything if these change identity.
const nodeTypes = { fn: TreeNodeView };
const edgeTypes = { tree: TreeEdgeView };

const ANIMATION_MS = 250;
const MAX_ANIMATED_ON_SCREEN = 90; // more nodes than this in view: snap instead of sliding, to stay smooth
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const ease = (t: number) => 1 - Math.pow(1 - t, 3);


function Inner() {
  const rf = useReactFlow();
  const { repo, rootId, dir, selPath, filters } = useStore();
  const setSel = useStore((s) => s.setSel);
  const drillTo = useStore((s) => s.drillTo);
  const model = useTreeStore((s) => s.model);
  const loadKey = useTreeStore((s) => s.loadKey);
  const loading = useTreeStore((s) => s.loading);
  const error = useTreeStore((s) => s.error);
  const busy = useTreeStore((s) => s.busy);
  const { load, open, close, ensurePath } = useTreeStore.getState();
  const wrapRef = useRef<HTMLDivElement>(null);
  const lens = useLens();
  const setField = useStore((s) => s.setField);
  const fitTick = useStore((s) => s.fitTick);
  const fitPending = useRef(false);

  // ---- load the tree when the root, direction or filters change
  useEffect(() => {
    if (repo && rootId) void load({ repo, rootId, direction: dir, includeNoise: filters.noise, includeMayCall: filters.virtual });
  }, [repo, rootId, dir, filters.noise, filters.virtual, load]);

  // ---- a shared ?sel= link: open the nodes on the way to it
  useEffect(() => {
    if (model && selPath && !model.nodes[selPath]) void ensurePath(selPath);
  }, [model, selPath, ensurePath]);

  const visible = useMemo(() => (model ? visibleNodes(model) : []), [model]);
  const links = useMemo(() => (model ? visibleLinks(model) : []), [model]);
  const selected = selPath && model?.nodes[selPath] ? selPath : ROOT;
  const onPath = useMemo(() => new Set(model ? pathToRoot(model, selected) : []), [model, selected]);

  /** How many of these positions fall inside what you can currently see. Sliding is only smooth when this is small. */
  const countOnScreen = useCallback(
    (pos: Record<string, XY>) => {
      const box = wrapRef.current?.getBoundingClientRect();
      if (!box) return 0;
      const { x, y, zoom } = rf.getViewport();
      let n = 0;
      for (const p of Object.values(pos)) {
        const sx = p.x * zoom + x;
        const sy = p.y * zoom + y;
        if (sx + NODE_W * zoom > 0 && sx < box.width && sy + NODE_H * zoom > 0 && sy < box.height) n++;
      }
      return n;
    },
    [rf],
  );

  /** First view of a new root: fit the root and its children across the width (never past 100%) and centre
   *  on the root, so a big tree starts at a readable size instead of shrinking to fit all of it. */
  const fitToRoot = useCallback(
    (pos: Record<string, XY>) => {
      const box = wrapRef.current?.getBoundingClientRect();
      const root = pos[ROOT];
      if (!box || !root) return;
      // look at the root and its direct children; deeper levels run off to the side and can be panned to
      const near = [ROOT, ...(useTreeStore.getState().model?.children[ROOT] ?? [])].map((id) => pos[id]).filter(Boolean) as XY[];
      const xs = near.map((p) => p.x);
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs) + NODE_W;
      const zoom = Math.max(0.4, Math.min(1, (box.width - 80) / (maxX - minX)));
      void rf.setViewport(
        { x: (box.width - (maxX - minX) * zoom) / 2 - minX * zoom, y: box.height / 2 - (root.y + NODE_H / 2) * zoom, zoom },
        { duration: 0 },
      );
    },
    [rf],
  );

  // ---- layout (in a worker) and the slide from old to new positions
  const [positions, setPositions] = useState<Record<string, XY>>({});
  const posRef = useRef(positions);
  const fitNextRef = useRef(true);
  const lastKeyRef = useRef<string | null>(null);
  const rafRef = useRef(0);
  const revealRef = useRef<string | null>(null); // a selected node (from a shared link) to bring into view once laid out

  useEffect(() => {
    if (loadKey !== lastKeyRef.current) {
      lastKeyRef.current = loadKey;
      fitNextRef.current = true; // a new root (or direction): fit the view once it is laid out
      revealRef.current = useStore.getState().selPath;
      posRef.current = {};
      setPositions({});
    }
  }, [loadKey]);

  const layoutSig = useMemo(() => `${dir}:${visible.map((n) => n.pathId).join("|")}`, [visible, dir]);
  useEffect(() => {
    if (!model || !visible.length) return;
    let cancelled = false;
    // the node whose opening/closing produced this very tree: it must stay exactly where it is on screen
    const st = useTreeStore.getState();
    const anchorPath = st.anchor && st.anchor.model === model ? st.anchor.pathId : null;
    layoutTree(visible.map((n) => n.pathId), links.map((l) => ({ id: l.id, source: l.source, target: l.target })), dir).then((laidOut) => {
      if (cancelled) return;
      cancelAnimationFrame(rafRef.current);
      const from = posRef.current;
      const first = fitNextRef.current;
      // Keep the clicked node in the same spot on screen: slide the whole new layout so that node's new
      // position equals its current one. That is the same as panning the viewport by its position change,
      // but nodes and viewport can never be a frame out of step, so nothing wobbles.
      let target = laidOut;
      if (!first && anchorPath && from[anchorPath] && laidOut[anchorPath]) {
        const dx = from[anchorPath]!.x - laidOut[anchorPath]!.x;
        const dy = from[anchorPath]!.y - laidOut[anchorPath]!.y;
        target = Object.fromEntries(Object.entries(laidOut).map(([id, p]) => [id, { x: p.x + dx, y: p.y + dy }]));
      }
      const animate = !first && !reducedMotion() && countOnScreen(target) <= MAX_ANIMATED_ON_SCREEN;
      const start: Record<string, XY> = {};
      for (const n of visible) start[n.pathId] = from[n.pathId] ?? (n.parent ? (from[n.parent] ?? target[n.parent]) : undefined) ?? target[n.pathId]!;

      const commit = (p: Record<string, XY>) => {
        posRef.current = p;
        setPositions(p);
      };

      if (!animate) {
        commit(target);
        if (first) {
          fitNextRef.current = false;
          setTimeout(() => requestAnimationFrame(() => fitToRoot(target)), 0);
        }
        return;
      }
      const t0 = performance.now();
      const step = (now: number) => {
        const t = Math.min(1, (now - t0) / ANIMATION_MS);
        const k = ease(t);
        const cur: Record<string, XY> = {};
        for (const n of visible) {
          const a = start[n.pathId]!;
          const b = target[n.pathId]!;
          cur[n.pathId] = { x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k };
        }
        commit(cur);
        if (t < 1) rafRef.current = requestAnimationFrame(step);
      };
      rafRef.current = requestAnimationFrame(step);
    }).catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutSig]);
  useEffect(() => () => cancelAnimationFrame(rafRef.current), []);

  // ---- open / close, keeping the clicked node where it is on screen
  const toggle = useCallback(
    (pathId: string) => {
      const m = useTreeStore.getState().model;
      if (!m) return;
      if (m.expanded[pathId]) close(pathId);
      else void open(pathId);
    },
    [open, close],
  );

  // ---- what React Flow draws. `data` only changes with the tree or the selection, never per animation frame.
  // Objects that did not change keep their identity, so React Flow only redraws the nodes that really changed.
  const dataCache = useRef(new Map<string, FlowNode["data"]>());
  const dataById = useMemo(() => {
    const out: Record<string, FlowNode["data"]> = {};
    if (!model) return out;
    for (const n of visible) {
      const next: FlowNode["data"] = {
        n,
        selected: n.pathId === selected,
        expanded: !!model.expanded[n.pathId],
        canToggle: canToggle(model, n.pathId),
        busy: !!busy[n.pathId],
        lens: lensRole(lens, n.id),
        dir,
        onToggle: toggle,
      };
      const old = dataCache.current.get(n.pathId);
      const same =
        old && old.n === next.n && old.selected === next.selected && old.expanded === next.expanded &&
        old.canToggle === next.canToggle && old.busy === next.busy && old.lens === next.lens && old.dir === next.dir &&
        old.onToggle === next.onToggle;
      out[n.pathId] = same ? old : next;
    }
    dataCache.current = new Map(Object.entries(out));
    return out;
  }, [model, visible, selected, busy, lens, dir, toggle]);

  const nodeCache = useRef(new Map<string, FlowNode>());

  const nodes: FlowNode[] = useMemo(() => {
    const next = new Map<string, FlowNode>();
    const out = visible
      .filter((n) => positions[n.pathId])
      .map((n) => {
        const pos = positions[n.pathId]!;
        const data = dataById[n.pathId]!;
        const old = nodeCache.current.get(n.pathId);
        const node: FlowNode =
          old && old.data === data && old.position.x === pos.x && old.position.y === pos.y
            ? old
            : { id: n.pathId, type: "fn", position: pos, data, width: NODE_W, height: NODE_H, draggable: false, selectable: false, focusable: false };
        next.set(n.pathId, node);
        return node;
      });
    nodeCache.current = next;
    return out;
  }, [visible, positions, dataById]);

  const edges: FlowEdge[] = useMemo(
    () =>
      links.map((l) => {
        const kind = l.node.edge?.kinds.includes("calls") ? "calls" : "may_call";
        return {
          id: l.id,
          source: l.source,
          target: l.target,
          type: "tree" as const,
          focusable: false,
          selectable: false,
          data: { kind, count: l.node.edge?.count ?? 1, reversed: dir === "up", onPath: onPath.has(l.target) },
        };
      }),
    [links, dir, onPath],
  );

  // ---- mouse
  const onNodeClick: NodeMouseHandler = useCallback(
    (_e, node) => {
      setSel(node.id === ROOT ? null : node.id);
      wrapRef.current?.focus();
    },
    [setSel],
  );
  const onNodeDoubleClick: NodeMouseHandler = useCallback(
    (_e, node) => {
      const n = useTreeStore.getState().model?.nodes[node.id];
      if (n && node.id !== ROOT) drillTo(n.id);
    },
    [drillTo],
  );

  // ---- keyboard
  const reveal = useCallback(
    (pathId: string, always = false) => {
      const p = posRef.current[pathId];
      const box = wrapRef.current?.getBoundingClientRect();
      if (!p || !box) return;
      const vp = rf.getViewport();
      const x = p.x * vp.zoom + vp.x;
      const y = p.y * vp.zoom + vp.y;
      const m = 24;
      const w = NODE_W * vp.zoom;
      const h = NODE_H * vp.zoom;
      if (always) {
        void rf.setCenter(p.x + NODE_W / 2, p.y + NODE_H / 2, { zoom: vp.zoom, duration: reducedMotion() ? 0 : 200 });
        return;
      }
      // Move the view only as far as needed, and only along the axis that needs it
      let nx = vp.x;
      let ny = vp.y;
      if (x < m) nx += m - x;
      else if (x + w > box.width - m) nx -= x + w - (box.width - m);
      if (y < m) ny += m - y;
      else if (y + h > box.height - m) ny -= y + h - (box.height - m);
      if (nx !== vp.x || ny !== vp.y) void rf.setViewport({ x: nx, y: ny, zoom: vp.zoom }, { duration: reducedMotion() ? 0 : 200 });
    },
    [rf],
  );
  useEffect(() => {
    const p = revealRef.current;
    if (p && positions[p]) {
      revealRef.current = null;
      setTimeout(() => requestAnimationFrame(() => reveal(p, true)), 30); // after the first-view fit
    }
  }, [positions, reveal]);

  // "Fit the tree on screen" (the tour asks for this): do it as soon as the new layout is on screen.
  const firstTick = useRef(fitTick);
  useEffect(() => {
    if (fitTick !== firstTick.current) fitPending.current = true;
  }, [fitTick]);
  useEffect(() => {
    if (!fitPending.current || !visible.length || !visible.every((n) => positions[n.pathId])) return;
    fitPending.current = false;
    setTimeout(() => requestAnimationFrame(() => void rf.fitView({ padding: 0.1, maxZoom: 1, duration: 0 })), 80);
  }, [fitTick, positions, visible, rf]);

  // A node picked from outside the canvas (a story link, the code gutter): bring it into view.
  useEffect(() => {
    if (!selPath) return;
    const t = setTimeout(() => reveal(selPath), 60);
    return () => clearTimeout(t);
  }, [selPath, reveal]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape" && useStore.getState().fieldId) {
      e.preventDefault();
      setField(null); // Esc clears the data lens
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey || !model) return;
    const down = dir === "down";
    let move: Move | null = null;
    if (e.key === "ArrowRight") move = down ? "child" : "parent";
    else if (e.key === "ArrowLeft") move = down ? "parent" : "child";
    else if (e.key === "ArrowUp") move = "prev";
    else if (e.key === "ArrowDown") move = "next";
    if (move) {
      e.preventDefault();
      const to = neighbor(model, selected, move);
      if (to) {
        setSel(to === ROOT ? null : to);
        reveal(to);
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (!model.expanded[selected] && canToggle(model, selected)) toggle(selected);
    } else if (e.key === "Backspace") {
      e.preventDefault();
      if (model.expanded[selected]) toggle(selected);
      else {
        const parent = model.nodes[selected]?.parent;
        if (parent) {
          setSel(parent === ROOT ? null : parent);
          reveal(parent);
        }
      }
    } else if (e.key === "f" || e.key === "F") {
      e.preventDefault();
      // a long animated zoom over a big tree would drop frames, so big trees snap
      void rf.fitView({ padding: 0.15, maxZoom: 1, duration: reducedMotion() || visible.length > 100 ? 0 : 200 });
    }
  };

  const sel = model?.nodes[selected];
  return (
    <div className="h-full flex flex-col min-h-0">
      <ProgressBar active={loading || Object.values(busy).some(Boolean)} label="Loading the call tree" />
      {error ? (
        <div className="p-4">
          <ErrorNote error={error} what="the call tree" onRetry={() => repo && rootId && void load({ repo, rootId, direction: dir, includeNoise: filters.noise, includeMayCall: filters.virtual })} />
        </div>
      ) : null}
      <div
        ref={wrapRef}
        tabIndex={0}
        role="application"
        aria-label="Call tree. Arrow keys move along the tree, Enter opens, Backspace closes, F fits the view."
        onKeyDown={onKeyDown}
        className="flex-1 min-h-0 relative outline-none focus-visible:outline-2 focus-visible:outline-ink focus-visible:-outline-offset-2"
        data-testid="tree-canvas"
        data-visible-nodes={visible.length}
      >
        <GraphDefs />
        {lens && <LensCard lens={lens} mode="tree" />}
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodeClick={onNodeClick}
          onNodeDoubleClick={onNodeDoubleClick}
          onlyRenderVisibleElements
          nodesDraggable={false}
          nodesConnectable={false}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          disableKeyboardA11y
          zoomOnDoubleClick={false}
          minZoom={0.1}
          maxZoom={1.5}
          attributionPosition="bottom-left"
        >
          <Background gap={32} size={1} color="var(--rule)" />
          <Controls position="bottom-right" showInteractive={false} />
          <MiniMap
            position="bottom-right"
            style={{ marginBottom: 108, width: 160, height: 100 }}
            pannable
            zoomable
            nodeColor={(n) => classColor((n.data as FlowNode["data"] | undefined)?.n.container)}
            nodeStrokeWidth={0}
          />
        </ReactFlow>
        <p className="sr-only" aria-live="polite">
          {sel ? `Selected ${sel.qualified_name}. ${sel.childCount} ${dir === "down" ? "functions it calls" : "functions that call it"}.` : ""}
        </p>
      </div>
    </div>
  );
}

export function TreeCanvas() {
  return (
    <ReactFlowProvider>
      <Inner />
    </ReactFlowProvider>
  );
}
