import type { FnBrief } from "../api";
import { classColor } from "../lib/classColor";
import { shortName } from "../lib/util";
import { useStore } from "../store";

/** A function name you can click to make it the new root. */
export function FnLink({ f, note }: { f: Pick<FnBrief, "id" | "name" | "container"> & { is_noise?: boolean }; note?: string }) {
  const setRoot = useStore((s) => s.setRoot);
  return (
    <button
      type="button"
      onClick={() => setRoot(f.id)}
      className="text-left inline-flex items-baseline gap-2 hover:underline underline-offset-2"
    >
      <span aria-hidden="true" className="inline-block size-2 rounded-node shrink-0" style={{ background: classColor(f.container) }} />
      <span className={`mono text-sm break-all ${f.is_noise ? "text-muted" : ""}`}>{shortName(f)}</span>
      {note && <span className="text-xs text-muted">{note}</span>}
    </button>
  );
}
