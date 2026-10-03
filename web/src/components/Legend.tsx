import { useStore, type Filters } from "../store";

function Swatch({ kind }: { kind: "calls" | "may" | "reads" | "writes" | "ai" | "noise" }) {
  const common = { width: 36, height: 12, "aria-hidden": true } as const;
  switch (kind) {
    case "calls":
      return (
        <svg {...common}>
          <line x1="2" y1="6" x2="34" y2="6" stroke="var(--ink)" strokeWidth="2" />
        </svg>
      );
    case "may":
      return (
        <svg {...common}>
          <line x1="2" y1="6" x2="34" y2="6" stroke="var(--may-call)" strokeWidth="2" strokeDasharray="5 3" />
        </svg>
      );
    case "reads":
      return (
        <svg {...common}>
          <line x1="2" y1="6" x2="34" y2="6" stroke="var(--reads)" strokeWidth="2" />
        </svg>
      );
    case "writes":
      return (
        <svg {...common}>
          <line x1="2" y1="6" x2="34" y2="6" stroke="var(--writes)" strokeWidth="3" />
        </svg>
      );
    case "ai":
      return (
        <svg {...common}>
          <text x="2" y="10" fontSize="11" fill="var(--unverified)" fontFamily="var(--font-sans)">
            Abc
          </text>
        </svg>
      );
    case "noise":
      return (
        <svg {...common}>
          <rect x="2" y="2" width="32" height="8" rx="4" fill="none" stroke="var(--muted)" strokeDasharray="2 2" />
        </svg>
      );
  }
}

function Row({ swatch, children }: { swatch: Parameters<typeof Swatch>[0]["kind"]; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 py-1">
      <Swatch kind={swatch} />
      <span>{children}</span>
    </div>
  );
}

function Toggle({ id, label, hint }: { id: keyof Filters; label: string; hint: string }) {
  const on = useStore((s) => s.filters[id]);
  const toggle = useStore((s) => s.toggleFilter);
  return (
    <label className="flex items-start gap-2 py-1 cursor-pointer">
      <input
        type="checkbox"
        checked={on}
        onChange={() => toggle(id)}
        className="mt-1 size-4 accent-[var(--ink)]"
      />
      <span>
        {label}
        <span className="block text-muted text-sm">{hint}</span>
      </span>
    </label>
  );
}

/** The legend doubles as the filters. */
export function Legend() {
  return (
    <section aria-labelledby="legend-title" className="border-t border-rule p-4 text-sm">
      <h2 id="legend-title" className="font-semibold mb-1">
        Key and filters
      </h2>
      <Row swatch="calls">A direct call</Row>
      <Row swatch="may">A call that could land on a subclass</Row>
      <Row swatch="reads">Reads a piece of data</Row>
      <Row swatch="writes">Changes a piece of data</Row>
      <Row swatch="ai">Explanation not yet checked</Row>
      <div className="mt-2 border-t border-rule pt-2">
        <Toggle id="virtual" label="Show virtual calls" hint="Calls that could land on a subclass." />
        <Toggle id="data" label="Show data links" hint="What each function reads and changes." />
        <Toggle id="noise" label="Show helpers & noise" hint="Getters, setters and other small helpers." />
      </div>
    </section>
  );
}
