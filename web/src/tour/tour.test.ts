import { describe, expect, it } from "vitest";
import { TOUR_STEPS } from "./steps";

describe("tour sentences", () => {
  it("there are five steps, each a short card of plain text", () => {
    expect(TOUR_STEPS).toHaveLength(5);
    for (const s of TOUR_STEPS) {
      expect(s.text.length, s.text).toBeLessThan(220);
      expect(s.text.split(/(?<=[.!?])\s+/).length, s.text).toBeLessThanOrEqual(3);
      expect(s.text).not.toMatch(/[A-Z]{3,}\b(?!:)/); // no SHOUTING
      expect(s.text).not.toMatch(/\b(leverage|seamless|powerful|revolutionary|utilize)\b/i);
    }
  });

  it("says only what the JSBSim code says (see the notes at the top of tour.ts)", () => {
    const t = TOUR_STEPS.map((s) => s.text).join(" ");
    expect(t).not.toMatch(/main loop/i); // FGFDMExec::Run is called by the loop, it is not the loop
    expect(t).not.toMatch(/we resolve/i); // we list the versions a virtual call could reach, we do not pick one
    expect(TOUR_STEPS[1]!.text).toMatch(/could reach/);
  });
});
