// ELK settings, shared by the worker and by the speed test that runs in Node.
export type LayoutMode = "tree" | "graph";
export type LayoutNode = { id: string; width: number; height: number };
export type LayoutEdge = { id: string; source: string; target: string };

export const elkOptions = (direction: "RIGHT" | "LEFT", mode: LayoutMode): Record<string, string> => ({
  "elk.algorithm": "layered",
  "elk.direction": direction,
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.spacing.nodeNodeBetweenLayers": "90",
  "elk.spacing.nodeNode": "18",
  "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
  ...(mode === "tree"
    ? {
        // A tree has no crossings to remove. We want the children in the order the server sent them (non-noise
        // first, biggest reach first). ELK's "keep the model order" option does that but makes a 300-node layout
        // about ten times slower. "Interactive" crossing minimisation keeps the order of the y positions we hand
        // in (see toElkGraph): the same result, reliably, in a tenth of the time.
        "elk.layered.crossingMinimization.strategy": "INTERACTIVE",
        "elk.layered.considerModelOrder.strategy": "NONE",
        // centre each parent among its children (the default lines it up with the first or last one)
        "elk.layered.nodePlacement.bk.fixedAlignment": "BALANCED",
      }
    : {}),
});

/**
 * For trees the nodes arrive in depth-first order. Giving each one a starting y in that order tells ELK
 * where each child belongs among its siblings (see elkOptions).
 */
export function toElkGraph(nodes: LayoutNode[], edges: LayoutEdge[], direction: "RIGHT" | "LEFT", mode: LayoutMode) {
  return {
    id: "root",
    layoutOptions: elkOptions(direction, mode),
    children: nodes.map((n, i) => ({
      id: n.id,
      width: n.width,
      height: n.height,
      ...(mode === "tree" ? { x: 0, y: i * (n.height + 18) } : {}),
    })),
    edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  };
}
