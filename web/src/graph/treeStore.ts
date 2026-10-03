import { create } from "zustand";
import { api, type Direction } from "../api";
import { queryClient } from "../queryClient";
import { useStore } from "../store";
import {
  ancestorPaths,
  collapse,
  fromTree,
  hasLoadedChildren,
  mergeChildren,
  needsFetch,
  reopen,
  ROOT,
  type TreeModel,
} from "./treeModel";

export type TreeArgs = { repo: string; rootId: string; direction: Direction; includeNoise: boolean; includeMayCall: boolean };

type TreeState = {
  model: TreeModel | null;
  /** which repo/root/direction the model belongs to: when it changes, the canvas fits the view */
  loadKey: string | null;
  args: TreeArgs | null;
  loading: boolean;
  error: unknown;
  busy: Record<string, boolean>;
  /** The node that was just opened or closed, tied to the exact model that change produced, so the
   *  layout for that change (and no other) keeps this node still on screen. */
  anchor: { pathId: string; model: TreeModel } | null;
  load: (a: TreeArgs) => Promise<void>;
  /** Open a node (fetching its children first if needed). Returns false if nothing changed. */
  open: (pathId: string) => Promise<boolean>;
  close: (pathId: string) => boolean;
  /** Close everything except the root, so only its direct children show. */
  collapseBelowRoot: () => void;
  ensurePath: (pathId: string) => Promise<void>;
  /** Select a node by path, opening the nodes above it if they are closed. */
  selectPath: (pathId: string) => Promise<void>;
  /** Select the child of the selected node that is this function (open it first if needed). */
  selectCallee: (fnId: string) => Promise<void>;
  /** Make sure there is a node for this function in the tree. Says what happened. */
  addFunction: (fnId: string) => Promise<"added" | "present" | "unreachable" | "hidden">;
};

let token = 0;
const STALE = 60_000;

const fetchTree = (a: TreeArgs, root: string, depth: number) =>
  queryClient.fetchQuery({
    queryKey: ["tree", a.repo, root, { direction: a.direction, depth, includeNoise: a.includeNoise, includeMayCall: a.includeMayCall }],
    queryFn: ({ signal }) =>
      api.tree(a.repo, root, { direction: a.direction, depth, includeNoise: a.includeNoise, includeMayCall: a.includeMayCall }, signal),
    staleTime: STALE,
  });

export const useTreeStore = create<TreeState>((set, get) => ({
  model: null,
  loadKey: null,
  args: null,
  loading: false,
  error: null,
  busy: {},
  anchor: null,

  async load(a) {
    const my = ++token;
    set({ loading: true, error: null, args: a });
    try {
      const resp = await fetchTree(a, a.rootId, 2);
      if (my !== token) return;
      set({ model: fromTree(resp), loadKey: `${a.repo}|${a.rootId}|${a.direction}`, loading: false, busy: {} });
    } catch (e) {
      if (my !== token) return;
      set({ error: e, loading: false });
    }
  },

  async open(pathId) {
    const { model, args } = get();
    if (!model || !args) return false;
    if (hasLoadedChildren(model, pathId)) {
      const next = reopen(model, pathId);
      if (next === model) return false;
      set({ model: next, anchor: { pathId, model: next } });
      return true;
    }
    if (!needsFetch(model, pathId)) return false;
    const node = model.nodes[pathId]!;
    const startToken = token;
    set((s) => ({ busy: { ...s.busy, [pathId]: true }, error: null }));
    try {
      const resp = await fetchTree(args, node.id, 1);
      if (startToken !== token) return false; // a newer tree was loaded meanwhile
      set((s) => {
        if (!s.model) return {};
        const next = mergeChildren(s.model, pathId, resp);
        return { model: next, anchor: { pathId, model: next } };
      });
      return true;
    } catch (e) {
      set({ error: e });
      return false;
    } finally {
      set((s) => ({ busy: { ...s.busy, [pathId]: false } }));
    }
  },

  close(pathId) {
    const { model } = get();
    if (!model) return false;
    const next = collapse(model, pathId);
    if (next === model) return false;
    set({ model: next, anchor: { pathId, model: next } });
    return true;
  },

  async selectPath(pathId) {
    await get().ensurePath(pathId);
    if (get().model?.nodes[pathId]) useStore.getState().setSel(pathId === ROOT ? null : pathId);
  },

  async selectCallee(fnId) {
    const st = useStore.getState();
    const from = st.selPath && get().model?.nodes[st.selPath] ? st.selPath : ROOT;
    await get().open(from);
    const m = get().model;
    const child = m?.children[from]?.find((c) => m.nodes[c]?.id === fnId);
    if (child) await get().selectPath(child);
    else st.drillTo(fnId); // not in the tree (hidden helper, or cut off by the 40-per-node limit): look at it as a new root
  },

  async addFunction(fnId) {
    const { model, args } = get();
    if (!model || !args) return "unreachable";
    const present = Object.values(model.nodes).some((n) => n.id === fnId);
    if (present) return "present";
    let resp;
    try {
      resp = await queryClient.fetchQuery({
        queryKey: ["path", args.repo, model.nodes[ROOT]!.id, fnId, args.direction],
        queryFn: ({ signal }) => api.path(args.repo, model.nodes[ROOT]!.id, fnId, args.direction, signal),
        staleTime: STALE,
      });
    } catch {
      return "unreachable";
    }
    if (!resp.found) return "unreachable";
    let cur: string = ROOT;
    for (const step of resp.path.slice(1)) {
      await get().open(cur);
      const m = get().model!;
      const next = m.children[cur]?.find((c) => m.nodes[c]?.id === step.id);
      if (!next) return "hidden"; // behind a hidden helper, or cut off by the per-node limit
      cur = next;
    }
    return "added";
  },

  collapseBelowRoot() {
    const { model } = get();
    if (!model) return;
    set({ model: { ...model, expanded: { [ROOT]: true } }, anchor: null });
  },

  /** Open every node on the way to `pathId`, so a shared ?sel= link lands on the right node. */
  async ensurePath(pathId) {
    for (const p of ancestorPaths(pathId)) {
      const m = get().model;
      if (!m?.nodes[p]) return;
      if (!m.expanded[p]) await get().open(p);
    }
  },
}));

export type AddResult = "added" | "present" | "unreachable" | "hidden";

/** The function the inspector should describe: the selected node's, or the root's. */
export function useSelectedFnId(): string | null {
  const rootId = useStore((s) => s.rootId);
  const selPath = useStore((s) => s.selPath);
  const model = useTreeStore((s) => s.model);
  if (!rootId) return null;
  if (model && model.nodes[ROOT]?.id === rootId && selPath && model.nodes[selPath]) return model.nodes[selPath]!.id;
  return rootId;
}
