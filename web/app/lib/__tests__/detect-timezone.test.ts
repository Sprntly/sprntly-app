import { afterEach, describe, expect, it, vi } from "vitest"
import { detectBrowserTimezone } from "../auth"

/** detectBrowserTimezone reads the browser's IANA zone (Intl) and feeds it into
 *  signUp options.data → handle_new_user → profiles.timezone, which drives the
 *  weekly brief's Monday-06:00-local send time. It must degrade to undefined
 *  (→ backend UTC fallback) rather than throw when the runtime can't report one. */
describe("detectBrowserTimezone", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("returns the browser's resolved IANA timezone", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "America/New_York" }),
    } as unknown as Intl.DateTimeFormat)
    expect(detectBrowserTimezone()).toBe("America/New_York")
  })

  it("trims surrounding whitespace", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "  Europe/London  " }),
    } as unknown as Intl.DateTimeFormat)
    expect(detectBrowserTimezone()).toBe("Europe/London")
  })

  it("returns undefined when the runtime reports no zone", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "" }),
    } as unknown as Intl.DateTimeFormat)
    expect(detectBrowserTimezone()).toBeUndefined()
  })

  it("returns undefined (never throws) when Intl blows up", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(() => {
      throw new Error("no Intl")
    })
    expect(detectBrowserTimezone()).toBeUndefined()
  })
})
