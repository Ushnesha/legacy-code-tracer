// ELK runs here, off the main thread, so a big tree never freezes the page.
// elkjs/lib/elk.bundled.js checks `typeof document === "undefined"` to decide it is itself the worker
// script, and then never exports the API. Inside our worker there is no document, so give it a stand-in
// first, then load it. (A static import would run before this line.)
(globalThis as unknown as { document?: object }).document ??= {};
const elkPromise = import("elkjs/lib/elk.bundled.js").then((m) => new m.default());

import { toElkGraph, type LayoutEdge, type LayoutMode, type LayoutNode } from "./layoutOptions";

export type LayoutRequest = {
  id: number;
  direction: "RIGHT" | "LEFT";
  mode: LayoutMode;
  nodes: LayoutNode[];
  edges: LayoutEdge[];
};
export type LayoutResponse = { id: number; positions?: Record<string, { x: number; y: number }>; error?: string; ms?: number };

self.onmessage = async (e: MessageEvent<LayoutRequest>) => {
  const { id, direction, mode, nodes, edges } = e.data;
  try {
    const elk = await elkPromise;
    const t0 = performance.now(); // time the layout itself, not the one-off loading of ELK
    const out = await elk.layout(toElkGraph(nodes, edges, direction, mode));
    const positions: Record<string, { x: number; y: number }> = {};
    for (const c of out.children ?? []) positions[c.id] = { x: c.x ?? 0, y: c.y ?? 0 };
    (self as unknown as Worker).postMessage({ id, positions, ms: performance.now() - t0 } satisfies LayoutResponse);
  } catch (err) {
    (self as unknown as Worker).postMessage({ id, error: String(err) } satisfies LayoutResponse);
  }
};
