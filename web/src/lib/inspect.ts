// Small pure helpers for the inspector: kept free of React so they can be unit-tested.
import type { FieldUse, FnWithEdge, FunctionDetail } from "../api";
import type { TreeModel } from "../graph/treeModel";

/** Turn the checker's technical reason into everyday English. */
export function plainReason(reason: string): string {
  let m = /^mentions '(.+)', which does not exist in the code graph$/.exec(reason);
  if (m) return `mentions ${m[1]}, which isn't anywhere in this code`;
  m = /^mentions '(.+)', which is not a callee, caller or field this function touches$/.exec(reason);
  if (m) return `mentions ${m[1]}, which isn't connected here`;
  m = /^side_effects names field '(.+)', but this function only reads it$/.exec(reason);
  if (m) return `says it changes ${m[1]}, but the code only reads it`;
  m = /^side_effects names field '(.+)', but this function does not write it$/.exec(reason);
  if (m) return `says it changes ${m[1]}, but the code never writes it`;
  if (reason.startsWith("the answer was not valid JSON")) return "the AI's answer could not be read";
  if (reason.startsWith("no usable answer")) return "the AI gave no usable answer for this function";
  if (reason.includes("the retry could not be read")) return "the second attempt could not be read, so the first answer was kept";
  return reason;
}

// ---------------------------------------------------------------- code gutter

export type CalleeMark = { id: string; name: string; qualified_name: string; container: string | null };
export type LineMarks = { calls: CalleeMark[]; writes: FieldUse[]; reads: FieldUse[] };

/** For each source line: what is called there, which data is changed, which is read. */
export function marksByLine(d: Pick<FunctionDetail, "callees" | "fields_read" | "fields_written">): Map<number, LineMarks> {
  const out = new Map<number, LineMarks>();
  const at = (line: number) => {
    let m = out.get(line);
    if (!m) out.set(line, (m = { calls: [], writes: [], reads: [] }));
    return m;
  };
  const addCall = (c: FnWithEdge) => {
    for (const line of c.lines) {
      const m = at(line);
      if (!m.calls.some((x) => x.id === c.id)) m.calls.push({ id: c.id, name: c.name, qualified_name: c.qualified_name, container: c.container });
    }
  };
  [...d.callees.calls, ...d.callees.may_call].forEach(addCall);
  for (const f of d.fields_written) for (const line of f.lines) at(line).writes.push(f);
  for (const f of d.fields_read) for (const line of f.lines) at(line).reads.push(f);
  return out;
}

// ---------------------------------------------------------------- story links

export type Segment = { text: string; pathId?: string };

/** name -> path id of the shallowest loaded tree node with that name (also Class::name and the full name). */
export function buildNameIndex(model: TreeModel | null): Map<string, string> {
  const idx = new Map<string, { path: string; depth: number }>();
  if (!model) return new Map();
  for (const n of Object.values(model.nodes)) {
    const depth = n.pathId.split("/").length;
    const keys = [n.qualified_name, n.container ? `${n.container}::${n.name}` : n.name, n.name];
    for (const k of keys) {
      const old = idx.get(k);
      if (!old || depth < old.depth) idx.set(k, { path: n.pathId, depth });
    }
  }
  return new Map([...idx].map(([k, v]) => [k, v.path]));
}

const TOKEN = /[A-Za-z_~][A-Za-z0-9_]*(?:::[A-Za-z_~][A-Za-z0-9_]*)*(?:\(\))?/g;

/** A word is only treated as a function name when it looks like one: Class::name, snake_case, camelCase, or name(). */
function looksLikeName(tok: string): boolean {
  return tok.includes("::") || tok.endsWith("()") || tok.includes("_") || /[a-z][A-Z]/.test(tok) || /^[A-Z]{2,}[a-z]/.test(tok);
}

/** Split a story step into plain text and links to nodes that are in the tree. */
export function linkifyStep(text: string, names: Map<string, string>): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  for (const m of text.matchAll(TOKEN)) {
    const raw = m[0];
    const tok = raw.replace(/\(\)$/, "");
    const path = looksLikeName(raw) ? names.get(tok) : undefined;
    if (!path) continue;
    if (m.index! > last) out.push({ text: text.slice(last, m.index) });
    out.push({ text: raw, pathId: path });
    last = m.index! + raw.length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}
