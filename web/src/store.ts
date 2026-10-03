import { create } from "zustand";
import type { Direction } from "./api";

export type TabId = "explain" | "code" | "data" | "impact";
export type Theme = "light" | "dark";
export type Filters = { virtual: boolean; noise: boolean; data: boolean };

/** The part of the state that lives in the URL (?repo=&root=&sel=&dir=&field=). */
export type ViewState = {
  repo: string | null;
  rootId: string | null; // the function whose tree is shown
  selPath: string | null; // path_id of the selected node in that tree (null = the root itself)
  dir: Direction; // down = what it calls, up = who calls it
  fieldId: string | null; // the data item the data lens is showing
  mode: "calls" | "data"; // the call tree, or the data map
};

type UiState = {
  /** roots you drilled through by double-clicking, oldest first: the breadcrumb lets you step back */
  trail: string[];
  /** the guided tour: which step is showing (0-based), or null when it is closed */
  tour: number | null;
  /** bumped to ask the call tree to fit itself on screen once it has been laid out (the tour uses this) */
  fitTick: number;
  filters: Filters;
  theme: Theme;
  tab: TabId;
  drawer: "left" | "right" | null; // only used below 900px
};

type Actions = {
  setRepo: (repo: string | null) => void;
  setRoot: (id: string | null) => void;
  drillTo: (id: string) => void;
  goTrail: (index: number) => void;
  setSel: (path: string | null) => void;
  setDir: (dir: Direction) => void;
  setField: (id: string | null) => void;
  setMode: (m: "calls" | "data") => void;
  setTour: (step: number | null) => void;
  requestFit: () => void;
  toggleFilter: (key: keyof Filters) => void;
  setTheme: (t: Theme) => void;
  setTab: (t: TabId) => void;
  setDrawer: (d: UiState["drawer"]) => void;
};

export type State = ViewState & UiState & Actions;

const THEME_KEY = "aerotrace-theme";

function savedTheme(): Theme {
  try {
    return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

export const EMPTY_VIEW: ViewState = { repo: null, rootId: null, selPath: null, dir: "down", fieldId: null, mode: "calls" };

export function viewFromUrl(search: string): ViewState {
  const p = new URLSearchParams(search);
  const dir = p.get("dir");
  const repo = p.get("repo") || null;
  return {
    repo,
    rootId: repo ? p.get("root") || null : null,
    selPath: repo ? p.get("sel") || null : null,
    dir: dir === "up" ? "up" : "down",
    fieldId: repo ? p.get("field") || null : null,
    mode: p.get("view") === "data" ? "data" : "calls",
  };
}

export function urlFromView(v: ViewState): string {
  const p = new URLSearchParams();
  if (v.repo) p.set("repo", v.repo);
  if (v.repo && v.rootId) p.set("root", v.rootId);
  if (v.repo && v.rootId && v.selPath) p.set("sel", v.selPath);
  if (v.dir !== "down") p.set("dir", v.dir);
  if (v.repo && v.fieldId) p.set("field", v.fieldId);
  if (v.repo && v.mode === "data") p.set("view", "data");
  const s = p.toString();
  return s ? `?${s}` : window.location.pathname;
}

export const useStore = create<State>((set) => ({
  ...viewFromUrl(window.location.search),
  trail: [],
  tour: null,
  fitTick: 0,
  filters: { virtual: true, noise: false, data: true },
  theme: savedTheme(),
  tab: "explain",
  drawer: null,

  setRepo: (repo) => set({ ...EMPTY_VIEW, repo, trail: [] }),
  setRoot: (rootId) => set({ rootId, selPath: null, fieldId: null, drawer: null, trail: [] }),
  drillTo: (id) =>
    set((s) => (s.rootId === id ? s : { trail: s.rootId ? [...s.trail, s.rootId] : s.trail, rootId: id, selPath: null, fieldId: null })),
  goTrail: (i) =>
    set((s) => (s.trail[i] ? { rootId: s.trail[i]!, trail: s.trail.slice(0, i), selPath: null, fieldId: null } : s)),
  setSel: (selPath) => set({ selPath }),
  setDir: (dir) => set({ dir, selPath: null }),
  setField: (fieldId) => set({ fieldId }),
  setMode: (mode) => set({ mode }),
  setTour: (tour) => set({ tour }),
  requestFit: () => set((s) => ({ fitTick: s.fitTick + 1 })),
  toggleFilter: (key) => set((s) => ({ filters: { ...s.filters, [key]: !s.filters[key] } })),
  setTheme: (theme) => {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* private window: the theme just will not be remembered */
    }
    set({ theme });
  },
  setTab: (tab) => set({ tab }),
  setDrawer: (drawer) => set({ drawer }),
}));

export const THEME_STORAGE_KEY = THEME_KEY;
