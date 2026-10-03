// The guided tour of JSBSim. Each step sets the same URL state you could set by hand and shows one sentence.
//
// The sentences were checked against the JSBSim source (repos/jsbsim) and not just against the graph:
//  - FGFDMExec::Run (src/FGFDMExec.cpp) is called once per simulation step. It advances the clock (IncrTime),
//    runs the script, then loops `Models[i]->Run(holding)` over every model. It is not itself a loop.
//  - That `Models[i]->Run` is a virtual call on FGModel pointers: which override runs is only known at run
//    time. The extractor lists every override as a "may call" edge, so the dashed lines are possible targets.
//  - FGPropagate::Run (src/models/FGPropagate.cpp) integrates rotational and translational rate and position,
//    then updates the location, the transformation matrices and the orientation.
//  - FGPropagate::VehicleState::vLocation is the aircraft's position (an FGLocation); FGPropagate::Run and
//    several of its helpers assign to it.
//  - FGPropagate::GetAltitudeASL is a 4-line helper: radius minus sea-level radius.
import { api } from "../api";
import { queryClient } from "../queryClient";
import { useTreeStore } from "../graph/treeStore";
import { ROOT, type TreeModel } from "../graph/treeModel";
import { useStore } from "../store";
import { QUALIFIED, TOUR_REPO } from "./steps";

async function search(q: string) {
  return queryClient.fetchQuery({ queryKey: ["search", TOUR_REPO, q], queryFn: () => api.search(TOUR_REPO, q, 50), staleTime: 60_000 });
}

async function functionId(qualified: string): Promise<string> {
  const short = qualified.split("::").slice(-2).join("::");
  const hit = (await search(short)).functions.find((f) => f.qualified_name === qualified);
  if (!hit) throw new Error(`The tour needs ${qualified}, which is not in this repo.`);
  return hit.id;
}

async function fieldId(qualified: string): Promise<string> {
  const short = qualified.split("::").slice(-2).join("::");
  const hit = (await search(short)).fields.find((f) => f.qualified_name === qualified);
  if (!hit) throw new Error(`The tour needs ${qualified}, which is not in this repo.`);
  return hit.id;
}

async function waitForTree(rootId: string, ms = 10_000): Promise<TreeModel> {
  const t0 = Date.now();
  for (;;) {
    const s = useTreeStore.getState();
    if (s.model && s.model.nodes[ROOT]?.id === rootId && !s.loading && s.args?.direction === useStore.getState().dir) return s.model;
    if (Date.now() - t0 > ms) throw new Error("The call tree took too long to load.");
    await new Promise((r) => setTimeout(r, 40));
  }
}

function showView(rootId: string, patch: Partial<ReturnType<typeof useStore.getState>> = {}) {
  const { filters } = useStore.getState();
  useStore.setState({
    repo: TOUR_REPO,
    rootId,
    selPath: null,
    dir: "down",
    fieldId: null,
    mode: "calls",
    tab: "explain",
    trail: [],
    drawer: null,
    filters: { ...filters, virtual: true },
    ...patch,
  });
}

/** The shallowest node in the tree for this function. */
function pathOf(model: TreeModel, qualified: string): string | null {
  let best: { path: string; depth: number } | null = null;
  for (const n of Object.values(model.nodes)) {
    const depth = n.pathId.split("/").length;
    if (n.qualified_name === qualified && (!best || depth < best.depth)) best = { path: n.pathId, depth };
  }
  return best?.path ?? null;
}

/** Put the app in the state this step describes. */
export async function runTourStep(step: number): Promise<void> {
  const tree = useTreeStore.getState();
  const run = await functionId(QUALIFIED.run);
  switch (step) {
    case 0:
    case 1: {
      showView(run);
      const model = await waitForTree(run);
      // Close the grandchildren so the root's own fan of calls is compact enough to see all at once
      tree.collapseBelowRoot();
      useStore.getState().requestFit();
      if (step === 0) return;
      // Select a virtual call from the middle of the fan, so its dashed line is drawn bold and the view stays
      // put (the middle ones sit next to the root; selecting one at the far end would scroll the fan away).
      const all = model.children[ROOT] ?? [];
      const middle = all
        .map((p, i) => ({ p, i }))
        .filter(({ p }) => {
          const k = model.nodes[p]?.edge?.kinds ?? [];
          return k.includes("may_call") && !k.includes("calls");
        })
        .sort((a, b) => Math.abs(a.i - all.length / 2) - Math.abs(b.i - all.length / 2))[0]; // nearest the middle of the fan
      if (middle) await tree.selectPath(middle.p);
      return;
    }
    case 2:
    case 3: {
      showView(run);
      const model = await waitForTree(run);
      const prop = pathOf(model, QUALIFIED.propagate);
      if (!prop) throw new Error("FGPropagate::Run is not in this tree.");
      await tree.open(prop); // open it, if it is closed
      await tree.selectPath(prop);
      if (step === 3) useStore.setState({ fieldId: await fieldId(QUALIFIED.position), tab: "data" });
      return;
    }
    case 4:
      showView(await functionId(QUALIFIED.helper), { tab: "impact" });
      return;
  }
}
