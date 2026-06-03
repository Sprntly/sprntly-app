import { describe, expect, it } from "vitest"
import { existsSync } from "node:fs"
import { join } from "node:path"
import { pathForScreen, screenIdFromPathname } from "../routes"

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
