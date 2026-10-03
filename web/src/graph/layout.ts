import type { Direction } from "../api";
import type { LayoutRequest, LayoutResponse } from "./layout.worker";
import type { LayoutMode } from "./layoutOptions";

export const NODE_W = 248;
export const NODE_H = 64;

export type XY = { x: number; y: number };

let worker: Worker | null = null;
let seq = 0;
const starts = new Map<number, number>();
const waiting = new Map<number, { ok: (p: Record<string, XY>) => void; fail: (e: Error) => void; nodes: number }>();

/** How long each layout took inside the worker (read by the speed test). */
export type LayoutStat = { nodes: number; ms: number; roundTripMs: number };
declare global {
  interface Window {
    __layoutStats?: LayoutStat[];
  }
}

function getWorker(): Worker {
  if (!worker) {
    worker = new Worker(new URL("./layout.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (e: MessageEvent<LayoutResponse>) => {
      const w = waiting.get(e.data.id);
      if (!w) return;
      waiting.delete(e.data.id);
      if (e.data.positions) {
        (window.__layoutStats ??= []).push({ nodes: w.nodes, ms: e.data.ms ?? 0, roundTripMs: performance.now() - (starts.get(e.data.id) ?? 0) });
        starts.delete(e.data.id);
        w.ok(e.data.positions);
      }
      else w.fail(new Error(e.data.error ?? "layout failed"));
    };
  }
  return worker;
}

export type Edge = { id: string; source: string; target: string };

/** Lay any graph out in a Web Worker, each node with its own size. Resolves with top-left positions by node id. */
export function layoutGraph(
  nodes: { id: string; width: number; height: number }[],
  edges: Edge[],
  direction: Direction,
  mode: LayoutMode = "graph",
): Promise<Record<string, XY>> {
  const id = ++seq;
  return new Promise((ok, fail) => {
    waiting.set(id, { ok, fail, nodes: nodes.length });
    starts.set(id, performance.now());
    const req: LayoutRequest = { id, direction: direction === "up" ? "LEFT" : "RIGHT", mode, nodes, edges };
    getWorker().postMessage(req);
  });
}

/** The call tree: every node is the same size. */
export function layoutTree(nodes: string[], edges: Edge[], direction: Direction): Promise<Record<string, XY>> {
  return layoutGraph(nodes.map((id) => ({ id, width: NODE_W, height: NODE_H })), edges, direction, "tree");
}

/** Start the worker and run one tiny layout now, so the first real one does not pay for loading ELK. */
export function warmUpLayout(): void {
  void layoutGraph(
    [
      { id: "a", width: NODE_W, height: NODE_H },
      { id: "b", width: NODE_W, height: NODE_H },
    ],
    [{ id: "e", source: "a", target: "b" }],
    "down",
    "tree",
  ).then(() => {
    window.__layoutStats = []; // the warm-up does not count
  });
}
