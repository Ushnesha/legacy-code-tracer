import { useMemo } from "react";
import { useField } from "../queries";
import { useStore } from "../store";

export type LensRole = "write" | "read" | "dim";

/** The data lens: which functions change or read the picked field. Null when no field is picked. */
export function useLens() {
  const repo = useStore((s) => s.repo);
  const fieldId = useStore((s) => s.fieldId);
  const q = useField(repo, fieldId);
  return useMemo(() => {
    if (!fieldId) return null;
    const writers = new Set((q.data?.writers ?? []).map((f) => f.id));
    const readers = new Set((q.data?.readers ?? []).map((f) => f.id));
    return { fieldId, query: q, data: q.data, writers, readers };
  }, [fieldId, q]);
}

export type Lens = NonNullable<ReturnType<typeof useLens>>;

/** What the lens says about one function: red-orange ring, teal ring, or faded. Writing wins over reading. */
export function lensRole(lens: Lens | null, fnId: string): LensRole | null {
  if (!lens || !lens.data) return null;
  if (lens.writers.has(fnId)) return "write";
  if (lens.readers.has(fnId)) return "read";
  return "dim";
}
