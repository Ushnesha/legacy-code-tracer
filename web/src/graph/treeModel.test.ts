import { describe, expect, it } from "vitest";
import type { TreeNode, TreeResponse } from "../api";
import {
  ancestorPaths,
  canToggle,
  collapse,
  fromTree,
  mergeChildren,
  needsFetch,
  neighbor,
  pathToRoot,
  reopen,
  visibleNodes,
} from "./treeModel";

function node(id: string, path: string, children: TreeNode[] = [], extra: Partial<TreeNode> = {}): TreeNode {
  return {
    id, name: id, qualified_name: `ns::${id}`, container: null, file: "x.cpp", line_start: 1, line_end: 2,
    is_virtual: false, is_pure_virtual: false, is_entry_point: false, is_noise: false, noise_reason: null,
    loc: 2, fan_in: 0, fan_out: 0, reach: 0, summary: null, summary_model: null, summary_verified: null,
    fields_written_count: 0, indirect_calls: 0,
    path_id: path, edge: null, cycle: false, has_more: false, child_count: children.length, hidden_count: 0, children,
    ...extra,
  };
}
const resp = (root: TreeNode): TreeResponse => ({
  repo: "r", direction: "down", depth: 2, include_noise: false, include_may_call: true, root, node_count: 0, truncated: false,
});

// a -> b -> d(more), a -> c
const initial = () =>
  fromTree(
    resp(
      node("a", "root", [
        node("b", "root/0", [node("d", "root/0/0", [], { has_more: true, child_count: 3 })], { child_count: 1 }),
        node("c", "root/1"),
      ]),
    ),
  );

describe("fromTree", () => {
  it("keys every node by path_id and opens the ones that came with children", () => {
    const m = initial();
    expect(Object.keys(m.nodes).sort()).toEqual(["root", "root/0", "root/0/0", "root/1"]);
    expect(m.children["root"]).toEqual(["root/0", "root/1"]);
    expect(m.expanded).toEqual({ root: true, "root/0": true });
    expect(visibleNodes(m).map((n) => n.id)).toEqual(["a", "b", "d", "c"]);
  });
});

describe("expanding", () => {
  const sub = resp(node("d", "root", [node("e", "root/0"), node("f", "root/1", [], { has_more: true, child_count: 2 })], { child_count: 2, hidden_count: 5 }));

  it("adds the children under that path_id", () => {
    const m = mergeChildren(initial(), "root/0/0", sub);
    expect(m.children["root/0/0"]).toEqual(["root/0/0/0", "root/0/0/1"]);
    expect(m.nodes["root/0/0/1"]!.hasMore).toBe(true);
    expect(m.nodes["root/0/0"]!.hiddenCount).toBe(5);
    expect(m.nodes["root/0/0"]!.hasMore).toBe(false);
    expect(visibleNodes(m).map((n) => n.pathId)).toContain("root/0/0/0");
  });

  it("does not duplicate children when expanded twice", () => {
    const once = mergeChildren(initial(), "root/0/0", sub);
    const twice = mergeChildren(once, "root/0/0", sub);
    expect(twice.children["root/0/0"]).toEqual(once.children["root/0/0"]);
    expect(Object.keys(twice.nodes)).toEqual(Object.keys(once.nodes));
    expect(visibleNodes(twice)).toHaveLength(visibleNodes(once).length);
  });

  it("knows when a request is needed", () => {
    const m = initial();
    expect(needsFetch(m, "root/0/0")).toBe(true);
    expect(needsFetch(m, "root/1")).toBe(false); // a leaf
    expect(needsFetch(mergeChildren(m, "root/0/0", sub), "root/0/0")).toBe(false);
  });

  it("marks a child as a cycle when its function is already above it", () => {
    const back = resp(node("d", "root", [node("a", "root/0", [], { has_more: true, child_count: 4 })]));
    const m = mergeChildren(initial(), "root/0/0", back); // a -> b -> d -> a
    const again = m.nodes["root/0/0/0"]!;
    expect(again.cycle).toBe(true);
    expect(again.hasMore).toBe(false);
    expect(canToggle(m, "root/0/0/0")).toBe(false);
  });
});

describe("collapsing", () => {
  it("hides the children but keeps their data", () => {
    const m = collapse(initial(), "root/0");
    expect(visibleNodes(m).map((n) => n.id)).toEqual(["a", "b", "c"]);
    expect(m.children["root/0"]).toEqual(["root/0/0"]);
    expect(m.nodes["root/0/0"]).toBeDefined();
  });

  it("opens again without needing a request", () => {
    const m = reopen(collapse(initial(), "root/0"), "root/0");
    expect(visibleNodes(m).map((n) => n.id)).toEqual(["a", "b", "d", "c"]);
    expect(needsFetch(m, "root/0")).toBe(false);
  });

  it("hides every descendant of a collapsed node", () => {
    const m = collapse(initial(), "root");
    expect(visibleNodes(m).map((n) => n.id)).toEqual(["a"]);
  });

  it("changes nothing when the node is already closed", () => {
    const m = collapse(initial(), "root/0");
    expect(collapse(m, "root/0")).toBe(m);
  });
});

describe("paths and keyboard moves", () => {
  it("gives the path from the root", () => {
    expect(pathToRoot(initial(), "root/0/0")).toEqual(["root", "root/0", "root/0/0"]);
    expect(ancestorPaths("root/0/0")).toEqual(["root", "root/0"]);
  });

  it("moves between parent, child and siblings", () => {
    const m = initial();
    expect(neighbor(m, "root", "child")).toBe("root/0");
    expect(neighbor(m, "root/0", "next")).toBe("root/1");
    expect(neighbor(m, "root/1", "next")).toBeNull();
    expect(neighbor(m, "root/1", "prev")).toBe("root/0");
    expect(neighbor(m, "root/0/0", "parent")).toBe("root/0");
    expect(neighbor(collapse(m, "root/0"), "root/0", "child")).toBeNull();
  });
});
