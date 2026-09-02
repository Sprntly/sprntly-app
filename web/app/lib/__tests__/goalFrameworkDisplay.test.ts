import { describe, expect, it } from "vitest"
import { frameworkDisplayName } from "../goalFrameworkDisplay"

describe("frameworkDisplayName — the reader's word, never the stored value", () => {
  it("cases RICE and MoSCoW correctly regardless of stored casing", () => {
    expect(frameworkDisplayName("rice")).toBe("RICE")
    expect(frameworkDisplayName("RICE")).toBe("RICE")
    expect(frameworkDisplayName("moscow")).toBe("MoSCoW")
    expect(frameworkDisplayName("MOSCOW")).toBe("MoSCoW")
  })

  it("is whitespace-tolerant", () => {
    expect(frameworkDisplayName("  moscow  ")).toBe("MoSCoW")
  })

  it("covers every value the onboarding CHECK constraint allows", () => {
    expect(frameworkDisplayName("wsjf")).toBe("WSJF")
    expect(frameworkDisplayName("kano")).toBe("Kano")
    expect(frameworkDisplayName("volume-severity")).toBe("volume/severity")
    expect(frameworkDisplayName("goal-based")).toBe("a goal-based ranking")
  })

  it("falls back to the raw value for anything unrecognised, rather than blanking it", () => {
    expect(frameworkDisplayName("something-new")).toBe("something-new")
  })

  it("returns empty for empty input", () => {
    expect(frameworkDisplayName("")).toBe("")
  })
})
