import { useStore, urlFromView, viewFromUrl, type ViewState } from "./store";

const pick = (s: ViewState): ViewState => ({
  repo: s.repo,
  rootId: s.rootId,
  selPath: s.selPath,
  dir: s.dir,
  fieldId: s.fieldId,
  mode: s.mode,
});

/**
 * Keeps ?repo=&root=&sel=&dir= in step with the store, both ways, so any view can be shared as a link.
 * Picking a repo or a root adds a history entry (Back works); smaller changes replace the current one.
 */
export function startUrlSync(): () => void {
  let applying = false;

  const unsubscribe = useStore.subscribe((state, prev) => {
    if (applying) return;
    const next = pick(state);
    const before = pick(prev);
    const target = urlFromView(next);
    const current = window.location.search || window.location.pathname;
    if (target === current) return;
    const big = next.repo !== before.repo || next.rootId !== before.rootId || next.fieldId !== before.fieldId;
    window.history[big ? "pushState" : "replaceState"](null, "", target);
  });

  const onPop = () => {
    applying = true;
    useStore.setState({ ...viewFromUrl(window.location.search), trail: [] });
    applying = false;
  };
  window.addEventListener("popstate", onPop);

  return () => {
    unsubscribe();
    window.removeEventListener("popstate", onPop);
  };
}
