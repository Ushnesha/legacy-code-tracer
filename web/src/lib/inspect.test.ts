import { describe, expect, it } from "vitest";
import { buildNameIndex, linkifyStep, marksByLine, plainReason } from "./inspect";
import type { TreeModel } from "../graph/treeModel";

describe("plainReason", () => {
  it("says it in everyday English", () => {
    expect(plainReason("mentions 'ghost', which does not exist in the code graph")).toBe("mentions ghost, which isn't anywhere in this code");
    expect(plainReason("mentions 'Parse', which is not a callee, caller or field this function touches")).toBe(
      "mentions Parse, which isn't connected here",
    );
    expect(plainReason("side_effects names field '_x', but this function only reads it")).toBe("says it changes _x, but the code only reads it");
    expect(plainReason("something new")).toBe("something new");
  });
});

const fn = (id: string, lines: number[]) => ({ id, name: id, qualified_name: `ns::${id}`, container: null, lines }) as never;
const field = (id: string, lines: number[]) => ({ id, name: id, lines }) as never;

describe("marksByLine", () => {
  it("puts calls, writes and reads on the lines the API says", () => {
    const m = marksByLine({
      callees: { calls: [fn("a", [10, 12]), fn("b", [12])], may_call: [fn("a", [10]), fn("c", [14])] },
      fields_written: [field("w", [11])],
      fields_read: [field("r", [11, 13])],
    });
    expect([...m.keys()].sort((x, y) => x - y)).toEqual([10, 11, 12, 13, 14]);
    expect(m.get(12)!.calls.map((c) => c.id)).toEqual(["a", "b"]);
    expect(m.get(10)!.calls).toHaveLength(1); // the same callee on a line is listed once
    expect(m.get(11)!.writes).toHaveLength(1);
    expect(m.get(11)!.reads).toHaveLength(1);
    expect(m.get(14)!.calls[0]!.id).toBe("c");
  });
});

const model = {
  nodes: {
    root: { pathId: "root", name: "Run", container: "FGFDMExec", qualified_name: "JSBSim::FGFDMExec::Run", id: "1" },
    "root/0": { pathId: "root/0", name: "Propagate", container: null, qualified_name: "Propagate", id: "2" },
    "root/0/3": { pathId: "root/0/3", name: "run_step", container: null, qualified_name: "run_step", id: "3" },
    "root/5": { pathId: "root/5", name: "run_step", container: "Other", qualified_name: "Other::run_step", id: "4" },
  },
} as unknown as TreeModel;

describe("linkifyStep", () => {
  const names = buildNameIndex(model);
  it("links names that look like functions and are in the tree, shallowest first", () => {
    const s = linkifyStep("First FGFDMExec::Run calls run_step() and later Other::run_step.", names);
    expect(s.filter((x) => x.pathId).map((x) => [x.text, x.pathId])).toEqual([
      ["FGFDMExec::Run", "root"],
      ["run_step()", "root/5"], // two nodes share this name: the shallower one wins
      ["Other::run_step", "root/5"],
    ]);
    expect(s.map((x) => x.text).join("")).toBe("First FGFDMExec::Run calls run_step() and later Other::run_step.");
  });
  it("leaves ordinary words alone, even when a function has that name", () => {
    expect(linkifyStep("Then Propagate the state and Run again.", names).every((x) => !x.pathId)).toBe(true);
  });
  it("does not link names that are not in the tree", () => {
    expect(linkifyStep("It calls other_thing().", names).every((x) => !x.pathId)).toBe(true);
  });
});
