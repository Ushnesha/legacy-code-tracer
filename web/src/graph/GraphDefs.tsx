/** Arrowheads for the edges. Colors come from the theme variables. */
export function GraphDefs() {
  const arrow = (id: string, color: string) => (
    <marker id={id} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse" markerUnits="userSpaceOnUse">
      <path d="M0,1 L9,5 L0,9 z" style={{ fill: color }} />
    </marker>
  );
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <defs>
        {arrow("aero-arrow-ink", "var(--ink)")}
        {arrow("aero-arrow-may", "var(--may-call)")}
        {arrow("aero-arrow-reads", "var(--reads)")}
        {arrow("aero-arrow-writes", "var(--writes)")}
      </defs>
    </svg>
  );
}
