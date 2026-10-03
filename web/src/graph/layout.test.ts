import { describe, expect, it } from "vitest";
import ELK from "elkjs/lib/elk.bundled.js";
import { toElkGraph, type LayoutEdge, type LayoutNode } from "./layoutOptions";

const W = 248;
const H = 64;

/** A tree shaped like a call tree: a root with many children, and a few levels below. */
function tree(count: number, seed = 1) {
  let s = seed;
  const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const nodes: LayoutNode[] = [{ id: "root", width: W, height: H }];
  const edges: LayoutEdge[] = [];
  const kids: Record<string, string[]> = {};
  const queue = ["root"];
  while (nodes.length < count && queue.length) {
    const p = queue.shift()!;
    const n = 1 + Math.floor(rnd() * (p === "root" ? 40 : 9));
    for (let i = 0; i < n && nodes.length < count; i++) {
      const id = `${p}/${i}`;
      nodes.push({ id, width: W, height: H });
      edges.push({ id: `e${edges.length}`, source: p, target: id });
      (kids[p] ??= []).push(id);
      queue.push(id);
    }
  }
  return { nodes, edges, kids };
}

async function run(count: number, direction: "RIGHT" | "LEFT" = "RIGHT", seed = 1) {
  const { nodes, edges, kids } = tree(count, seed);
  const elk = new ELK();
  const t0 = performance.now();
  const out = await elk.layout(toElkGraph(nodes, edges, direction, "tree"));
  const ms = performance.now() - t0;
  const pos = Object.fromEntries((out.children ?? []).map((c) => [c.id, c]));
  return { out, pos, kids, ms };
}

describe("tree layout", () => {
  it("lays 300 nodes out in under 500 ms (after the first, warm-up call)", async () => {
    await run(40); // ELK compiles itself on first use
    const times = [];
    for (let i = 0; i < 3; i++) times.push((await run(300)).ms);
    times.sort((a, b) => a - b);
    expect(times[1]).toBeLessThan(500); // the median
  }, 30_000);

  it("keeps every parent's children in the order they were given, top to bottom, for trees of every size and shape", async () => {
    let pairs = 0;
    for (let seed = 1; seed <= 40; seed++) {
      const count = 2 + ((seed * 37) % 330);
      for (const direction of ["RIGHT", "LEFT"] as const) {
        const { pos, kids } = await run(count, direction, seed);
        for (const list of Object.values(kids))
          for (let i = 1; i < list.length; i++) {
            pairs++;
            expect(pos[list[i]!]!.y!, `${count} nodes, seed ${seed}, ${direction}: ${list[i]}`).toBeGreaterThan(pos[list[i - 1]!]!.y!);
          }
      }
    }
    expect(pairs).toBeGreaterThan(1000);
  }, 120_000);

  it("puts no two nodes on top of each other, and children one layer to the side of their parent", async () => {
    const { pos, kids } = await run(300);
    const byLayer = new Map<number, { y: number }[]>();
    for (const c of Object.values(pos)) byLayer.set(c.x!, [...(byLayer.get(c.x!) ?? []), { y: c.y! }]);
    for (const layer of byLayer.values()) {
      layer.sort((a, b) => a.y - b.y);
      for (let i = 1; i < layer.length; i++) expect(layer[i]!.y).toBeGreaterThanOrEqual(layer[i - 1]!.y + H);
    }
    for (const [p, list] of Object.entries(kids)) for (const c of list) expect(pos[c]!.x).toBe(pos[p]!.x! + W + 90);
  }, 30_000);

  it("a LEFT layout puts children to the left", async () => {
    const { pos, kids } = await run(60, "LEFT");
    for (const [p, list] of Object.entries(kids)) for (const c of list) expect(pos[c]!.x).toBeLessThan(pos[p]!.x!);
  });

  it("a parent sits among its children, not above them all", async () => {
    const nodes: LayoutNode[] = [{ id: "root", width: W, height: H }];
    const edges: LayoutEdge[] = [];
    for (let i = 0; i < 7; i++) {
      nodes.push({ id: `root/${i}`, width: W, height: H });
      edges.push({ id: `e${edges.length}`, source: "root", target: `root/${i}` });
      for (let j = 0; j < 2; j++) {
        nodes.push({ id: `root/${i}/${j}`, width: W, height: H });
        edges.push({ id: `e${edges.length}`, source: `root/${i}`, target: `root/${i}/${j}` });
      }
    }
    const out = await new ELK().layout(toElkGraph(nodes, edges, "RIGHT", "tree"));
    const pos = Object.fromEntries((out.children ?? []).map((c) => [c.id, c]));
    const middle = (pos["root/0"]!.y! + pos["root/6"]!.y!) / 2;
    expect(Math.abs(pos["root"]!.y! - middle)).toBeLessThan(H * 1.5);
  });
});
