// A stable color for a class: the same name always gets the same one of ten muted colors.
// The colors themselves live in index.css as --class-0 ... --class-9.
export const CLASS_COLOR_COUNT = 10;

export function classColorIndex(name: string | null | undefined): number {
  const s = name ?? "";
  let h = 0x811c9dc5; // FNV-1a
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h % CLASS_COLOR_COUNT;
}

/** CSS color for a class name. Functions with no class share one color. */
export function classColor(name: string | null | undefined): string {
  return `var(--class-${classColorIndex(name)})`;
}
