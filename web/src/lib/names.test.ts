import { describe, expect, it } from "vitest";
import { lastPart } from "./names";

describe("lastPart", () => {
  it("handles C++ names", () => expect(lastPart("JSBSim::FGFDMExec::Run")).toBe("Run"));
  it("handles Ada names", () => expect(lastPart("STM32.Device.Enable_Clock")).toBe("Enable_Clock"));
  it("leaves a bare name alone", () => expect(lastPart("main")).toBe("main"));
});
