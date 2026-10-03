import type { FieldUse } from "../api";
import { useField, useFunction } from "../queries";
import { useStore } from "../store";
import { ErrorNote } from "./ErrorNote";
import { FnLink } from "./FnLink";
import { ProgressBar } from "./ProgressBar";

function FieldButton({ f, tone }: { f: FieldUse; tone: "reads" | "writes" }) {
  const setField = useStore((s) => s.setField);
  const on = useStore((s) => s.fieldId === f.id);
  const color = tone === "reads" ? "var(--reads)" : "var(--writes)";
  return (
    <button
      type="button"
      onClick={() => setField(on ? null : f.id)}
      aria-pressed={on}
      title={on ? "Turn the data lens off" : "Show who changes and reads this in the call tree"}
      data-testid={`field-${tone}`}
      className="w-full text-left py-1.5 pl-2 pr-1 border-l-4 hover:bg-paper aria-pressed:bg-paper"
      style={{ borderLeftColor: color }}
    >
      <span className="mono text-sm break-all block">{f.name}</span>
      <span className="text-xs text-muted block">
        {f.type}
        {f.container ? ` · in ${f.container}` : ""}
      </span>
    </button>
  );
}

function AboutField({ repo, id }: { repo: string; id: string }) {
  const q = useField(repo, id);
  const setField = useStore((s) => s.setField);
  const d = q.data;
  return (
    <section className="mt-6 border-t border-rule pt-4" aria-label="About this piece of data">
      <ProgressBar active={q.isFetching} label="Loading the data item" />
      {q.isError && <ErrorNote error={q.error} what="this piece of data" onRetry={() => q.refetch()} />}
      {d && (
        <>
          <div className="flex items-start justify-between gap-2">
            <h3 className="mono font-medium break-all">{String(d.field.qualified_name)}</h3>
            <button type="button" className="text-sm underline underline-offset-2 hover:no-underline shrink-0" onClick={() => setField(null)}>
              Turn lens off
            </button>
          </div>
          <p className="text-sm text-muted">{String(d.field.type)}</p>
          <h4 className="font-semibold text-sm mt-3 text-writes">Who changes it?</h4>
          <ul className="space-y-1 mt-1">
            {d.writers.length === 0 && <li className="text-sm text-muted">Nothing changes it.</li>}
            {d.writers.map((w) => (
              <li key={w.id}>
                <FnLink f={w} />
              </li>
            ))}
          </ul>
          <h4 className="font-semibold text-sm mt-3 text-reads">Who reads it?</h4>
          <ul className="space-y-1 mt-1">
            {d.readers.length === 0 && <li className="text-sm text-muted">Nothing reads it.</li>}
            {d.readers.map((w) => (
              <li key={w.id}>
                <FnLink f={w} />
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export function DataTab({ repo, id }: { repo: string; id: string | null }) {
  const q = useFunction(repo, id);
  const fieldId = useStore((s) => s.fieldId);
  const d = q.data;
  return (
    <div>
      <ProgressBar active={q.isFetching} label="Loading the data" />
      <div className="p-4">
        {q.isError && <ErrorNote error={q.error} what="the data this function uses" onRetry={() => q.refetch()} />}
        {d && (
          <>
            <p className="text-sm text-muted mb-3">Click a piece of data to see who else touches it in the call tree.</p>
            <section aria-labelledby="changes-title">
              <h3 id="changes-title" className="font-semibold text-sm text-writes">
                Changes
              </h3>
              <div className="mt-1">
                {d.fields_written.length === 0 && <p className="text-sm text-muted">It doesn't change any data we could find.</p>}
                {d.fields_written.map((f) => (
                  <FieldButton key={f.id} f={f} tone="writes" />
                ))}
              </div>
            </section>
            <section aria-labelledby="reads-title" className="mt-4">
              <h3 id="reads-title" className="font-semibold text-sm text-reads">
                Reads
              </h3>
              <div className="mt-1">
                {d.fields_read.length === 0 && <p className="text-sm text-muted">It doesn't read any data we could find.</p>}
                {d.fields_read.map((f) => (
                  <FieldButton key={f.id} f={f} tone="reads" />
                ))}
              </div>
            </section>
          </>
        )}
        {fieldId && <AboutField repo={repo} id={fieldId} />}
      </div>
    </div>
  );
}
