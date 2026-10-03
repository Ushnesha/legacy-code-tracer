import { useState } from "react";
import { TOUR_STEPS } from "../tour/steps";
import { runTourStep } from "../tour/tour";
import { useStore } from "../store";

/** The small card that shows one sentence of the tour. */
export function TourCard() {
  const step = useStore((s) => s.tour);
  const setTour = useStore((s) => s.setTour);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  if (step === null) return null;
  const s = TOUR_STEPS[step]!;
  const last = step === TOUR_STEPS.length - 1;

  const go = async (to: number) => {
    setBusy(true);
    setProblem(null);
    try {
      await runTourStep(to);
      setTour(to);
    } catch (e) {
      setProblem(e instanceof Error ? `${e.message} Try again, or close the tour.` : "That step could not be set up. Try again, or close the tour.");
    } finally {
      setBusy(false);
    }
  };

  const btn = "border border-rule rounded-node bg-paper px-3 py-1 hover:border-ink disabled:opacity-50";
  return (
    <aside
      role="region"
      aria-label="Guided tour"
      data-testid="tour-card"
      className="absolute bottom-10 left-4 z-30 w-[min(360px,calc(100%-2rem))] border border-ink bg-panel p-3 text-sm"
    >
      <p className="text-muted">
        Step {step + 1} of {TOUR_STEPS.length} · {s.title}
      </p>
      <p className="mt-1 text-base" data-testid="tour-text" aria-live="polite">
        {s.text}
      </p>
      {problem && <p className="mt-2 text-writes">{problem}</p>}
      <div className="mt-3 flex items-center gap-2">
        <button type="button" className={btn} disabled={busy || step === 0} onClick={() => void go(step - 1)}>
          Back
        </button>
        {last ? (
          <button type="button" className={btn} onClick={() => setTour(null)} data-testid="tour-finish">
            Finish
          </button>
        ) : (
          <button type="button" className={btn} disabled={busy} onClick={() => void go(step + 1)} data-testid="tour-next">
            Next
          </button>
        )}
        <button type="button" className="ml-auto underline underline-offset-2 hover:no-underline" onClick={() => setTour(null)}>
          Close tour
        </button>
      </div>
    </aside>
  );
}
