import { describe, expect, it } from "vitest"
import { existsSync } from "node:fs"
import { join } from "node:path"
import {
  pathForScreen,
  screenIdFromPathname,
  prototypePath,
  PROTOTYPE_PATH,
} from "../routes"

describe("routes — standalone connectors removed (commit A)", () => {
  it("does not map any ScreenId to the /connectors path", () => {
    // pathForScreen previously returned "/connectors" for screen "connectors".
    // After commit A there is no route entry, so the lookup should return
    // undefined (or a fallback) rather than the deleted /connectors URL.
    // Cast through `as never` so the test compiles even after the ScreenId
    // union narrows; the runtime check is what we care about.
    expect(pathForScreen("connectors" as never)).not.toBe("/connectors")
  })

  it("does not resolve /connectors to any active screen", () => {
    // PATH_TO_SCREEN previously mapped "/connectors" → "connectors".
    // After commit A it should fall through to the default ("chat").
    expect(screenIdFromPathname("/connectors")).toBe("chat")
  })
})

describe("routes — prototypePath generate-intent option", () => {
  it("appends &generate=1 after the prd param when generate intent is set", () => {
    expect(prototypePath(42, { generate: true })).toBe("/prototype?prd=42&generate=1")
  })

  it("appends ?generate=1 on the bare path when there is no prd", () => {
    expect(prototypePath(undefined, { generate: true })).toBe("/prototype?generate=1")
    expect(prototypePath(null, { generate: true })).toBe("/prototype?generate=1")
  })

  it("does NOT append generate when the option is absent or false (default callers)", () => {
    expect(prototypePath(42)).toBe("/prototype?prd=42")
    expect(prototypePath(42, {})).toBe("/prototype?prd=42")
    expect(prototypePath(42, { generate: false })).toBe("/prototype?prd=42")
    expect(prototypePath()).toBe(PROTOTYPE_PATH)
    expect(prototypePath(null, { generate: false })).toBe(PROTOTYPE_PATH)
  })
})

describe("connectors route file (commit A)", () => {
  it("does not exist on disk", () => {
    const file = join(
      process.cwd(),
      "app",
      "(app)",
      "connectors",
      "page.tsx",
    )
    expect(existsSync(file)).toBe(false)
  })
})
