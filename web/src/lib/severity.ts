// High / medium / low, the same look everywhere: red-orange is where bugs live, amber is caution.
export type Severity = "high" | "medium" | "low";

export const SEVERITY_STYLE: Record<Severity, string> = {
  high: "border-writes text-writes",
  medium: "border-may-call text-may-call",
  low: "border-rule text-muted",
};

export const SEVERITY_WORD: Record<Severity, string> = { high: "High", medium: "Medium", low: "Low" };

/** 0.25 -> "15 min", 1 -> "1 hour", 2.5 -> "2.5 hours" */
export function hours(h: number): string {
  if (h < 1) return `${Math.round(h * 60)} min`;
  return `${h} hour${h === 1 ? "" : "s"}`;
}
