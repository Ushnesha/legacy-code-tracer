import { useRepos, useStatus } from "../queries";
import { TOUR_REPO } from "../tour/steps";
import { runTourStep } from "../tour/tour";
import { shortSha } from "../lib/util";
import { Breadcrumb } from "./Breadcrumb";
import { useStore } from "../store";

export function TopBar({ narrow }: { narrow: boolean }) {
  const { repo, rootId, dir, theme, drawer, mode } = useStore();
  const setMode = useStore((s) => s.setMode);
  const setRepo = useStore((s) => s.setRepo);
  const setTheme = useStore((s) => s.setTheme);
  const setDrawer = useStore((s) => s.setDrawer);
  const setDir = useStore((s) => s.setDir);
  const repos = useRepos();
  const status = useStatus();
  const tour = useStore((s) => s.tour);
  const setTour = useStore((s) => s.setTour);
  const hasTour = !!repos.data?.some((r) => r.name === TOUR_REPO);
  const startTour = async () => {
    if (tour !== null) return setTour(null);
    try {
      await runTourStep(0);
      setTour(0);
    } catch {
      setTour(null);
    }
  };
  const info = repos.data?.find((r) => r.name === repo);
  const sha = shortSha(info?.commit_sha);

  const btn =
    "border border-rule rounded-node px-3 py-1 bg-panel hover:border-ink aria-pressed:border-ink aria-pressed:font-semibold";

  return (
    <header className="flex items-center gap-4 px-4 h-12 border-b border-rule bg-panel shrink-0">
      <span className="font-semibold tracking-tight">aerotrace</span>

      <label className="flex items-center gap-2">
        <span className="sr-only">Repo</span>
        <select
          aria-label="Repo"
          value={repo ?? ""}
          onChange={(e) => setRepo(e.target.value || null)}
          className="border border-rule rounded-node px-2 py-1 bg-panel max-w-48"
        >
          <option value="">Pick a repo…</option>
          {repos.data?.map((r) => (
            <option key={r.name} value={r.name}>
              {r.name}
            </option>
          ))}
          {repo && !info && <option value={repo}>{repo}</option>}
        </select>
      </label>

      {sha && (
        <span className="text-muted text-sm" title={info?.commit_sha ?? undefined}>
          commit <span className="mono">{sha}</span>
        </span>
      )}

      <Breadcrumb />

      {repo && (
        <div role="radiogroup" aria-label="What to draw" className="inline-flex shrink-0">
          {([["calls", "Calls"], ["data", "Data"]] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={mode === value}
              onClick={() => setMode(value)}
              className={`px-3 py-1 border border-rule first:rounded-l-node last:rounded-r-node -ml-px first:ml-0 ${mode === value ? "bg-ink text-paper border-ink" : "bg-panel hover:border-ink"}`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {repo && rootId && mode === "calls" && (
        <div role="radiogroup" aria-label="Which way to look" className="inline-flex shrink-0">
          {([["down", "What it calls"], ["up", "Who calls it"]] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={dir === value}
              onClick={() => setDir(value)}
              className={`px-3 py-1 border border-rule first:rounded-l-node last:rounded-r-node -ml-px first:ml-0 ${dir === value ? "bg-ink text-paper border-ink" : "bg-panel hover:border-ink"}`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {status.data?.offline && (
        <span className="shrink-0 border border-rule rounded-node px-2 py-0.5 text-sm text-muted" title="The AI is not being called. Only explanations saved earlier are shown.">
          Offline: saved explanations only
        </span>
      )}
      {hasTour && (
        <button type="button" className={btn} aria-pressed={tour !== null} onClick={() => void startTour()} data-testid="tour-button">
          Tour
        </button>
      )}

      {narrow && (
        <>
          <button type="button" className={btn} aria-pressed={drawer === "left"} onClick={() => setDrawer(drawer === "left" ? null : "left")}>
            Search
          </button>
          <button type="button" className={btn} aria-pressed={drawer === "right"} onClick={() => setDrawer(drawer === "right" ? null : "right")}>
            Details
          </button>
        </>
      )}
      <button
        type="button"
        className={btn}
        aria-pressed={theme === "dark"}
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      >
        Dark theme
      </button>
    </header>
  );
}
