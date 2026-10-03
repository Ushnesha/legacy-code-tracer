// The client-side map of loaded tree nodes, keyed by path_id, and the pure logic that grows it.
// No React in here, so it can be tested on its own.
import type { Direction, TreeNode, TreeResponse } from "../api";

export type TNode = Omit<TreeNode, "children" | "path_id" | "edge" | "has_more" | "child_count" | "hidden_count"> & {
  pathId: string;
  parent: string | null;
  edge: TreeNode["edge"];
  hasMore: boolean; // the server has children we have not loaded
  childCount: number;
  hiddenCount: number; // children the server left out because of its per-node cap
};

export type TreeModel = {
  rootPath: "root";
  direction: Direction;
  nodes: Record<string, TNode>;
  /** loaded children of each node, in display order */
  children: Record<string, string[]>;
  /** which loaded nodes are currently open */
  expanded: Record<string, boolean>;
};

export const ROOT = "root" as const;

function toTNode(n: TreeNode, pathId: string, parent: string | null): TNode {
  const { children: _c, path_id: _p, has_more, child_count, hidden_count, ...rest } = n;
  void _c;
  void _p;
  return { ...rest, pathId, parent, hasMore: has_more, childCount: child_count, hiddenCount: hidden_count };
}

/** Flatten the server's nested tree. Every node that arrived with children starts open. */
export function fromTree(resp: TreeResponse): TreeModel {
  const model: TreeModel = { rootPath: ROOT, direction: resp.direction as Direction, nodes: {}, children: {}, expanded: {} };
  const walk = (n: TreeNode, pathId: string, parent: string | null) => {
    model.nodes[pathId] = toTNode(n, pathId, parent);
    if (n.children.length) {
      model.children[pathId] = n.children.map((_, i) => `${pathId}/${i}`);
      model.expanded[pathId] = true;
      n.children.forEach((c, i) => walk(c, `${pathId}/${i}`, pathId));
    }
  };
  walk(resp.root, ROOT, null);
  return model;
}

/** Function ids from the root down to (and including) this node. */
export function ancestorFunctionIds(model: TreeModel, pathId: string): Set<string> {
  const ids = new Set<string>();
  for (let p: string | null = pathId; p; p = model.nodes[p]?.parent ?? null) ids.add(model.nodes[p]!.id);
  return ids;
}

export const hasLoadedChildren = (m: TreeModel, pathId: string) => (m.children[pathId]?.length ?? 0) > 0;

/** True when opening this node needs a request: the server has children we have not loaded. */
export function needsFetch(m: TreeModel, pathId: string): boolean {
  const n = m.nodes[pathId];
  return !!n && n.hasMore && !n.cycle && !hasLoadedChildren(m, pathId);
}

/** Does this node get a +N / − button? */
export function canToggle(m: TreeModel, pathId: string): boolean {
  const n = m.nodes[pathId];
  return !!n && !n.cycle && (hasLoadedChildren(m, pathId) || n.childCount > 0);
}

/**
 * Put the children from a depth-1 /tree response under `pathId`.
 * Safe to call twice: children that are already loaded are never duplicated.
 * Children whose function already appears above them are marked as cycles here, because the
 * server only saw the sub-tree, not the whole chain.
 */
export function mergeChildren(model: TreeModel, pathId: string, resp: TreeResponse): TreeModel {
  const parent = model.nodes[pathId];
  if (!parent) return model;
  if (hasLoadedChildren(model, pathId)) return { ...model, expanded: { ...model.expanded, [pathId]: true } };

  const above = ancestorFunctionIds(model, pathId);
  const nodes = { ...model.nodes };
  const ids: string[] = [];
  resp.root.children.forEach((c, i) => {
    const id = `${pathId}/${i}`;
    const t = toTNode(c, id, pathId);
    if (above.has(t.id)) Object.assign(t, { cycle: true, hasMore: false, childCount: 0, hiddenCount: 0 });
    nodes[id] = t;
    ids.push(id);
  });
  nodes[pathId] = { ...parent, hasMore: false, childCount: resp.root.child_count, hiddenCount: resp.root.hidden_count };
  return {
    ...model,
    nodes,
    children: { ...model.children, [pathId]: ids },
    expanded: { ...model.expanded, [pathId]: true },
  };
}

/** Hide a node's children. The data stays, so opening it again needs no request. */
export function collapse(model: TreeModel, pathId: string): TreeModel {
  if (!model.expanded[pathId]) return model;
  return { ...model, expanded: { ...model.expanded, [pathId]: false } };
}

/** Open a node whose children are already loaded. */
export function reopen(model: TreeModel, pathId: string): TreeModel {
  if (!hasLoadedChildren(model, pathId) || model.expanded[pathId]) return model;
  return { ...model, expanded: { ...model.expanded, [pathId]: true } };
}

/** Nodes you can currently see, parents before children, in display order. */
export function visibleNodes(model: TreeModel): TNode[] {
  const out: TNode[] = [];
  const walk = (p: string) => {
    const n = model.nodes[p];
    if (!n) return;
    out.push(n);
    if (model.expanded[p]) for (const c of model.children[p] ?? []) walk(c);
  };
  walk(ROOT);
  return out;
}

export type TreeLink = { id: string; source: string; target: string; node: TNode };

/** One link per visible node except the root: from its parent to it. */
export function visibleLinks(model: TreeModel): TreeLink[] {
  return visibleNodes(model)
    .filter((n) => n.parent)
    .map((n) => ({ id: `e:${n.pathId}`, source: n.parent!, target: n.pathId, node: n }));
}

/** Path ids from the root to this node: used to draw that path darker. */
export function pathToRoot(model: TreeModel, pathId: string): string[] {
  const out: string[] = [];
  for (let p: string | null = pathId; p && model.nodes[p]; p = model.nodes[p]!.parent) out.unshift(p);
  return out;
}

export type Move = "parent" | "child" | "prev" | "next";

/** Where an arrow key goes from here, among the nodes you can see. */
export function neighbor(model: TreeModel, pathId: string, move: Move): string | null {
  const n = model.nodes[pathId];
  if (!n) return null;
  if (move === "parent") return n.parent;
  if (move === "child") return model.expanded[pathId] ? (model.children[pathId]?.[0] ?? null) : null;
  if (!n.parent) return null;
  const sibs = model.children[n.parent] ?? [];
  const i = sibs.indexOf(pathId);
  return sibs[move === "prev" ? i - 1 : i + 1] ?? null;
}

/** Every ancestor path of a path id: "root/3/1" -> ["root", "root/3"]. */
export function ancestorPaths(pathId: string): string[] {
  const parts = pathId.split("/");
  return parts.slice(1).map((_, i) => parts.slice(0, i + 1).join("/"));
}
