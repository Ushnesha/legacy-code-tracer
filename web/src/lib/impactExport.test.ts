import { describe, expect, it } from "vitest";
import { buildReport, toCsv, toHtml, toJson } from "./impactExport";

const impact = {
  root: { id: "r", name: "Run", qualified_name: "A::Run", file: "a.cpp", line_start: 1, line_end: 5 },
  severity: "high",
  severity_reason: "many callers",
  effort: { change: 1, checks: 2, low: 2, high: 5 },
  business_impact: "Users see <odd> values",
  affected: [
    { id: "x", name: "Go", qualified_name: "B::Go", file: "b.cpp", line_start: 10, line_end: 12, distance: 1, severity: "medium", severity_reason: "calls it", effort_hours: 1, how: ["Calls Run"], summary: "Goes" },
  ],
} as never;

const detail = (code: string) =>
  ({
    summary: { short: "Runs it" },
    source: { available: true, code },
    callers: [{ qualified_name: "C::Top" }],
    callees: { calls: [{ qualified_name: "D::Leaf" }], may_call: [] },
    fields_read: [{ qualified_name: "A::x" }],
    fields_written: [],
  }) as never;

const report = buildReport("demo", impact, new Map([["r", detail("int a;")], ["x", detail('say("hi, there");\nend')]]));

describe("impact export", () => {
  it("keeps code and dependencies in JSON", () => {
    const j = JSON.parse(toJson(report));
    expect(j.affected[0].code).toContain("hi, there");
    expect(j.changed.dependencies.calls).toEqual(["D::Leaf"]);
  });
  it("quotes CSV cells that hold commas, quotes and newlines", () => {
    const csv = toCsv(report);
    expect(csv).toContain('"say(""hi, there"");\nend"');
    expect(csv.split("\r\n")[0]).toContain("called_by");
    expect(csv).toContain("D::Leaf");
  });
  it("escapes HTML and includes the code block", () => {
    const h = toHtml(report);
    expect(h).toContain("Users see &lt;odd&gt; values");
    expect(h).toContain("<pre>");
    expect(h).toContain("C::Top");
  });
  it("still lists a function whose code could not be loaded", () => {
    const r = buildReport("demo", impact, new Map());
    expect(r.affected[0]!.code).toBeNull();
    expect(toCsv(r)).toContain("B::Go");
  });
});
