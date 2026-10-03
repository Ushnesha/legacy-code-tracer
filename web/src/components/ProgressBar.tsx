/** A thin bar that shows something is loading. It always takes 2px so the page does not jump. */
export function ProgressBar({ active, label = "Loading" }: { active: boolean; label?: string }) {
  return (
    <div className="progress" role={active ? "progressbar" : undefined} aria-label={active ? label : undefined} aria-busy={active}>
      {active && <span />}
    </div>
  );
}
