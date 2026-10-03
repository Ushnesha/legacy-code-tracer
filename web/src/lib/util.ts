import { useEffect, useState, useSyncExternalStore } from "react";
import type { FnBrief } from "../api";

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

const NARROW = "(max-width: 899px)";
/** True below 900px, where the side panels turn into drawers. */
export function useIsNarrow(): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(NARROW);
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    () => window.matchMedia(NARROW).matches,
    () => false,
  );
}

/** Short display name: Class::name, or just the name for a free function. */
export function shortName(f: Pick<FnBrief, "name" | "container">): string {
  return f.container ? `${f.container}::${f.name}` : f.name;
}

export const shortSha = (sha: string | null | undefined) => (sha ? sha.slice(0, 7) : null);

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}
