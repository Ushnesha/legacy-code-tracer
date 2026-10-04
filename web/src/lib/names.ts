/** Last part of a qualified name: C++ `A::B::f` and Ada `Pkg.Sub.Proc` both give the short name. */
export function lastPart(qualified: string): string {
  const sep = qualified.includes("::") ? "::" : qualified.includes(".") ? "." : null;
  return sep ? qualified.slice(qualified.lastIndexOf(sep) + sep.length) : qualified;
}
